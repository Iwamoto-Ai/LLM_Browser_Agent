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
run_template.py — サイトごとの YAML テンプレート＋数値データから、エージェント用の
指示プロンプトを生成して実行する。

考え方:
  * テンプレート（手順）とデータ（入力する数値）を分離する。
    - 手順 = ログイン / メニュー操作 / どの項目に入れるか … サイトごとに 1 度書く
    - データ = 実際の数値 … 実行ごとに JSON で差し替え
  * 値は {{key}} で参照し、--values の JSON から埋め込む。
    パスワード等の秘密情報は {{SECRET:NAME}} のまま残し、ブラウザ層が実行時に環境変数から補完する
    （プロンプトにもこのスクリプトにも実値は載らない）。

使い方:
  python run_template.py --template templates/example_site.yaml \
                         --values data/example_values.json \
                         --browser edge --no-headless
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

import yaml
from dotenv import load_dotenv

# 注: `from agent import run` は anthropic を要求するため、--dry-run でも動くよう
#     実行直前に遅延インポートする（main 内）。

# {{key}} を data から埋め込む。{{SECRET:NAME}} はコロンを含むためここでは一致せず温存される。
_PLACEHOLDER = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")


def fill(text: str, values: dict) -> str:
    if text is None:
        return ""
    return _PLACEHOLDER.sub(
        lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0),
        str(text),
    )


def build_prompt(tpl: dict, values: dict) -> str:
    name = tpl.get("name", "対象サイト")
    steps: list[str] = []
    n = 1

    login = tpl.get("login")
    if login:
        steps.append(
            f"{n}. ログイン: 『{login['username_field']}』に "
            f"{fill(login['username_value'], values)} を入力し、"
            f"『{login['password_field']}』に {fill(login['password_value'], values)} を入力して、"
            f"{fill(login['submit'], values)}。ログイン完了を確認する。"
        )
        n += 1

    nav = tpl.get("navigation")
    if nav:
        steps.append(f"{n}. 画面遷移: {fill(nav, values).strip()}")
        n += 1

    fields = tpl.get("fields", [])
    if fields:
        body = [f"{n}. 次の各項目に値を 1 つずつ入力する。1 項目ごとに、入力後その欄の現在値が"
                f"指定どおりになっているか確認してから次に進むこと（画面外なら scroll する）:"]
        for f in fields:
            body.append(f"   - {f['description']} に「{fill(f['value'], values)}」を入力")
        steps.append("\n".join(body))
        n += 1

    submit = tpl.get("submit")
    if submit:
        steps.append(f"{n}. {fill(submit, values)}")
        n += 1

    steps.append(
        f"{n}. get_page_state を取得し、上記すべての項目の現在値が指定どおりか最終検証する。"
        f"異なる項目があれば修正して再確認する。"
    )
    n += 1

    verify = tpl.get("verify")
    if verify:
        steps.append(f"{n}. 登録成功の確認: {fill(verify, values)}")
        n += 1

    shot = tpl.get("screenshot", "result.png")
    steps.append(f"{n}. take_screenshot で『{shot}』として、登録完了が分かる画面を保存する。")
    n += 1
    steps.append(f"{n}. finish で、入力した項目と検証結果を要約する。")

    header = (
        f"あなたは『{name}』への入力作業を行うエージェントです。"
        f"以下の手順を順番に、正確に実行してください。値は勝手に変えず、指定された値をそのまま入力します。"
        f"複雑なメニューや多数の項目がある場合は、焦らず get_page_state で状態を確認しながら一つずつ進めてください。\n\n"
        + "\n".join(steps)
    )
    return header


def main() -> None:
    load_dotenv()
    p = argparse.ArgumentParser(description="テンプレートからブラウザ入力タスクを実行")
    p.add_argument("--template", required=True, help="サイトテンプレート (YAML)")
    p.add_argument("--values", default=None, help="入力値 (JSON)。{{key}} に埋め込む")
    p.add_argument("--browser", choices=["edge", "chrome"], default="edge")
    p.add_argument("--engine", choices=["selenium", "playwright"], default="selenium",
                   help="ブラウザ駆動エンジン（既定: selenium）")
    p.add_argument("--backend", choices=["anthropic", "ollama"], default="anthropic",
                   help="頭脳に使う LLM。ollama はローカル・API キー不要")
    p.add_argument("--model", default=None,
                   help="モデル名。未指定なら backend ごとの既定"
                        "（anthropic: claude-sonnet-4-6 / ollama: mistral-nemo）")
    p.add_argument("--max-steps", type=int, default=40, help="最大ステップ数（項目が多いサイトは増やす）")
    p.add_argument("--out-dir", default="output")
    p.add_argument("--dry-run", action="store_true", help="プロンプトを表示するだけで実行しない")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--headless", dest="headless", action="store_true")
    g.add_argument("--no-headless", dest="headless", action="store_false")
    p.set_defaults(headless=True)
    args = p.parse_args()

    with open(args.template, encoding="utf-8") as f:
        tpl = yaml.safe_load(f)
    values = {}
    if args.values:
        with open(args.values, encoding="utf-8") as f:
            values = json.load(f)

    prompt = build_prompt(tpl, values)

    # 未解決の {{key}}（SECRET 以外）が残っていれば、値の指定漏れとして止める
    leftover = [m.group(1) for m in _PLACEHOLDER.finditer(prompt)]
    if leftover:
        sys.exit(f"値が未指定のプレースホルダがあります: {', '.join(sorted(set(leftover)))}"
                 f"\n--values の JSON に追加してください。")

    if args.dry_run:
        print(prompt)
        return

    if args.backend == "ollama":
        from agent_ollama import run  # ローカル LLM。API キー不要
        model = args.model or "qwen3:14b"
    else:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            sys.exit("ANTHROPIC_API_KEY が未設定です（.env か環境変数で設定してください）")
        from agent import run  # ここで初めて anthropic を読み込む
        model = args.model or "claude-sonnet-4-6"

    code = run(prompt, tpl.get("start_url"), model, args.max_steps,
               args.headless, args.out_dir, args.browser, args.engine)
    sys.exit(code)


if __name__ == "__main__":
    main()
