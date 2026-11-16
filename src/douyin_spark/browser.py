from __future__ import annotations

import os
import re
import time
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .storage import APP_DIR
from .urls import douyin_search_url

CHAT_URL = "https://www.douyin.com/chat"
BROWSER_PROFILE = APP_DIR / "browser_data"
SHOT_DIR = APP_DIR / "shots"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
LOGIN_MARKERS = ("扫码登录", "验证码登录", "密码登录", "登录后免费畅享", "登录后查看")
SIDEBAR_RATIO = 0.40
HEADER_BAND = 160
FUZZY_FLOOR = 0.75
FUZZY_RIVAL_GAP = 0.08

# Emoji / pictographs / flags / keycaps: douyin nicknames are full of them, but the
# user never types them into our friend list.
_MARK_CHARS = re.compile(
    "["
    "\U0001F000-\U0001FAFF"
    "\u2600-\u27BF"
    "\uFE00-\uFE0F"
    "\u200d\u200b\u200c\u2060\ufeff"
    "\u20e3"
    "]"
)


def normalize_name(value: str) -> str:
    """Fold a nickname down to the part humans actually recognise."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = _MARK_CHARS.sub("", text)
    text = text.replace("…", "").replace("...", "")
    text = re.sub(r"[\s\u3000]+", "", text)
    return text.casefold()


def name_match_score(query: str, candidate: str) -> float:
    """0 when unrelated; 1.0 when identical after folding."""
    left, right = normalize_name(query), normalize_name(candidate)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if right.startswith(left):
        return 0.95 if len(left) >= 2 else 0.85
    if left in right:
        return 0.90 if len(left) >= 2 else 0.80
    if right in left and len(right) >= 3:
        return 0.88  # sidebar truncated the tail we still remember
    ratio = SequenceMatcher(None, left, right).ratio()
    return ratio if ratio >= FUZZY_FLOOR else 0.0


def pick_name_matches(query: str, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Best hit per visual row, strongest first; each row keeps its own score."""
    rows: dict[int, dict[str, Any]] = {}
    for block in blocks:
        text = str(block.get("text") or "")
        score = name_match_score(query, text)
        if score <= 0:
            continue
        try:
            y = float(block.get("y") or 0)
        except (TypeError, ValueError):
            continue
        key = round(y / 14)
        current = rows.get(key)
        if current is None or score > current["score"]:
            rows[key] = dict(block, score=score)
    return sorted(rows.values(), key=lambda hit: (-hit["score"], float(hit.get("y") or 0)))

# User cards on the douyin search page: anchors to /user/ are the stable part,
# the data-e2e selectors are a fallback for list-style layouts.
_USER_CARDS_JS = """
() => {
  const out = [];
  for (const a of document.querySelectorAll('a[href*="/user/"]')) {
    const name = ((a.innerText || a.textContent || '').trim().split('\\n')[0] || '').trim();
    if (name && name.length <= 40) out.push({name: name, url: a.href || ''});
  }
  if (out.length) return out;
  for (const el of document.querySelectorAll('[data-e2e*="user"], li[class*="user"]')) {
    const name = ((el.innerText || '').trim().split('\\n')[0] || '').trim();
    if (name && name.length <= 40) out.push({name: name, url: ''});
  }
  return out;
}
"""

# Text nodes in the left conversation column, leaf-most only, so a container never wins.
_HEADER_JS = """
(name) => {
  const ed = document.querySelector('[contenteditable="true"]');
  const out = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const text = (walker.currentNode.nodeValue || '').trim();
    if (!text || !text.includes(name)) continue;
    const exact = text === name;
    let el = walker.currentNode.parentElement;
    if (!el) continue;
    const rect = el.getBoundingClientRect();
    if (!rect.width || !rect.height) continue;
    let node = el, inPane = false;
    for (let i = 0; i < 14 && node; i += 1) {
      if (ed && node.contains(ed)) { inPane = true; break; }
      node = node.parentElement;
    }
    if (!inPane) continue;
    if (ed) {
      const edRect = ed.getBoundingClientRect();
      if (rect.bottom > edRect.top + 4) continue;
    }
    out.push({x: rect.x, y: rect.y, w: rect.width, h: rect.height, text: text, exact: exact});
  }
  return out;
}
"""


