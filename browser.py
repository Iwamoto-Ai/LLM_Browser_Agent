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
browser.py — Selenium WebDriver の薄いラッパー。

設計の要点:
  * ページ上の「操作可能な要素」に連番インデックス (data-claude-idx) を振り、
    Claude にはその一覧をテキストで渡す。Claude はインデックスを指定して操作する
    （座標やセレクタを推測させない = 安定する）。
  * パスワード等のシークレットはモデルに渡さない。テキスト中の {{SECRET:NAME}} を
    このレイヤーで環境変数の値に置換してから入力する。
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    ElementNotInteractableException,
    NoSuchElementException,
    WebDriverException,
)

# 可視で操作可能な要素を収集し data-claude-idx を付与して一覧を返す JS
_COLLECT_JS = r"""
(function () {
  document.querySelectorAll('[data-claude-idx]')
          .forEach(e => e.removeAttribute('data-claude-idx'));
  const sel = 'a, button, input, textarea, select, [role=button], [role=link],' +
              '[role=textbox], [role=checkbox], [role=search], [role=menuitem],' +
              '[contenteditable=true], [onclick], [tabindex]';
  const out = [];
  let i = 0;
  for (const el of document.querySelectorAll(sel)) {
    if (el.type === 'hidden') continue;
    const r = el.getBoundingClientRect();
    const s = window.getComputedStyle(el);
    const visible = r.width > 0 && r.height > 0 &&
                    s.visibility !== 'hidden' && s.display !== 'none' &&
                    s.opacity !== '0';
    if (!visible) continue;
    el.setAttribute('data-claude-idx', i);
    const tag = el.tagName.toLowerCase();
    const isField = tag === 'input' || tag === 'textarea' || tag === 'select' ||
                    el.getAttribute('contenteditable') === 'true';
    let label = (el.getAttribute('aria-label') || el.getAttribute('placeholder') ||
                 el.getAttribute('title') || el.getAttribute('alt') ||
                 el.getAttribute('name') || (isField ? '' : el.innerText) || '').trim();
    label = label.replace(/\s+/g, ' ').slice(0, 100);
    let value = '';
    if (isField) {
      value = (el.value || el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 80);
    }
    out.push({
      idx: i,
      tag: tag,
      type: el.getAttribute('type') || '',
      label: label,
      value: value
    });
    i++;
  }
  return out;
})();
"""

_SECRET_RE = re.compile(r"\{\{SECRET:([A-Z0-9_]+)\}\}")


