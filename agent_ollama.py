# Copyright 2026 Iwamoto-Ai
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
agent_ollama.py — ローカル LLM (Ollama) でブラウザを操作するエージェント。

agent.py（Anthropic API 版）と同じ browser.py / tools.py を共有し、頭脳だけを
ローカルの Ollama に差し替えたもの。クラウド API キーは一切不要。

ローカルモデル向けの堅牢化:
  * get_page_state など同一ツールの無意味な連続呼び出しを検知して操作を促す
  * tool calling を構造化せずテキストで JSON を返すモデルを救済（テキストから実行）
  * qwen 系などの <think>...</think> 思考タグを除去
  * ツールを使わず本文だけ返したときは即終了せず数回だけ促す

前提:
  * Ollama 本体が起動していること（既定 http://localhost:11434。OLLAMA_HOST で変更可）
  * tool calling 対応モデルを pull 済み（例: ollama pull qwen3:14b / mistral-nemo）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

import ollama

from browser_factory import make_browser
from tools import TOOLS, dispatch

SYSTEM_PROMPT = """あなたはブラウザを操作する自律エージェントです。WebDriver を介して
実際のブラウザを操作します。必ず「ツール」を使って操作を進めてください。

重要な行動ルール:
- navigate・click_element・input_text などの結果には、最新のページ状態（操作可能な
  要素の [番号] 一覧）が自動で付いてきます。したがって get_page_state を繰り返す必要は
  ありません。状態が分かったら、すぐ次の操作（input_text / click_element）を実行します。
- get_page_state を連続で呼ばないこと。状態は直前の結果に必ず含まれています。
- 多くのページは 1 つの画面で完結します。目的の項目が見つからないからといって、別の URL に
  移動したり、やみくもにスクロールして探したりしないこと。まず表示中の要素一覧をよく見ます。
- 指示された項目（例: ユーザーID, パスワード, 交通費）は、要素一覧の各行のラベルと文字列を
  照合し、最も一致する [番号] を選んで操作します。完全一致でなくても部分一致で判断します。
- 操作は必ず要素の [番号] で指定します。座標やセレクタ、URL を推測しないこと。
- 入力欄には input_text、ボタン/リンクには click_element を使います。
- パスワード等の秘密情報は値を書かず {{SECRET:NAME}} 形式のまま渡します。
- 値は勝手に変えず、指示された値をそのまま入力します。
- ツール呼び出しは必ず「関数呼び出し（tool call）」として行い、本文に JSON を書かないこと。
- すべて終わったら take_screenshot で保存し、最後に finish を呼んで要約します。
- 1 ステップにつき、次に必要なツールを 1 つだけ呼んでください。
- 安易に finish で諦めないこと。要素一覧に該当しそうな項目があれば、まず操作を試します。"""

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_TOOL_NAMES = {t["name"] for t in TOOLS}


def _to_ollama_tools(tools: list[dict]) -> list[dict]:
    """Anthropic 形式のツール定義を Ollama / OpenAI 形式に変換する。"""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]


def _attr(obj, key, default=None):
    """dict でも pydantic オブジェクトでも値を取れるヘルパ。"""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _chat(client, model, messages, tools):
    """client.chat のラッパ。環境変数 OLLAMA_THINK で思考モードを制御できる。
    OLLAMA_THINK=0 で思考オフ（qwen3 等の徘徊抑制に有効）。未対応モデルなら自動で外す。"""
    kwargs = dict(model=model, messages=messages, tools=tools)
    tv = os.environ.get("OLLAMA_THINK")
    if tv is not None:
        kwargs["think"] = tv.strip().lower() in ("1", "true", "yes", "on")
        try:
            return client.chat(**kwargs)
        except Exception:
            kwargs.pop("think", None)
    return client.chat(**kwargs)


def _strip_think(text: str) -> str:
    return _THINK_RE.sub("", text or "").strip()


def _iter_json_objects(text: str):
    """テキスト中から波括弧の対応が取れた {...} を順に取り出す（ネスト対応）。"""
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    yield text[start:i + 1]
                    start = None


