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
run_recording.py — Chrome Recorder の JSON を「決定論的に」再生する（LLM 不要）。

複雑なメニューや項目が多いサイトでも、人が一度操作して録画した手順をそのまま再生するため、
LLM の判断ゆらぎが無く確実。入力値は {{key}} を --values で差し替えられるので、
同じ録画を別データで何度でも回せる（パスワードは {{SECRET:NAME}} のまま安全に補完）。

使い方:
  python run_recording.py --recording recordings/test_site.example.json --values data/test_values.json --browser edge --no-headless
  # 既定エンジンは playwright（Recorder セレクタとの相性が良い）。--engine selenium も可。
"""

from __future__ import annotations

import argparse
import os
import sys

from browser_factory import make_browser
from recorder_import import load_recording, fill_value, candidates, missing_placeholders

# Recorder の keyDown で扱う特殊キー（send_keys に渡す）
_SPECIAL_KEYS = {"Enter", "Tab", "Escape", "Backspace",
                 "ArrowDown", "ArrowUp", "PageDown", "PageUp"}


def replay(recording: dict, values: dict, browser, out_dir: str,
           screenshot: str = "recording_done.png", max_steps: int = 0,
           full_page: bool = True) -> str:
    steps = recording.get("steps", [])
    if max_steps and max_steps > 0:
        steps = steps[:max_steps]
    total = len(steps)
    for i, step in enumerate(steps, 1):
        t = step.get("type")
        fr = step.get("frame")
        tag = f"[{i}/{total}] {t}" + (f" frame={fr}" if fr else "")
        if t == "setViewport":
            w, h = step.get("width"), step.get("height")
            if w and h and hasattr(browser, "set_viewport"):
                print(f"  {tag} → {w}x{h}")
                try:
                    browser.set_viewport(w, h)
                except Exception:
                    pass
            else:
                print(f"  {tag} … スキップ")
            continue
        if t in ("waitForElement", "waitForExpression", "customStep", "close"):
            print(f"  {tag} … スキップ")
            continue
        if t == "navigate":
            print(f"  {tag} {step.get('url')}")
            browser.navigate(step["url"])
        elif t in ("click", "doubleClick"):
            print(f"  {tag}")
            print("    " + browser.click_selector(candidates(step), frame=fr,
                                                   target=step.get("target")))
        elif t == "change":
            val = fill_value(step.get("value", ""), values)
            print(f"  {tag}")
            print("    " + browser.fill_selector(candidates(step), val, frame=fr,
                                                  target=step.get("target")))
        elif t == "keyDown":
            k = step.get("key")
            if k in _SPECIAL_KEYS:
                print(f"  {tag} {k}")
                browser.send_keys(k)
            else:
                print(f"  {tag} {k} … スキップ（修飾キー/通常キー）")
        elif t == "keyUp":
            continue  # keyDown 側で処理済み
        elif t == "scroll":
            print(f"  {tag}")
            browser.scroll("down", int(step.get("y") or 600))
        else:
            print(f"  {tag} … 未対応のためスキップ")
    saved = browser.screenshot(os.path.join(out_dir, screenshot), full_page=full_page)
    print(f"\n📸 スクリーンショット保存: {saved}")
    return saved


def main() -> None:
    p = argparse.ArgumentParser(description="Chrome Recorder JSON の決定論リプレイ（LLM 不要）")
    p.add_argument("--recording", required=True, help="Recorder からエクスポートした JSON")
    p.add_argument("--values", default=None, help="入力値 JSON（{{key}} に埋め込む）")
    p.add_argument("--engine", choices=["selenium", "playwright"], default="playwright",
                   help="ブラウザ駆動エンジン（既定: playwright。Recorder セレクタと相性が良い）")
    p.add_argument("--browser", choices=["edge", "chrome"], default="edge")
    p.add_argument("--out-dir", default="output")
    p.add_argument("--screenshot", default="recording_done.png", help="保存名（日時が自動付与される）")
    p.add_argument("--max-steps", type=int, default=0,
                   help="先頭から指定ステップ数だけ実行（0=全部）。Logout 等の末尾を止めたいときに使う")
    p.add_argument("--viewport-shot", dest="full_page", action="store_false",
                   help="スクショを表示領域（横長）だけにする。既定はページ全体（縦長）")
    p.set_defaults(full_page=True)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--headless", dest="headless", action="store_true")
    g.add_argument("--no-headless", dest="headless", action="store_false")
    p.set_defaults(headless=False)
    args = p.parse_args()

    recording = load_recording(args.recording)
    values = {}
    if args.values:
        import json
        with open(args.values, encoding="utf-8") as f:
            values = json.load(f)

    miss_target = recording
    if args.max_steps and args.max_steps > 0:
        miss_target = {"steps": recording.get("steps", [])[:args.max_steps]}
    miss = missing_placeholders(miss_target, values)
    if miss:
        sys.exit("値が未指定のプレースホルダがあります: " + ", ".join(miss)
                 + "\n--values の JSON に追加してください。")

    os.makedirs(args.out_dir, exist_ok=True)
    browser = make_browser(args.engine, args.browser, args.headless)
    try:
        replay(recording, values, browser, args.out_dir, args.screenshot,
               args.max_steps, args.full_page)
        print("\n✅ リプレイ完了")
    finally:
        browser.quit()


if __name__ == "__main__":
    main()