_TEXT_HITS_JS = """
(name) => {
  const W = window.innerWidth, H = window.innerHeight;
  const out = [];
  for (const el of document.querySelectorAll('div,span,a,li,p')) {
    const text = (el.innerText || '').trim();
    if (!text || text.indexOf(name) < 0) continue;
    if (text.length > 160) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 40 || r.height < 14 || r.width > W * 0.8) continue;
    if (r.top < 0 || r.bottom > H || r.left < 0) continue;
    let hasChild = false;
    for (const child of el.children) {
      if (((child.innerText || '').indexOf(name)) >= 0) { hasChild = true; break; }
    }
    if (hasChild) continue;
    out.push({x: r.x, y: r.y, w: r.width, h: r.height, text: text.slice(0, 80)});
  }
  return out;
}
"""

_SIDEBAR_BLOCKS_JS = """
(ratio) => {
  const W = window.innerWidth, H = window.innerHeight;
  const limit = W * (ratio || 0.40);
  const out = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const raw = (walker.currentNode.nodeValue || '').trim();
    if (!raw || raw.length > 60) continue;
    const el = walker.currentNode.parentElement;
    if (!el) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 36 || r.height < 12) continue;
    if (r.left < 0 || r.left > limit || r.top < 0 || r.bottom > H) continue;
    out.push({text: raw, x: r.x, y: r.y, w: r.width, h: r.height});
  }
  return out;
}
"""

_CONTACT_ROW_JS = """
(name) => {
  const out = [];
  const W = window.innerWidth;
  const clean = (value) => (value || '').replace(/\\s+/g, '');
  for (const el of document.querySelectorAll('div,span,button,a')) {
    const text = (el.innerText || '').trim();
    if (text !== '发消息') continue;
    const r = el.getBoundingClientRect();
    if (r.width < 36 || r.height < 16) continue;
    if (r.left < 0 || r.left > W * 0.8) continue;
    let node = el.parentElement, row = null;
    for (let i = 0; i < 6 && node; i += 1) {
      const rowText = clean(node.innerText || '');
      if (rowText.includes(clean(name))) { row = node; break; }
      node = node.parentElement;
    }
    if (!row) continue;
    out.push({x: r.x, y: r.y, w: r.width, h: r.height, text: row.innerText.slice(0, 40)});
  }
  return out;
}
"""

_HEADER_TEXTS_JS = """
() => {
  const ed = document.querySelector('[contenteditable="true"]');
  const out = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const text = (walker.currentNode.nodeValue || '').trim();
    if (!text || text.length > 80) continue;
    const el = walker.currentNode.parentElement;
    if (!el) continue;
    const rect = el.getBoundingClientRect();
    if (!rect.width || !rect.height) continue;
    let node = el, inPane = false;
    for (let i = 0; i < 14 && node; i += 1) {
      if (ed && node.contains(ed)) { inPane = true; break; }
      node = node.parentElement;
    }
    if (!inPane) continue;
    if (ed) {
      const edRect = ed.getBoundingClientRect();
      if (rect.bottom > edRect.top + 4) continue;
    }
    out.push({x: rect.x, y: rect.y, w: rect.width, h: rect.height, text: text});
  }
  return out;
}
"""

_EDITOR_JS = """
() => {
  const nodes = document.querySelectorAll('[contenteditable="true"], textarea');
  let best = null, bestBottom = -1;
  for (const el of nodes) {
    const r = el.getBoundingClientRect();
    if (r.width < 120 || r.height < 18) continue;
    if (r.top < 0 || r.bottom > window.innerHeight + 4) continue;
    if (r.bottom <= bestBottom) continue;
    bestBottom = r.bottom;
    best = el;
  }
  return best;
}
"""

_SEARCH_JS = """
() => {
  const nodes = document.querySelectorAll('input, textarea, [contenteditable="true"]');
  for (const el of nodes) {
    const hint = ((el.getAttribute('placeholder') || '') + (el.getAttribute('aria-label') || '') +
                  (el.getAttribute('data-placeholder') || '')).trim();
    if (!hint) continue;
    if (!/搜索|联系人|好友|查找/.test(hint)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 60 || r.height < 16) continue;
    return el;
  }
  return null;
}
"""