def _parse_text_toolcall(text: str):
    """本文に紛れたツール呼び出し JSON を救済して (name, args) を返す。無ければ None。
    例: {"name": "input_text", "arguments": {"index": 3, "text": "demo"}}"""
    if not text:
        return None
    for frag in _iter_json_objects(text):
        try:
            obj = json.loads(frag)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        name = obj.get("name")
        if name in _TOOL_NAMES:
            args = obj.get("arguments")
            if args is None:
                args = obj.get("parameters", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            return name, (args or {})
    return None


def run(task: str, start_url: str | None, model: str, max_steps: int,
        headless: bool, out_dir: str, browser_name: str = "edge",
        engine: str = "selenium") -> int:
    host = os.environ.get("OLLAMA_HOST")
    client = ollama.Client(host=host) if host else ollama.Client()
    browser = make_browser(engine, browser_name, headless)
    os.makedirs(out_dir, exist_ok=True)

    user_task = task + (f"\n\n開始 URL: {start_url}" if start_url else "")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_task},
    ]
    tools = _to_ollama_tools(TOOLS)
    exit_code = 1

    last_tool = None          # 直前に実行したツール名（連続検知用）
    repeat = 0                # 同一ツールの連続回数
    no_action_nudges = 0      # ツール未使用が続いた回数
    MAX_NUDGE = 3

    def execute(name, args):
        """1 ツールを実行して結果文字列を返す（finish/連続抑制を含む）。"""
        nonlocal exit_code
        print(f"   ⚙️  {name}({args})")
        if name == "finish":
            print(f"\n✅ 完了: {args.get('summary', '')}")
            exit_code = 0
            return "FINISH", True
        return dispatch(name, args, browser, out_dir), False

    try:
        for step in range(1, max_steps + 1):
            resp = _chat(client, model, messages, tools)
            msg = _attr(resp, "message")
            content = _strip_think(_attr(msg, "content", "") or "")
            tool_calls = _attr(msg, "tool_calls") or []
            messages.append(msg if not isinstance(msg, dict) else dict(msg))

            # 1) 構造化された tool_calls があれば実行
            if tool_calls:
                no_action_nudges = 0
                finished = False
                for tc in tool_calls:
                    fn = _attr(tc, "function")
                    name = _attr(fn, "name")
                    args = _attr(fn, "arguments") or {}
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except Exception:
                            args = {}

                    # 同一ツールの無意味な連続（特に get_page_state）を抑制
                    repeat = repeat + 1 if name == last_tool else 0
                    last_tool = name
                    if name == "get_page_state" and repeat >= 1:
                        print(f"   ⚙️  {name}() … 連続のため抑制")
                        messages.append({
                            "role": "tool", "tool_name": name,
                            "content": "ページ状態は直前の結果に含まれています。"
                                       "get_page_state はもう呼ばないでください。"
                                       "input_text か click_element で次の操作を 1 つ実行してください。",
                        })
                        continue

                    result, finished = execute(name, args)
                    if finished:
                        result = "完了を確認しました。"
                    messages.append({"role": "tool", "tool_name": name, "content": result})
                if finished:
                    break
                continue

            # 2) tool_calls が無い → 本文に紛れたツール呼び出しを救済
            if content:
                print(f"\n🤖 [{step}] {content[:300]}")
            salvaged = _parse_text_toolcall(content)
            if salvaged:
                name, args = salvaged
                repeat = repeat + 1 if name == last_tool else 0
                last_tool = name
                if name == "get_page_state" and repeat >= 1:
                    messages.append({"role": "user",
                                     "content": "状態は取得済みです。input_text か click_element で操作を実行してください。"})
                    continue
                result, finished = execute(name, args)
                messages.append({"role": "user", "content": f"[{name} の実行結果]\n{result}"})
                if finished:
                    break
                no_action_nudges = 0
                continue

            # 3) ツールも JSON も無い → 数回だけ促してから打ち切り
            no_action_nudges += 1
            if no_action_nudges > MAX_NUDGE:
                print("\n（ツールが使われないため終了）")
                exit_code = 0 if last_tool == "finish" else 1
                break
            messages.append({
                "role": "user",
                "content": "手順の説明ではなく、次の操作を必ずツール（input_text / click_element / "
                           "take_screenshot / finish）として 1 つ実行してください。get_page_state は不要です。",
            })
        else:
            print(f"\n⚠️  上限 {max_steps} ステップに到達しました。")
    except KeyboardInterrupt:
        print("\n中断しました。")
    finally:
        browser.quit()
    return exit_code


def main() -> None:
    p = argparse.ArgumentParser(description="LLM Browser Agent（ローカル LLM / Ollama 版）")
    p.add_argument("--task", required=True, help="自然言語のタスク指示")
    p.add_argument("--start-url", default=None, help="開始 URL（任意）")
    p.add_argument("--browser", choices=["edge", "chrome"], default="edge")
    p.add_argument("--engine", choices=["selenium", "playwright"], default="selenium",
                   help="ブラウザ駆動エンジン（既定: selenium）")
    p.add_argument("--model", default="qwen3:14b",
                   help="Ollama モデル名（既定: qwen3:14b。tool calling 対応モデルを推奨）")
    p.add_argument("--max-steps", type=int, default=40)
    p.add_argument("--out-dir", default="output")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--headless", dest="headless", action="store_true")
    g.add_argument("--no-headless", dest="headless", action="store_false")
    p.set_defaults(headless=True)
    args = p.parse_args()

    code = run(args.task, args.start_url, args.model, args.max_steps,
               args.headless, args.out_dir, args.browser, args.engine)
    sys.exit(code)


if __name__ == "__main__":
    main()
