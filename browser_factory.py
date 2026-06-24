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
browser_factory.py — ブラウザエンジン（selenium / playwright）を選んで生成する。
返すオブジェクトは同一インターフェース（navigate/state/click/input_text/...）なので、
上位コードはエンジンの違いを意識しない。依存は遅延 import（未使用エンジンの依存は不要）。
"""

from __future__ import annotations


def make_browser(engine: str = "selenium", browser: str = "edge", headless: bool = True):
    engine = (engine or "selenium").lower()
    if engine == "playwright":
        from browser_playwright import PlaywrightBrowser
        return PlaywrightBrowser(browser=browser, headless=headless)
    if engine == "selenium":
        from browser import Browser
        return Browser(browser=browser, headless=headless)
    raise ValueError(f"未対応のエンジン: {engine}（selenium または playwright）")