class Browser:
    def __init__(self, browser: str = "edge", headless: bool = True,
                 window: tuple[int, int] = (1280, 1600)):
        browser = (browser or "edge").lower()
        if browser not in ("edge", "chrome"):
            raise ValueError(f"未対応のブラウザ: {browser}（edge または chrome）")
        self.browser_name = browser

        opts = EdgeOptions() if browser == "edge" else ChromeOptions()
        if headless:
            opts.add_argument("--headless=new")
        opts.add_argument(f"--window-size={window[0]},{window[1]}")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--lang=ja-JP")
        # Linux / WSL でのみ必要なフラグ（Windows ネイティブでは付けない）
        if sys.platform != "win32":
            opts.add_argument("--no-sandbox")
            opts.add_argument("--disable-dev-shm-usage")

        # Selenium 4.6+ の Selenium Manager がドライバ（msedgedriver/chromedriver）を自動取得
        if browser == "edge":
            self.driver = webdriver.Edge(options=opts)
        else:
            self.driver = webdriver.Chrome(options=opts)
        self.driver.set_page_load_timeout(45)

    # ---- 基本操作 -----------------------------------------------------------
    def navigate(self, url: str) -> str:
        if not re.match(r"^https?://", url):
            url = "https://" + url
        self.driver.get(url)
        self._wait_ready()
        return f"{url} に移動しました。"

    def _wait_ready(self, timeout: float = 10.0) -> None:
        end = time.time() + timeout
        while time.time() < end:
            try:
                if self.driver.execute_script("return document.readyState") == "complete":
                    return
            except WebDriverException:
                pass
            time.sleep(0.2)

    def _resolve_secrets(self, text: str) -> str:
        def repl(m: re.Match) -> str:
            val = os.environ.get(m.group(1))
            if val is None:
                raise ValueError(f"環境変数 {m.group(1)} が設定されていません")
            return val
        return _SECRET_RE.sub(repl, text)

    def _find(self, idx: int):
        try:
            return self.driver.find_element(By.CSS_SELECTOR, f"[data-claude-idx='{idx}']")
        except NoSuchElementException:
            raise ValueError(f"インデックス {idx} の要素が見つかりません。"
                             "get_page_state でページ状態を取り直してください。")

    def click(self, idx: int) -> str:
        el = self._find(idx)
        label = el.get_attribute("aria-label") or el.text or f"#{idx}"
        try:
            el.click()
        except (ElementClickInterceptedException, ElementNotInteractableException):
            self.driver.execute_script("arguments[0].click();", el)
        self._wait_ready()
        return f"要素 [{idx}] ({label[:40]}) をクリックしました。"

    def input_text(self, idx: int, text: str, submit: bool = False) -> str:
        el = self._find(idx)
        resolved = self._resolve_secrets(text)
        try:
            el.clear()
        except WebDriverException:
            pass
        el.send_keys(resolved)
        if submit:
            el.send_keys(Keys.RETURN)
            self._wait_ready()
        # ログに残すテキストはシークレットを伏せる（モデルに値は渡らない）
        shown = _SECRET_RE.sub(r"[SECRET:\1]", text)
        return f"要素 [{idx}] に「{shown}」を入力しました{'（Enter送信）' if submit else ''}。"

    def send_keys(self, key: str) -> str:
        mapping = {
            "enter": Keys.RETURN, "return": Keys.RETURN, "tab": Keys.TAB,
            "escape": Keys.ESCAPE, "esc": Keys.ESCAPE, "backspace": Keys.BACKSPACE,
            "pagedown": Keys.PAGE_DOWN, "pageup": Keys.PAGE_UP,
            "arrowdown": Keys.ARROW_DOWN, "arrowup": Keys.ARROW_UP,
        }
        k = mapping.get(key.lower().strip())
        if k is None:
            raise ValueError(f"未対応のキー: {key}")
        webdriver.ActionChains(self.driver).send_keys(k).perform()
        self._wait_ready()
        return f"キー {key} を送信しました。"

    def scroll(self, direction: str = "down", amount: int = 800) -> str:
        dy = amount if direction == "down" else -amount
        self.driver.execute_script(f"window.scrollBy(0, {dy});")
        time.sleep(0.3)
        return f"{direction} に {abs(dy)}px スクロールしました。"

    def screenshot(self, path: str) -> str:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self.driver.save_screenshot(str(p))
        return f"スクリーンショットを保存しました: {p.resolve()}"

    # ---- 状態取得 -----------------------------------------------------------
    def state(self, max_elements: int = 120) -> str:
        elems = self.driver.execute_script(_COLLECT_JS) or []
        lines = []
        for e in elems[:max_elements]:
            t = e["tag"] + (f":{e['type']}" if e["type"] else "")
            label = e["label"] or "(ラベルなし)"
            val = e.get("value") or ""
            suffix = f'  = 現在値:"{val}"' if val else ""
            lines.append(f"[{e['idx']}] <{t}> {label}{suffix}")
        more = "" if len(elems) <= max_elements else f"\n…他 {len(elems) - max_elements} 要素"
        elist = "\n".join(lines) if lines else "(操作可能な要素なし)"
        return (
            f"URL: {self.driver.current_url}\n"
            f"タイトル: {self.driver.title}\n"
            f"--- 操作可能な要素 ---\n{elist}{more}"
        )

    def quit(self) -> None:
        try:
            self.driver.quit()
        except WebDriverException:
            pass
