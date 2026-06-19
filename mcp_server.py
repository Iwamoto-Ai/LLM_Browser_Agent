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
mcp_server.py — ブラウザ操作を MCP ツールとして公開するサーバー。

Claude Desktop や OpenClaw から、navigate / click / input_text / take_screenshot
などを直接呼んでブラウザを操作できる（API キー不要。ホスト側の Claude が頭脳になる）。

ブラウザは 1 プロセス内で 1 セッションを保持し、ツール呼び出しをまたいで維持する。

環境変数（claude_desktop_config.json の env で指定可能）:
  BROWSER_AGENT_BROWSER  edge | chrome   （既定: edge）
  BROWSER_AGENT_HEADLESS 1 で非表示       （既定: 表示）
  BROWSER_AGENT_OUTPUT   スクショ保存先   （既定: ~/claude_browser_agent_output）
  ログイン用シークレット（MY_PASSWORD など）も env に置けば {{SECRET:NAME}} で参照可
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP, Image

from browser import Browser

mcp = FastMCP("browser-agent")

_browser: Browser | None = None


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _output_dir() -> Path:
    p = Path(os.environ.get("BROWSER_AGENT_OUTPUT")
             or (Path.home() / "claude_browser_agent_output"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _get(create: bool = True) -> Browser:
    global _browser
    if _browser is None and create:
        _browser = Browser(
            browser=os.environ.get("BROWSER_AGENT_BROWSER", "edge"),
            headless=_env_bool("BROWSER_AGENT_HEADLESS", False),
        )
    if _browser is None:
        raise RuntimeError("ブラウザが開かれていません。open_browser を呼んでください。")
    return _browser


@mcp.tool()
def open_browser(browser: str = "edge", headless: bool = False) -> str:
    """ブラウザを起動する。browser は 'edge'（既定）か 'chrome'。
    すでに開いている場合は一度閉じてから開き直す。headless=True で画面非表示。"""
    global _browser
    if _browser is not None:
        _browser.quit()
        _browser = None
    _browser = Browser(browser=browser, headless=headless)
    return f"{browser} を起動しました（headless={headless}）。"


@mcp.tool()
def navigate(url: str) -> str:
    """指定 URL に移動し、移動後のページ状態（操作可能要素の一覧）を返す。"""
    b = _get()
    msg = b.navigate(url)
    return f"{msg}\n\n{b.state()}"


@mcp.tool()
def get_page_state() -> str:
    """現在の URL・タイトルと、操作可能な要素のインデックス一覧を返す。"""
    return _get().state()


@mcp.tool()
def click_element(index: int) -> str:
    """指定インデックスの要素（リンク/ボタン等）をクリックし、最新のページ状態を返す。"""
    b = _get()
    msg = b.click(index)
    return f"{msg}\n\n{b.state()}"


@mcp.tool()
def input_text(index: int, text: str, submit: bool = False) -> str:
    """入力欄にテキストを入力する。パスワード等の秘密情報は値を直接書かず
    {{SECRET:NAME}} 形式で指定する（例: {{SECRET:MY_PASSWORD}}）。実際の値はサーバー側の
    環境変数から補完され、モデルには渡らない。submit=True で入力後 Enter を送る。"""
    b = _get()
    msg = b.input_text(index, text, submit)
    return f"{msg}\n\n{b.state()}"


@mcp.tool()
def send_keys(key: str) -> str:
    """特殊キーを送る（enter, tab, escape, pagedown, pageup, arrowdown, arrowup など）。"""
    b = _get()
    msg = b.send_keys(key)
    return f"{msg}\n\n{b.state()}"


@mcp.tool()
def scroll(direction: str = "down", amount: int = 800) -> str:
    """ページを上下にスクロールする（direction は 'up' か 'down'）。"""
    b = _get()
    msg = b.scroll(direction, amount)
    return f"{msg}\n\n{b.state()}"


@mcp.tool()
def take_screenshot(filename: str = "screenshot.png") -> Image:
    """現在のページのスクリーンショットを保存し、画像をホスト（Claude）にも返す。
    保存先は BROWSER_AGENT_OUTPUT（既定: ~/claude_browser_agent_output）。"""
    b = _get()
    path = str(_output_dir() / filename)
    b.screenshot(path)
    return Image(path=path)


@mcp.tool()
def close_browser() -> str:
    """ブラウザを閉じてセッションを破棄する。"""
    global _browser
    if _browser is not None:
        _browser.quit()
        _browser = None
        return "ブラウザを閉じました。"
    return "ブラウザは開かれていません。"


if __name__ == "__main__":
    mcp.run(transport="stdio")