_SEND_BUTTON_JS = """
() => {
  const out = [];
  const ariaNodes = document.querySelectorAll('[aria-label], [title], [data-e2e]');
  for (const el of ariaNodes) {
    const hint = ((el.getAttribute('aria-label') || '') + (el.getAttribute('title') || '') +
                  (el.getAttribute('data-e2e') || '')).toLowerCase();
    if (!/发送|send/.test(hint)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 18 || r.height < 18) continue;
    out.push({x: r.x, y: r.y, w: r.width, h: r.height, src: 'aria', x2: r.right});
  }
  const editor = document.querySelector('[contenteditable="true"]');
  for (const el of document.querySelectorAll('svg, div, button')) {
    const cls = String(el.className && el.className.baseVal !== undefined ? el.className.baseVal : el.className);
    if (!/publishBtn|sendBtn|send_btn|e2e-send-msg/i.test(cls)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 18 || r.height < 18) continue;
    out.push({x: r.x, y: r.y, w: r.width, h: r.height, src: 'class', x2: r.right});
  }
  if (editor) {
    const er = editor.getBoundingClientRect();
    for (const el of document.querySelectorAll('div,a,span,svg')) {
      const r = el.getBoundingClientRect();
      if (r.width < 22 || r.height < 22 || r.width > 90 || r.height > 90) continue;
      if (r.left < er.right - 10) continue;
      if (r.left > er.right + 150) continue;
      if (Math.abs((r.top + r.bottom) / 2 - (er.top + er.bottom) / 2) > 40) continue;
      out.push({x: r.x, y: r.y, w: r.width, h: r.height, src: 'icon', x2: r.right});
    }
  }
  out.sort((a, b) => {
    const ca = a.src === 'class' ? 0 : 1, cb = b.src === 'class' ? 0 : 1;
    if (ca !== cb) return ca - cb;
    if (Math.abs(b.x2 - a.x2) > 2) return b.x2 - a.x2;
    return a.w - b.w;
  });
  return out;
}
"""


_IMAGE_SEND_JS = """
() => {
  const out = [];
  for (const el of document.querySelectorAll('button, div, span, a')) {
    const t = (el.innerText || '').trim();
    if (!/^发送（?\\d*）?$/.test(t)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 80 || r.height < 24) continue;
    let hasTextChild = false;
    for (const child of el.children) {
      if (/^发送/.test((child.innerText || '').trim())) { hasTextChild = true; break; }
    }
    if (hasTextChild) continue;
    out.push({x: r.x, y: r.y, w: r.width, h: r.height, text: t});
  }
  return out;
}
"""


class ChatUnavailable(RuntimeError):
    """Fail-closed error: login, recipient or editor could not be identified."""


@dataclass
class BrowserResult:
    action: str
    event_id: str
    status: str
    detail: str
    message: str = ""


def _is_image(path: str) -> bool:
    return bool(path) and Path(path).suffix.lower() in IMAGE_EXTENSIONS and Path(path).exists()


