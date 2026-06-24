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
agent.py — Claude がブラウザを操作する AI エージェントの本体。

仕組み:
  1. ユーザーのタスクを Claude に渡す。
  2. Claude が tool use でブラウザ操作（navigate / click / input_text ...）を指示。
  3. ローカルで Selenium が実行し、結果＋最新ページ状態を Claude に返す。
  4. Claude が finish を呼ぶか、上限ステップに達するまで繰り返す。

使い方:
    python agent.py --task "example.com にログインして 'AI' を検索し結果を保存" \
                    --start-url example.com --no-headless
"""

from __future__ import annotations

import argparse
import os
import sys

import anthropic
from dotenv import load_dotenv

from browser_factory import make_browser
from tools import TOOLS, dispatch

SYSTEM_PROMPT = """あなたはブラウザを操作する自律エージェントです。WebDriver を介して
実際の Chrome を操作できます。次の方針で行動してください。

- ページ上の操作可能な要素には [番号] が振られています。操作は必ずこの番号で指定します。
- ページ状態が不明なときは get_page_state で確認してから操作します。
- 入力欄の特定 → input_text、ボタン/リンクは click_element を使います。
- ログイン時、パスワードなどの秘密情報は値を書かず {{SECRET:NAME}} 形式で指定します
  （実際の値はローカルで補完され、あなたには渡りません）。
- 検索は検索欄に input_text(submit=true) するか、検索ボタンを click します。
- 動的に内容が変わるページでは、必要に応じて get_page_state で取り直します。
- タスクが完了したら take_screenshot で結果を保存し、finish で要約を返します。
- 1 ステップにつき必要なツールだけを呼び、結果を見て次を判断します。"""


def run(task: str, start_url: str | None, model: str, max_steps: int,
        headless: bool, out_dir: str, browser_name: str = "edge",
        engine: str = "selenium") -> int:
    client = anthropic.Anthropic()  # ANTHROPIC_API_KEY を環境変数から読む
    browser = make_browser(engine, browser_name, headless)
    os.makedirs(out_dir, exist_ok=True)

    user_task = task
    if start_url:
        user_task += f"\n\n開始 URL: {start_url}"

    messages = [{"role": "user", "content": user_task}]
    exit_code = 1

    try:
        for step in range(1, max_steps + 1):
            resp = client.messages.create(
                model=model,
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
            )
            messages.append({"role": "assistant", "content": resp.content})

            tool_results = []
            finished = False
            for block in resp.content:
                if block.type == "text" and block.text.strip():
                    print(f"\n🤖 [{step}] {block.text.strip()}")
                elif block.type == "tool_use":
                    print(f"   ⚙️  {block.name}({block.input})")
                    result = dispatch(block.name, block.input, browser, out_dir)
                    if block.name == "finish" and result == "FINISH":
                        print(f"\n✅ 完了: {block.input.get('summary', '')}")
                        finished = True
                        result = "完了を確認しました。"
                        exit_code = 0
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

            if finished:
                break
            if not tool_results:
                # ツールを使わずテキストだけ → 終了とみなす
                print("\n（ツール呼び出しがないため終了）")
                exit_code = 0
                break
            messages.append({"role": "user", "content": tool_results})
        else:
            print(f"\n⚠️  上限 {max_steps} ステップに到達しました。")
    except KeyboardInterrupt:
        print("\n中断しました。")
    finally:
        browser.quit()
    return exit_code


def main() -> None:
    load_dotenv()
    p = argparse.ArgumentParser(description="Claude ブラウザ操作エージェント")
    p.add_argument("--task", required=True, help="自然言語のタスク指示")
    p.add_argument("--start-url", default=None, help="開始 URL（任意）")
    p.add_argument("--browser", choices=["edge", "chrome"], default="edge",
                   help="使用ブラウザ（既定: edge）")
    p.add_argument("--engine", choices=["selenium", "playwright"], default="selenium",
                   help="ブラウザ駆動エンジン（既定: selenium）")
    p.add_argument("--model", default="claude-sonnet-4-6",
                   help="使用モデル（既定: claude-sonnet-4-6。難しいタスクは claude-opus-4-8）")
    p.add_argument("--max-steps", type=int, default=25, help="最大ステップ数")
    p.add_argument("--out-dir", default="output", help="スクリーンショット保存先")
    headless = p.add_mutually_exclusive_group()
    headless.add_argument("--headless", dest="headless", action="store_true")
    headless.add_argument("--no-headless", dest="headless", action="store_false")
    p.set_defaults(headless=True)
    args = p.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY が未設定です（.env か環境変数で設定してください）")

    code = run(args.task, args.start_url, args.model, args.max_steps,
               args.headless, args.out_dir, args.browser, args.engine)
    sys.exit(code)


if __name__ == "__main__":
    main()