class ChatSession:
    """Local Edge chat automation. Uses its own browser profile, never another browser's cookies."""

    def __init__(
        self,
        user_data_dir: Path | str = BROWSER_PROFILE,
        *,
        channel: str = "msedge",
        headless: bool = False,
    ) -> None:
        self.user_data_dir = Path(user_data_dir)
        self.channel = channel
        self.headless = headless
        self._playwright: Any = None
        self.context: Any = None
        self.page: Any = None

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self.context is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - depends on local install
            raise ChatUnavailable("未安装 Playwright 运行库，无法自动发送。") from exc
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        SHOT_DIR.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        try:
            self.context = self._playwright.chromium.launch_persistent_context(
                str(self.user_data_dir),
                channel=self.channel,
                headless=self.headless,
                accept_downloads=True,
                locale="zh-CN",
                no_viewport=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--start-maximized",
                ],
            )
        except Exception as exc:
            self.stop()
            raise ChatUnavailable(f"无法启动 Edge 自动化会话：{exc}") from exc
        self.context.set_default_timeout(20_000)
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()

    def stop(self) -> None:
        try:
            if self.context is not None:
                self.context.close()
        except Exception:
            pass
        finally:
            self.context = None
            self.page = None
        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:
            pass
        finally:
            self._playwright = None

    def restart(self) -> None:
        self.stop()
        self.start()

    @property
    def alive(self) -> bool:
        try:
            return self.context is not None and self.page is not None and not self.page.is_closed()
        except Exception:
            return False

    # ------------------------------------------------------------------- page io
    def _body_text(self) -> str:
        try:
            return self.page.evaluate("() => document.body ? document.body.innerText : ''") or ""
        except Exception:
            return ""

    def _wait_ready(self, timeout: float = 28.0) -> str:
        """Poll until the page shows either a login wall or a usable chat surface."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            text = self._body_text()
            if any(marker in text for marker in LOGIN_MARKERS):
                return "needs_login"
            if self._editor() is not None or self._search_input() is not None:
                return "ok"
            try:
                self.page.wait_for_timeout(700)
            except Exception:
                time.sleep(0.7)
        text = self._body_text()
        if any(marker in text for marker in LOGIN_MARKERS):
            return "needs_login"
        return "unknown"

    def _wait_editor(self, timeout: float = 12.0) -> Any:
        """Wait for the message editor, which may only mount after a thread is opened."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            editor = self._editor()
            if editor is not None:
                return editor
            try:
                self.page.wait_for_timeout(600)
            except Exception:
                time.sleep(0.6)
        return None

    def _dump_debug(self, tag: str) -> str:
        """Save a screenshot plus the page text/HTML so selector problems can be diagnosed."""
        stamp = time.strftime("%Y%m%d-%H%M%S")
        base = SHOT_DIR / f"{tag}-{stamp}"
        try:
            SHOT_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        shot, dump = base.with_suffix(".png"), base.with_suffix(".txt")
        try:
            self.page.screenshot(path=str(shot))
        except Exception:
            pass
        try:
            html = self.page.content() or ""
            text = self._body_text()
            body = (
                f"url: {self.page.url}\n"
                f"editor: {self._editor() is not None}\n"
                f"search_box: {self._search_input() is not None}\n"
                f"text:\n{text[:4000]}\n\n"
                f"html:\n{html[:400000]}\n"
            )
            dump.write_text(body, encoding="utf-8")
        except Exception:
            return str(shot)
        return str(shot)

    def logged_in(self) -> bool:
        return self._wait_ready() == "ok"

    def open_chat(self) -> str:
        self.start()
        page = self.page
        page.bring_to_front()
        try:
            page.goto(CHAT_URL, wait_until="domcontentloaded", timeout=45_000)
        except Exception as exc:
            raise ChatUnavailable(f"打开抖音聊天页失败：{exc}") from exc
        host = (urlparse(page.url).hostname or "").lower()
        if host != "douyin.com" and not host.endswith(".douyin.com"):
            raise ChatUnavailable(f"当前页面不是抖音官方域名：{page.url}")
        state = self._wait_ready()
        if state == "unknown":
            try:
                page.reload(wait_until="domcontentloaded", timeout=45_000)
            except Exception:
                pass
            state = self._wait_ready()
        return state

    # ---------------------------------------------------------------- locating
    def lookup_user(self, keyword: str) -> list[dict[str, Any]]:
        """Search douyin.com/users for a 抖音号 and return nickname candidates.

        Best-effort DOM read: Douyin changes its search markup often, so an empty
        list only means "couldn't parse", and the browser is left on the result
        page for the user to read.
        """
        value = keyword.strip()
        if not value:
            raise ChatUnavailable("请先填写抖音号。")
        self.start()
        page = self.page
        page.bring_to_front()
        try:
            page.goto(douyin_search_url(value), wait_until="domcontentloaded", timeout=45_000)
        except Exception as exc:
            raise ChatUnavailable(f"打开抖音搜索页失败：{exc}") from exc
        host = (urlparse(page.url).hostname or "").lower()
        if host != "douyin.com" and not host.endswith(".douyin.com"):
            raise ChatUnavailable(f"搜索页跳到了非抖音域名：{page.url}")
        try:
            page.wait_for_timeout(2600)
            cards = page.evaluate(_USER_CARDS_JS) or []
        except Exception:
            return []
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for card in cards:
            if not isinstance(card, dict):
                continue
            name = str(card.get("name") or "").strip()
            url = str(card.get("url") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            out.append({"name": name[:40], "url": url})
            if len(out) >= 8:
                break
        return out

    def _text_hits(self, name: str) -> list[dict[str, Any]]:
        try:
            hits = self.page.evaluate(_TEXT_HITS_JS, name) or []
        except Exception:
            return []
        return [hit for hit in hits if isinstance(hit, dict) and hit.get("w")]

    @staticmethod
    def _click_hit(page: Any, hit: dict[str, Any]) -> None:
        x = hit["x"] + min(hit["w"] * 0.5, 90)
        y = hit["y"] + hit["h"] * 0.5
        page.mouse.click(x, y)

    def _sidebar_blocks(self, ratio: float = SIDEBAR_RATIO) -> list[dict[str, Any]]:
        try:
            blocks = self.page.evaluate(_SIDEBAR_BLOCKS_JS, ratio) or []
        except Exception:
            return []
        return [block for block in blocks if isinstance(block, dict) and block.get("w")]

    def _sidebar_hits(self, name: str, ratio: float = SIDEBAR_RATIO) -> list[dict[str, Any]]:
        return pick_name_matches(name, self._sidebar_blocks(ratio))

    def _contact_rows(self, name: str) -> list[dict[str, Any]]:
        """Search results sometimes render as 联系人 cards with a 发消息 button."""
        try:
            rows = self.page.evaluate(_CONTACT_ROW_JS, name) or []
        except Exception:
            return []
        unique: dict[int, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict) or not row.get("w"):
                continue
            unique.setdefault(round(float(row.get("y") or 0) / 14), row)
        return list(unique.values())

    def _pane_left(self) -> float:
        """Left edge of the conversation pane, measured from the editor's own box."""
        editor = self._editor()
        if editor is None:
            width = float(self.page.evaluate("() => window.innerWidth") or 0)
            return width * 0.15 if width else 0.0
        try:
            box = editor.bounding_box()
            value = float(box["x"]) if box else 0.0
            return value if value > 0 else 0.0
        except Exception:
            return 0.0

    def _header_hit(self, name: str, pane_left: float = 0.0) -> dict[str, Any] | None:
        """Nickname shown in the thread header: same panel as the editor, above it."""
        try:
            hits = self.page.evaluate(_HEADER_TEXTS_JS) or []
        except Exception:
            hits = []
        best: dict[str, Any] | None = None
        best_score = 0.0
        for hit in hits:
            if not isinstance(hit, dict) or not hit.get("w"):
                continue
            if float(hit.get("y") or 0) > HEADER_BAND:
                continue
            score = name_match_score(name, str(hit.get("text") or ""))
            if score > best_score:
                best, best_score = hit, score
        if best is not None and best_score >= 0.7:
            return best
        return None

    def _editor(self) -> Any:
        try:
            handle = self.page.evaluate_handle(_EDITOR_JS)
            return handle.as_element()
        except Exception:
            return None

    def _editor_text(self, editor: Any) -> str:
        try:
            raw = editor.evaluate("el => el.isContentEditable ? el.innerText : el.value") or ""
        except Exception:
            return ""
        return raw.replace("\u200b", "").replace("\ufeff", "").strip()

    def _message_visible(self, text: str) -> int:
        try:
            return self.page.get_by_text(text, exact=True).count()
        except Exception:
            return 0

    def _search_input(self) -> Any:
        try:
            handle = self.page.evaluate_handle(_SEARCH_JS)
            return handle.as_element()
        except Exception:
            return None

    def _open_search_entry(self) -> None:
        """Open the chat search panel when its input is hidden behind an icon."""
        tries = 0
        for hit in self._text_hits("搜索"):
            if tries >= 3:
                break
            try:
                self._click_hit(self.page, hit)
                self.page.wait_for_timeout(900)
                tries += 1
                if self._search_input() is not None:
                    return
            except Exception:
                continue

    def _pick_thread(self, matches: list[dict[str, Any]], nickname: str) -> dict[str, Any]:
        """Accept a fuzzy hit only when no lookalike row is equally plausible."""
        best = matches[0]
        rivals = [
            hit
            for hit in matches[1:]
            if abs(float(best["score"]) - float(hit["score"])) <= 0.05
        ]
        if rivals:
            labels = "、".join(str(hit.get("text") or "")[:18] for hit in rivals[:3])
            raise ChatUnavailable(
                f"有 {len(rivals) + 1} 个相似会话（{labels}），无法确定是“{nickname}”，为避免发错已停止。"
                "建议把好友名改成长一点、能区分开的那几个字。"
            )
        return best

    def open_conversation(self, nickname: str) -> str:
        """Open exactly one thread named `nickname`, refusing when it is ambiguous."""
        matches = self._sidebar_hits(nickname)
        if matches:
            self._click_hit(self.page, self._pick_thread(matches, nickname))
            self.page.wait_for_timeout(1_200)
            self._wait_editor(8.0)
            return self._confirm_thread(nickname, "list")

        self._open_search_entry()
        search = self._search_input()
        if search is None:
            raise ChatUnavailable("未找到聊天搜索框，无法定位好友会话。")
        search.click(timeout=10_000)
        self._fill_search(search, nickname)
        self.page.wait_for_timeout(1_800)

        # Contact cards in the search overlay are the most direct route: their
        # 发消息 button opens the thread without depending on row coordinates.
        contacts = self._contact_rows(nickname)
        if len(contacts) == 1:
            self._click_hit(self.page, contacts[0])
            self.page.wait_for_timeout(1_200)
            self._wait_editor(8.0)
            return self._confirm_thread(nickname, "contact")

        # The chat search overlay widens the left column to ~58%; the plain
        # sidebar stays under 40%, so only the search route gets the wider cut.
        results = self._sidebar_hits(nickname, ratio=0.62)
        if not results:
            plain = _MARK_CHARS.sub("", unicodedata.normalize("NFKC", nickname)).strip()
            if plain and plain != nickname:
                self._fill_search(search, plain)
                self.page.wait_for_timeout(1_800)
                results = self._sidebar_hits(nickname, ratio=0.62)
        if not results:
            near = sorted(
                self._sidebar_blocks(ratio=0.62),
                key=lambda block: -name_match_score(nickname, str(block.get("text") or "")),
            )[:3]
            labels = "、".join(str(block.get("text") or "")[:18] for block in near if block.get("text"))
            extra = f"左栏最接近的是：{labels}。" if labels else ""
            raise ChatUnavailable(
                f"搜索结果里没有昵称为“{nickname}”的会话。{extra}"
                "可把好友名改成抖音里显示的名字（表情、符号可以省略，保留能区分的几个字）。"
            )
        self._click_hit(self.page, self._pick_thread(results, nickname))
        self.page.wait_for_timeout(1_200)
        self._wait_editor(8.0)
        return self._confirm_thread(nickname, "search")

    def _fill_search(self, search: Any, value: str) -> None:
        try:
            search.fill(value)
        except Exception:
            search.press("Control+A")
            search.type(value, delay=80)

    def _confirm_thread(self, nickname: str, route: str) -> str:
        """Verify the opened thread's header really belongs to the intended friend."""
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            header = self._header_hit(nickname, self._pane_left())
            if header is not None:
                return route
            self.page.wait_for_timeout(600)
        dump = self._dump_debug("header-mismatch")
        raise ChatUnavailable(
            f"打开会话后未在标题栏确认到“{nickname}”，为避免发错已停止。页面快照：{dump}"
        )

    # ----------------------------------------------------------------- sending
    def _attach_image(self, path: str) -> str:
        if not _is_image(path):
            return "skipped"
        page = self.page
        try:
            for index in range(int(page.evaluate("() => document.querySelectorAll('input[type=file]').length") or 0)):
                handle = page.evaluate_handle(
                    "(i) => document.querySelectorAll('input[type=file]')[i]", index
                ).as_element()
                if handle is None:
                    continue
                accept = (handle.get_attribute("accept") or "").lower()
                if accept and "image" not in accept and ".png" not in accept:
                    continue
                handle.set_input_files(path, timeout=15_000)
                page.wait_for_timeout(2_000)
                return "attached" if self._send_image_dialog() else "attached_not_sent"
        except Exception:
            pass
        for hit in self._text_hits("图片") + self._text_hits("相册"):
            try:
                with self.page.expect_file_chooser(timeout=8_000) as chooser:
                    self._click_hit(self.page, hit)
                chooser.value.set_files(path)
                self.page.wait_for_timeout(2_000)
                return "attached" if self._send_image_dialog() else "attached_not_sent"
            except Exception:
                continue
        return "image_input_not_found"

    def _wait_image_done(self, timeout: float = 30.0) -> bool:
        """Wait until the uploaded image finishes sending and no failure marker shows."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                pending = self.page.get_by_text("发送中", exact=False).count()
                failed = self.page.get_by_text("重新发送", exact=False).count() + self.page.get_by_text(
                    "发送失败", exact=False
                ).count()
            except Exception:
                return False
            if failed:
                return False
            if pending == 0:
                return True
            self.page.wait_for_timeout(1_000)
        return False

    def _send_image_dialog(self) -> bool:
        """Confirm the image preview dialog opened by the upload input."""
        self.page.wait_for_timeout(1_200)
        for _ in range(6):
            hits = []
            try:
                hits = self.page.evaluate(_IMAGE_SEND_JS) or []
            except Exception:
                hits = []
            if hits:
                self._click_hit(self.page, hits[-1])
                self.page.wait_for_timeout(1_800)
                return self.page.get_by_text("取消", exact=True).count() == 0
            self.page.wait_for_timeout(700)
        return False

    def _send_button_hits(self) -> list[dict[str, Any]]:
        try:
            return self.page.evaluate(_SEND_BUTTON_JS) or []
        except Exception:
            return []

    def _open_thread(self, nickname: str) -> tuple[str, Any]:
        """Load the chat page and open the friend's thread; returns (route, editor)."""
        if not nickname.strip():
            raise ChatUnavailable("好友昵称为空，未发送。")
        state = self.open_chat()
        if state == "needs_login":
            raise ChatUnavailable("抖音聊天页未登录，请先在自动化浏览器里扫码登录。")
        if state != "ok":
            dump = self._dump_debug("not-ready")
            raise ChatUnavailable(f"抖音聊天页未就绪（未识别到输入框/搜索框），未发送。页面快照：{dump}")
        route = self.open_conversation(nickname)
        editor = self._wait_editor(12.0)
        if editor is None:
            dump = self._dump_debug("no-editor")
            raise ChatUnavailable(f"会话已打开，但未识别到消息输入框，未发送。页面快照：{dump}")
        return route, editor

    def send_text(
        self,
        nickname: str,
        message: str,
        emoji: str = "",
        *,
        should_stop: Callable[[], bool] | None = None,
    ) -> tuple[str, str]:
        """Send one text, one image, or both. Empty text is allowed when sending an image."""
        text = (message or "").strip()
        emoji = (emoji or "").strip()
        image = _is_image(emoji)
        if not text and emoji and not image:
            text = emoji  # a bare emoji can go out as plain text
        if not text and not image:
            raise ChatUnavailable("文案与表情都为空，未发送。")

        route, editor = self._open_thread(nickname)
        if should_stop and should_stop():
            return "cancelled", "发送前已取消。"

        confirmed = False
        before = self._message_visible(text) if text else 0
        if text:
            editor.click(timeout=10_000)
            self.page.keyboard.press("Control+A")
            self.page.keyboard.press("Backspace")
            self.page.keyboard.type(text, delay=40)
            self.page.wait_for_timeout(600)
            typed = self._editor_text(editor)
            if text not in typed:
                try:
                    editor.fill(text)
                    self.page.wait_for_timeout(600)
                    typed = self._editor_text(editor)
                except Exception:
                    pass
            if text not in typed:
                raise ChatUnavailable("文案未能写入输入框，已放弃发送。")
            if should_stop and should_stop():
                return "cancelled", "输入框已填写，但发送前被取消。"

            sent = False
            hits = self._send_button_hits()
            if hits:
                self._click_hit(self.page, hits[0])
                self.page.wait_for_timeout(1_500)
                sent = not self._editor_text(editor)
            if not sent:
                self.page.keyboard.press("Enter")
                self.page.wait_for_timeout(1_500)
                sent = not self._editor_text(editor)
                if not sent and self._message_visible(text) > before:
                    sent = True
                    confirmed = True
            if not sent:
                raise ChatUnavailable("消息未能发出（输入框仍有内容，消息列表也没有新增），已停止。")

        image_state = "skipped"
        if image:
            if should_stop and should_stop():
                return ("sent_no_image", "文字已发送；表情因取消未发送。") if text else (
                    "cancelled",
                    "发送表情前已取消。",
                )
            image_state = self._attach_image(emoji)
            if image_state == "attached":
                image_state = "attached" if self._wait_image_done() else "attached_unconfirmed"

        detail = "会话经搜索进入" if route == "search" else "会话从左栏进入"
        if text:
            if self._message_visible(text) > before:
                detail += "，已在消息列表确认"
            else:
                detail += "，输入框已清空但未在消息列表定位到原文"
        if image_state == "attached":
            detail += "，表情图片已附加并完成上传"
        elif image_state == "attached_unconfirmed":
            detail += "，表情图片已发送但未确认上传完成"
        elif image_state != "skipped":
            detail += f"，表情未发送（{image_state}）"
        if confirmed:
            detail += "，回车后输入框未清空但消息列表已新增"
        if not text:
            if image_state == "attached":
                detail += "，仅发送表情图片"
                return "sent", detail
            if image_state == "attached_unconfirmed":
                return "sent", detail
            return "failed", detail
        return "sent", detail

    def send_image(
        self,
        nickname: str,
        path: str,
        *,
        should_stop: Callable[[], bool] | None = None,
    ) -> tuple[str, str]:
        """Send one image/sticker with no text at all."""
        if not _is_image(path):
            raise ChatUnavailable("表情文件不可用，未发送。")
        route, _editor = self._open_thread(nickname)
        if should_stop and should_stop():
            return "cancelled", "发送表情前已取消。"
        state = self._attach_image(path)
        if state != "attached":
            return "failed", f"表情图片未能发出（{state}）"
        done = self._wait_image_done()
        detail = "会话经搜索进入" if route == "search" else "会话从左栏进入"
        detail += "，仅发送表情图片" + ("并完成上传" if done else "，但未确认上传完成")
        return ("sent" if done else "attached_unconfirmed"), detail

    # -------------------------------------------------------------- diagnostics
    def probe(self) -> dict[str, Any]:
        """Read-only self check: reports what the page exposes without sending anything."""
        report: dict[str, Any] = {"url": "", "login": False, "detail": "", "shot": ""}
        try:
            state = self.open_chat()
            report["state"] = state
            report["url"] = self.page.url
            report["login"] = state == "ok"
            report["search_box"] = self._search_input() is not None
            report["editor"] = self._editor() is not None
            shot = SHOT_DIR / f"probe-{time.strftime('%Y%m%d-%H%M%S')}.png"
            try:
                self.page.screenshot(path=str(shot))
                report["shot"] = str(shot)
            except Exception:
                pass
            if state == "needs_login":
                report["detail"] = "浏览器已打开抖音聊天页，停在扫码登录界面；请扫码登录后再自检。"
            elif state == "unknown":
                report["detail"] = "页面已打开但未识别到聊天界面（可能仍在加载或被风控拦截），请查看截图。"
            elif not report["editor"]:
                report["dump"] = self._dump_debug("probe-no-editor")
                report["detail"] = "已登录，但未识别到消息输入框；已保存页面快照与结构，请把 shots 里的文件发给开发者定位选择器。"
            elif not report["search_box"]:
                report["detail"] = "已登录，输入框可用；未识别到搜索框，将改用左栏会话匹配昵称。"
            else:
                report["detail"] = "自检通过：登录态、搜索框、输入框均可用。"
            return report
        except ChatUnavailable as exc:
            report["detail"] = str(exc)
            return report
        except Exception as exc:  # pragma: no cover - defensive
            report["detail"] = f"自检异常：{exc}"
            return report


def run_selftest() -> dict[str, Any]:
    """Headless smoke test used to verify the packaged exe can drive Edge."""
    report: dict[str, Any] = {"ok": False, "driver": "", "edge": "", "detail": ""}
    try:
        from playwright.sync_api import sync_playwright
        from playwright._impl._driver import compute_driver_executable
    except ImportError as exc:
        report["detail"] = f"Playwright 未打包：{exc}"
        return report
    node, cli = compute_driver_executable()
    report["driver"] = node
    if not Path(node).exists():
        report["detail"] = f"驱动不存在：{node}"
        return report
    if not Path(cli).exists():
        report["detail"] = f"驱动脚本不存在：{cli}"
        return report
    profile = APP_DIR / "selftest-profile"
    try:
        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(str(profile), channel="msedge", headless=True)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto("about:blank")
                value = page.evaluate("() => 1 + 1")
                report["edge"] = page.evaluate("() => navigator.userAgent")
                report["ok"] = value == 2
                report["detail"] = "Edge 自动化可用" if report["ok"] else f"页面返回异常：{value}"
            finally:
                context.close()
    except Exception as exc:
        report["detail"] = f"启动 Edge 失败：{exc}"
    return report
