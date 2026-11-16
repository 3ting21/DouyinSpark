from __future__ import annotations

import json
import math
import os
import sys
import threading
import tkinter as tk
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__
from . import theme
from .aiclient import (
    DEFAULT_AI_SETTINGS,
    PROVIDERS,
    AIError,
    apply_provider,
    chat_text,
    generate_image,
    generate_message,
    generate_variants,
    normalize_image_model,
    probe_chat,
    settings_with_defaults,
    sticker_prompt,
    suggest_quote,
)
from .auto import AutomationService, Job, JobResult
from .models import Friend, ScheduleSlot, new_id
from .quotes import QUOTE_POOL, format_quote, next_quote as next_local_quote, quote_for
from .scheduler import DueEvent, Scheduler
from .storage import APP_DIR, EncryptedStore, export_backup
from .theme import paint, available as available_themes
from .urls import DOUYIN_CHAT_URL, DOUYIN_HOME_URL, normalize_douyin_id, official_douyin_url

EMOJI_PALETTE = ("🔥", "🫣", "🤔", "😂", "😮‍💨", "🤸", "🍪", "👋", "🤩", "😊", "🫧", "😁", "🌞", "⭐", "🌙", "❤️", "🤡", "🤙", "绝", "🥰", "🎂")

BG = "#f3f6fa"
SURFACE = "#ffffff"
NAV = "#0d1f33"
NAV_HOVER = "#16293f"
NAV_SELECTED = "#1a3451"
INK = "#141e2b"
MUTED = "#5c6878"
ACCENT = "#1b4a75"
ACCENT_HOVER = "#15395c"
LINE = "#dce3ed"
SOFT = "#eff3f8"
SUCCESS = "#186046"
WARNING = "#8a5c17"
DANGER = "#9c2c3a"
WARM_BG = "#fff8f2"
WARM_LINE = "#f0dccd"
WARM_INK = "#342a24"
WARM_MUTED = "#8a7466"
CORAL = "#cf4527"
CORAL_DARK = "#a8341b"
CORAL_SOFT = "#fdeee7"
INFO = "#2a5a88"
PANEL_SOFT = "#fbfcfe"
ON_ACCENT = "#ffffff"
ON_NAV = "#ffffff"

_THEME_KEYS = (
    "BG", "SURFACE", "NAV", "NAV_HOVER", "NAV_SELECTED", "INK", "MUTED", "ACCENT",
    "ACCENT_HOVER", "LINE", "SOFT", "SUCCESS", "WARNING", "DANGER", "WARM_BG",
    "WARM_LINE", "WARM_INK", "WARM_MUTED", "CORAL", "CORAL_DARK", "CORAL_SOFT",
    "INFO", "PANEL_SOFT", "ON_ACCENT", "ON_NAV",
)


def use_theme(name: str | None = None) -> str:
    """Load one palette and rebind the module-level colour constants."""
    palette = theme.apply(name or "light")
    for key in _THEME_KEYS:
        globals()[key] = palette[key]
    return theme.current()


def enable_dpi_awareness() -> None:
    """Draw at the monitor's real pixels so text stops getting bitmap-stretched."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def wrap_px(widget: tk.Misc, px: int) -> int:
    """Wrap width in layout pixels, scaled for the current monitor."""
    return int(px * ui_scale(widget))


def ui_scale(root: tk.Misc) -> float:
    """Physical-pixel scale of this monitor (1.0 at 96 dpi, 1.5 at 150%)."""
    dpi = 0.0
    try:
        import ctypes

        dpi = float(ctypes.windll.user32.GetDpiForWindow(root.winfo_id()))
    except Exception:
        dpi = 0.0
    if not dpi:
        try:
            dpi = float(root.winfo_fpixels("1i"))
        except (tk.TclError, ValueError):
            dpi = 96.0
    return max(1.0, round(dpi / 96.0, 2))


MODE_LABELS = {"text": "只发文字", "emoji": "只发表情", "both": "文字 + 表情"}

DEFAULT_SETTINGS = {
    "auto": {
        "enabled": True,
        "delay_seconds": 0,
        "retry": 1,
        "idle_minutes": 15,
        "close_after_send": False,
        "late_minutes": 120,
    },
    "ai": dict(DEFAULT_AI_SETTINGS),
}


def settings_with_state(raw: dict | None) -> dict:
    merged = {
        "auto": dict(DEFAULT_SETTINGS["auto"]),
        "ai": settings_with_defaults(None),
        "ui": {"theme": "light"},
    }
    if not raw:
        return merged
    for section in ("auto", "ai", "ui"):
        values = raw.get(section)
        if isinstance(values, dict):
            merged[section].update(values)
    return merged


def open_external_url(url: str) -> bool:
    target = official_douyin_url(url)
    try:
        if os.name == "nt" and hasattr(os, "startfile"):
            os.startfile(target)  # type: ignore[attr-defined]
            return True
        return bool(webbrowser.open_new_tab(target))
    except (OSError, webbrowser.Error):
        try:
            return bool(webbrowser.open_new(target))
        except webbrowser.Error:
            return False


def describe_content(mode: str, template: str, emoji: str) -> str:
    """One-line description used by cards and dialogs."""
    label = MODE_LABELS.get(mode, mode)
    parts: list[str] = []
    if mode in ("text", "both") and template:
        parts.append(template)
    if mode in ("emoji", "both") and emoji:
        name = Path(emoji).name if Path(emoji).exists() else emoji
        parts.append(f"表情：{name}")
    return f"{label} · " + " / ".join(parts) if parts else label


def friend_matches(friend: Friend, query: str) -> bool:
    """Whether the search text hits this friend's name, times or templates."""
    query = query.strip().lower()
    if not query:
        return True
    if query in friend.name.lower():
        return True
    return any(query in slot.at.lower() or query in slot.template.lower() for slot in friend.slots)


def parse_schedule(text: str, day: int = -1) -> list[ScheduleSlot]:
    result: list[ScheduleSlot] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split("|", 2)]
        datetime.strptime(parts[0], "%H:%M")
        result.append(ScheduleSlot(day=day, at=parts[0], template=parts[1] if len(parts) > 1 and parts[1] else "你好，{nickname}", emoji_path=parts[2] if len(parts) > 2 else ""))
    return result


class FriendDialog(tk.Toplevel):
    """Friend editor: identity, daily limits, and per-time content split into text / emoji."""

    MODES = (("text", "只发文字"), ("emoji", "只发表情"), ("both", "文字 + 表情"))

    def __init__(self, parent: tk.Misc, friend: Friend | None, callback, sticker_paths: list[str], settings: dict | None = None, lookup=None, open_library=None) -> None:
        super().__init__(parent)
        self.title("新增好友" if friend is None else "编辑好友")
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        scale = ui_scale(self)
        width = min(int(820 * scale), max(int(560 * scale), screen_w - 120))
        height = min(int(840 * scale), max(int(520 * scale), screen_h - 90))
        self.geometry(f"{width}x{height}+{max(20, (screen_w - width) // 2)}+{max(20, (screen_h - height) // 3)}")
        self.minsize(int(560 * scale), int(500 * scale))
        self.transient(parent)
        self.grab_set()
        self.friend_before, self.callback = friend, callback
        self.lookup = lookup
        self.open_library = open_library
        self._lookup_busy = False
        self.settings = settings_with_state(settings)
        self.sticker_paths = [path for path in sticker_paths if Path(path).exists()]
        self.photo_refs: dict[str, tk.PhotoImage] = {}
        self.emoji_buttons: list[tk.Button] = []
        self.rows: list[dict] = []
        self.active_row: dict | None = None
        self._setup_style()
        self._build()
        self.bind("<Escape>", lambda _event: self.destroy())

    # ----------------------------------------------------------------- styling
    def _setup_style(self) -> None:
        style = ttk.Style(self)
        try:
            if style.theme_use() != "clam":
                style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("DialogPrimary.TButton", background=ACCENT, foreground=ON_ACCENT, borderwidth=0, padding=(24, 10), font=("Microsoft YaHei UI", 10, "bold"))
        style.map("DialogPrimary.TButton", background=[("active", ACCENT_HOVER)])
        style.configure("DialogSoft.TButton", background=SURFACE, foreground=INK, borderwidth=1, bordercolor=LINE, padding=(14, 8), font=("Microsoft YaHei UI", 9))
        style.map("DialogSoft.TButton", background=[("active", SOFT)])
        style.configure("Dialog.TSpinbox", padding=4, arrowsize=12)

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        self.configure(bg=BG)
        banner = tk.Frame(self, bg=NAV, padx=22, pady=14)
        banner.pack(fill="x")
        tk.Frame(self, bg=paint("#2a3f58"), height=1).pack(fill="x")
        tk.Label(banner, text="新增好友" if self.friend_before is None else "编辑好友", bg=NAV, fg=ON_NAV, font=("Microsoft YaHei UI", 16, "bold")).pack(anchor="w")
        tk.Label(
            banner,
            text="昵称、每日上限和发送时间；每个时间点都能单独选「只发文字 / 只发表情 / 文字 + 表情」。",
            bg=NAV,
            fg=paint("#c9d6e8"),
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", pady=(4, 0))

        footer = tk.Frame(self, bg=SURFACE, padx=16, pady=12)
        footer.pack(side="bottom", fill="x")
        tk.Frame(self, bg=LINE, height=1).pack(side="bottom", fill="x")
        ttk.Button(footer, text="保存好友", command=self.save, style="DialogPrimary.TButton").pack(side="right", padx=(8, 0))
        ttk.Button(footer, text="取消", command=self.destroy, style="DialogSoft.TButton").pack(side="right")

        body = self._scroll_area()
        self._build_identity(body)
        self._build_content(body)
        self._build_picker(body)
        tk.Frame(body, bg=BG, height=14).pack(fill="x")

    def _scroll_area(self) -> tk.Frame:
        self.box = Scrollable(self, bg=BG)
        self.box.pack(fill="both", expand=True, pady=(0, 4))
        self.body_canvas = self.box.canvas
        return self.box.inner

    def _card(self, parent: tk.Misc, title: str, hint: str = "") -> tk.Frame:
        scale = ui_scale(self)
        card = tk.Frame(parent, bg=SURFACE, highlightbackground=LINE, highlightthickness=1)
        card.pack(fill="x", padx=int(16 * scale), pady=(int(12 * scale), 0))
        tk.Label(card, text=title, bg=SURFACE, fg=INK, font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w", padx=int(18 * scale), pady=(int(14 * scale), 0))
        if hint:
            tk.Label(card, text=hint, bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 9), justify="left", wraplength=int(700 * scale)).pack(anchor="w", padx=int(18 * scale), pady=(3, 0))
        body = tk.Frame(card, bg=SURFACE)
        body.pack(fill="x", padx=int(18 * scale), pady=(int(10 * scale), int(14 * scale)))
        return body

    def _build_identity(self, parent: tk.Misc) -> None:
        body = self._card(parent, "基本信息", "昵称不必和左栏一字不差：表情、空格、省略号会自动忽略；但太短（两个字以内）容易被相似昵称混淆，写长一点更稳。")
        self.name = tk.StringVar(value=self.friend_before.name if self.friend_before else "")
        self.douyin = tk.StringVar(value=self.friend_before.douyin_id if self.friend_before else "")
        self.limit = tk.IntVar(value=self.friend_before.daily_limit if self.friend_before else 3)
        self.enabled = tk.BooleanVar(value=self.friend_before.enabled if self.friend_before else True)
        self.consent = tk.BooleanVar(value=self.friend_before.consent if self.friend_before else False)

        body.columnconfigure(1, weight=1)
        tk.Label(body, text="昵称", bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 9)).grid(row=0, column=0, sticky="w", pady=(0, 10), padx=(0, 14))
        entry = tk.Entry(
            body, textvariable=self.name, relief="flat", bg=SURFACE, fg=INK, font=("Microsoft YaHei UI", 11),
            highlightthickness=1, highlightbackground=LINE, highlightcolor=ACCENT, insertbackground=INK,
        )
        entry.grid(row=0, column=1, sticky="ew", pady=(0, 10), ipady=6)

        tk.Label(body, text="抖音号", bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 9)).grid(row=1, column=0, sticky="w", pady=(0, 10), padx=(0, 14))
        douyin_row = tk.Frame(body, bg=SURFACE)
        douyin_row.grid(row=1, column=1, sticky="ew", pady=(0, 10))
        douyin_row.columnconfigure(0, weight=1)
        tk.Entry(
            douyin_row, textvariable=self.douyin, relief="flat", bg=SURFACE, fg=INK, font=("Microsoft YaHei UI", 11),
            highlightthickness=1, highlightbackground=LINE, highlightcolor=ACCENT, insertbackground=INK,
        ).grid(row=0, column=0, sticky="ew", ipady=6)
        self.lookup_button = ttk.Button(
            douyin_row, text="按抖音号找人", command=self._lookup_douyin, style="DialogSoft.TButton"
        )
        self.lookup_button.grid(row=0, column=1, padx=(10, 0))
        self.lookup_note = tk.Label(
            body, text="昵称和抖音号至少填一个。昵称太长记不住时，只填抖音号，点「按抖音号找人」会自动补昵称。",
            bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 9), anchor="w", justify="left",
        )
        self.lookup_note.grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 10))
        wrap_px(self.lookup_note, 560)

        tk.Label(body, text="每日上限", bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 9)).grid(row=3, column=0, sticky="w", pady=(0, 10), padx=(0, 14))
        limit_row = tk.Frame(body, bg=SURFACE)
        limit_row.grid(row=3, column=1, sticky="w", pady=(0, 10))
        ttk.Spinbox(limit_row, from_=1, to=99, width=6, textvariable=self.limit, style="Dialog.TSpinbox").pack(side="left")
        tk.Label(limit_row, text="条 / 天", bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(8, 0))

        switches = tk.Frame(body, bg=SURFACE)
        switches.grid(row=4, column=0, columnspan=2, sticky="w")
        tk.Checkbutton(switches, text="启用该好友", variable=self.enabled, bg=SURFACE, activebackground=SURFACE, fg=INK, font=("Microsoft YaHei UI", 9), highlightthickness=0, selectcolor=SURFACE).pack(side="left")
        tk.Checkbutton(switches, text="已确认可以为这位好友设置提醒", variable=self.consent, bg=SURFACE, activebackground=SURFACE, fg=INK, font=("Microsoft YaHei UI", 9), highlightthickness=0, selectcolor=SURFACE).pack(side="left", padx=(22, 0))
        tk.Label(body, text="未确认时也能保存，确认前不会产生定时任务。", bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 9)).grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))

    def _lookup_douyin(self) -> None:
        raw = self.douyin.get().strip()
        if not raw:
            messagebox.showwarning("先填抖音号", "请在「抖音号」一栏填写对方的抖音号，再点查找。", parent=self)
            return
        if self.lookup is None:
            messagebox.showinfo("无法查找", "当前窗口没有连接自动化浏览器，请关闭后从主界面重新打开。", parent=self)
            return
        if self._lookup_busy:
            return
        self._lookup_busy = True
        self.lookup_button.configure(state="disabled")
        self.lookup_note.configure(text=f"正在用「{raw}」搜索抖音用户，浏览器会打开搜索页…", fg=INFO)
        self.lookup(self, raw)

    def show_lookup(self, candidates: list[dict], detail: str) -> None:
        """Fill the nickname from a resolved 抖音号 search, or explain why not."""
        self._lookup_busy = False
        try:
            self.lookup_button.configure(state="normal")
        except tk.TclError:
            return
        if not candidates:
            self.lookup_note.configure(text=detail or "没解析到结果，请在浏览器搜索结果里手动确认。", fg=WARNING)
            wrap_px(self.lookup_note, 560)
            return
        names = [str(item.get("name") or "").strip() for item in candidates if item.get("name")]
        names = list(dict.fromkeys(names))[:8]
        if not names:
            self.lookup_note.configure(text=detail or "没解析到昵称，请手动填写。", fg=WARNING)
            return
        if len(names) == 1:
            self.name.set(names[0])
            self.lookup_note.configure(text=f"已按抖音号匹配到昵称「{names[0]}」，请确认后保存。", fg=SUCCESS)
            wrap_px(self.lookup_note, 560)
            return
        picker = tk.Toplevel(self)
        picker.title("选择匹配到的人")
        picker.transient(self)
        picker.grab_set()
        scale = ui_scale(self)
        picker.geometry(f"{int(520 * scale)}x{int(360 * scale)}")
        frame = tk.Frame(picker, bg=SURFACE, padx=16, pady=14)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text="抖音号匹配到多个用户，点一个作为昵称：", bg=SURFACE, fg=INK, font=("Microsoft YaHei UI", 10)).pack(anchor="w", pady=(0, 8))
        box = tk.Frame(frame, bg=SURFACE)
        box.pack(fill="both", expand=True)

        def choose(name: str) -> None:
            self.name.set(name)
            self.lookup_note.configure(text=f"已选择「{name}」，请确认后保存。", fg=SUCCESS)
            picker.destroy()

        for name in names:
            ttk.Button(box, text=name, command=lambda value=name: choose(value), style="DialogSoft.TButton").pack(fill="x", pady=3)
        ttk.Button(frame, text="都不对，我手动填", command=picker.destroy, style="DialogSoft.TButton").pack(anchor="e", pady=(10, 0))

    def _build_content(self, parent: tk.Misc) -> None:
        body = self._card(parent, "发送内容", "每个时间点独立选择发送方式：只发文字不动表情库，只发表情不用写文案。")
        head = tk.Frame(body, bg=SURFACE)
        head.pack(fill="x", pady=(0, 10))
        tk.Label(head, text="每日发送时间", bg=SURFACE, fg=INK, font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
        tk.Label(head, text="可添加多个", bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(10, 0))
        ttk.Button(head, text="＋ 添加时间", command=lambda: self.add_slot(), style="DialogSoft.TButton").pack(side="right")
        self.slots_box = tk.Frame(body, bg=SURFACE)
        self.slots_box.pack(fill="x")
        existing = sorted((self.friend_before.slots if self.friend_before else []), key=lambda slot: slot.at)
        for slot in existing or [ScheduleSlot(at="09:00", mode="text")]:
            self.add_slot(slot)

    def _build_picker(self, parent: tk.Misc) -> None:
        body = self._card(parent, "表情库", "先在某个时间点里点「选择表情」，再点下面的图案；表情可以单独发送。")
        head = tk.Frame(body, bg=SURFACE)
        head.pack(fill="x", pady=(0, 8))
        self.pick_label = tk.Label(head, text="当前：未选择时间点", bg=SURFACE, fg=INFO, font=("Microsoft YaHei UI", 9, "bold"))
        self.pick_label.pack(side="left")
        if self.open_library is not None:
            ttk.Button(head, text="管理表情库（添加 / AI 生成）", command=self.open_library, style="DialogSoft.TButton").pack(side="right")
        area = tk.Frame(body, bg=SURFACE)
        area.pack(fill="x")
        self.emoji_canvas = tk.Canvas(area, bg=SURFACE, height=176, highlightthickness=0)
        bar = ttk.Scrollbar(area, orient="vertical", command=self.emoji_canvas.yview)
        self.emoji_canvas.configure(yscrollcommand=bar.set)
        self.emoji_canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        grid = tk.Frame(self.emoji_canvas, bg=SURFACE)
        window = self.emoji_canvas.create_window((0, 0), window=grid, anchor="nw")
        grid.bind("<Configure>", lambda _event: self.emoji_canvas.configure(scrollregion=self.emoji_canvas.bbox("all")))
        self.emoji_canvas.bind("<Configure>", lambda event: (self.emoji_canvas.itemconfigure(window, width=event.width), self._layout_emoji_grid(event.width)))
        self._build_emoji_grid(grid)

    # ------------------------------------------------------------------- slots
    def add_slot(self, slot: ScheduleSlot | None = None) -> None:
        card = tk.Frame(self.slots_box, bg=paint("#f9fbfe"), highlightbackground=LINE, highlightthickness=1)
        card.pack(fill="x", pady=(0, 10))
        inner = tk.Frame(card, bg=paint("#f9fbfe"), padx=14, pady=12)
        inner.pack(fill="x")

        text_time = slot.at if slot else "09:00"
        hour = tk.IntVar(value=int(text_time.split(":")[0]))
        minute = tk.IntVar(value=int(text_time.split(":")[1]))
        message = tk.StringVar(value=slot.template if slot else "你好，{nickname}")
        emoji = tk.StringVar(value=slot.emoji_path if slot else "")
        default_mode = slot.mode if slot else ("both" if emoji.get().strip() else "text")
        if slot and slot.emoji_path and slot.template and slot.mode == "text":
            default_mode = "text"
        mode = tk.StringVar(value=default_mode)

        top = tk.Frame(inner, bg=paint("#f9fbfe"))
        top.pack(fill="x")
        tk.Label(top, text="时间", bg=paint("#f9fbfe"), fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(side="left")
        ttk.Spinbox(top, from_=0, to=23, width=4, textvariable=hour, style="Dialog.TSpinbox").pack(side="left", padx=(6, 2))
        tk.Label(top, text=":", bg=paint("#f9fbfe"), fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(side="left")
        ttk.Spinbox(top, from_=0, to=59, width=4, textvariable=minute, style="Dialog.TSpinbox").pack(side="left", padx=(2, 18))
        tk.Label(top, text="发送方式", bg=paint("#f9fbfe"), fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(0, 8))
        for key, label in self.MODES:
            tk.Radiobutton(
                top, text=label, variable=mode, value=key, bg=paint("#f9fbfe"), activebackground=paint("#f9fbfe"),
                fg=INK, font=("Microsoft YaHei UI", 9), selectcolor=SURFACE, highlightthickness=0, bd=0,
            ).pack(side="left", padx=(0, 10))
        ttk.Button(top, text="删除", command=lambda: self.remove_slot(state), style="DialogSoft.TButton").pack(side="right")

        text_row = tk.Frame(inner, bg=paint("#f9fbfe"))
        text_row.pack(fill="x", pady=(10, 0))
        tk.Label(text_row, text="文字", bg=paint("#f9fbfe"), fg=MUTED, font=("Microsoft YaHei UI", 9), width=6, anchor="w").pack(side="left")
        entry = tk.Entry(
            text_row, textvariable=message, relief="flat", bg=SURFACE, fg=INK, font=("Microsoft YaHei UI", 10),
            highlightthickness=1, highlightbackground=LINE, highlightcolor=ACCENT, insertbackground=INK,
        )
        entry.pack(side="left", fill="x", expand=True, ipady=5)
        ttk.Button(text_row, text="AI 候选", command=lambda: self._open_ai_candidates(state), style="DialogSoft.TButton").pack(side="left", padx=(8, 0))
        tk.Label(text_row, text="{nickname} {date} {weekday}", bg=paint("#f9fbfe"), fg=paint("#8b98ab"), font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(8, 0))

        emoji_row = tk.Frame(inner, bg=paint("#f9fbfe"))
        emoji_row.pack(fill="x", pady=(8, 0))
        tk.Label(emoji_row, text="表情", bg=paint("#f9fbfe"), fg=MUTED, font=("Microsoft YaHei UI", 9), width=6, anchor="w").pack(side="left")
        preview = tk.Label(emoji_row, bg=SURFACE, width=46, height=46, highlightbackground=LINE, highlightthickness=1, text="未选", fg=MUTED, font=("Microsoft YaHei UI", 8))
        preview.pack(side="left")
        select_btn = ttk.Button(emoji_row, text="选择表情", command=lambda: self._select_row_emoji(state), style="DialogSoft.TButton")
        select_btn.pack(side="left", padx=(10, 6))
        clear_btn = ttk.Button(emoji_row, text="清除", command=lambda: self._clear_emoji(state), style="DialogSoft.TButton")
        clear_btn.pack(side="left")
        hint = tk.Label(emoji_row, text="", bg=paint("#f9fbfe"), fg=MUTED, font=("Microsoft YaHei UI", 9))
        hint.pack(side="left", padx=(12, 0))

        state = {
            "frame": card,
            "hour": hour,
            "minute": minute,
            "message": message,
            "emoji": emoji,
            "mode": mode,
            "entry": entry,
            "preview": preview,
            "select": select_btn,
            "clear": clear_btn,
            "hint": hint,
        }
        self.rows.append(state)
        mode.trace_add("write", lambda *_args: self._apply_mode(state))
        self._apply_mode(state)
        self._render_preview(state)

    def _apply_mode(self, state: dict) -> None:
        mode = state["mode"].get()
        has_text = mode in ("text", "both")
        has_emoji = mode in ("emoji", "both")
        state["entry"].configure(state="normal" if has_text else "disabled", disabledbackground=paint("#eff3f8"), disabledforeground=paint("#9aa8bd"))
        for widget in (state["select"], state["clear"]):
            widget.configure(state="normal" if has_emoji else "disabled")
        state["hint"].configure(
            text={
                "text": "只发送文字，不附加表情。",
                "emoji": "只发送表情，不需要文案。",
                "both": "先发文字，再发这张表情。",
            }.get(mode, "")
        )
        self._highlight_active()

    def _highlight_active(self) -> None:
        for state in self.rows:
            active = state is self.active_row
            bg = paint("#fff6f5") if active else paint("#f9fbfe")
            state["frame"].configure(bg=bg, highlightbackground=ACCENT if active else LINE)
            self._paint_bg(state["frame"], bg)
            state["preview"].configure(bg=SURFACE)

    def _paint_bg(self, widget: tk.Misc, bg: str) -> None:
        for child in widget.winfo_children():
            if isinstance(child, (tk.Frame, tk.Label)):
                try:
                    child.configure(bg=bg)
                except tk.TclError:
                    pass
                self._paint_bg(child, bg)

    def _select_row_emoji(self, state: dict) -> None:
        self.active_row = state
        label_time = f"{int(state['hour'].get()):02d}:{int(state['minute'].get()):02d}"
        self.pick_label.configure(text=f"当前：{label_time} 的表情")
        self._highlight_active()
        self.focus_force()

    def _clear_emoji(self, state: dict) -> None:
        state["emoji"].set("")
        self._render_preview(state)

    def _open_ai_candidates(self, state: dict) -> None:
        nickname = self.name.get().strip() or "好友"
        AICandidateDialog(
            self, self.settings, nickname, state["message"].get().strip(),
            lambda text: self._use_candidate(state, text),
        )

    def _use_candidate(self, state: dict, text: str) -> None:
        state["message"].set(text)
        if state["mode"].get() == "emoji":
            state["mode"].set("both")

    def remove_slot(self, state: dict) -> None:
        if len(self.rows) <= 1:
            messagebox.showinfo("至少保留一条", "每位好友至少需要一个发送时间。", parent=self)
            return
        state["frame"].destroy()
        self.rows = [item for item in self.rows if item is not state]
        if self.active_row is state:
            self.active_row = None
            self.pick_label.configure(text="当前：未选择时间点")

    # ------------------------------------------------------------------ emojis
    def _build_emoji_grid(self, parent: tk.Misc) -> None:
        for path in self.sticker_paths[:40]:
            try:
                image = tk.PhotoImage(file=path)
                image = image.subsample(max(1, image.width() // 44), max(1, image.height() // 44))
                self.photo_refs.setdefault(path, image)
                button = tk.Button(
                    parent, image=image, width=44, height=44, relief="flat",
                    bg=paint("#f4f7fc"), activebackground=paint("#e3edfb"), bd=0, cursor="hand2",
                    command=lambda value=path: self._set_emoji(value),
                )
                self.emoji_buttons.append(button)
            except tk.TclError:
                continue
        for emoji in EMOJI_PALETTE:
            button = tk.Button(
                parent, text=emoji, font=("Segoe UI Emoji", 18), width=3, height=1,
                relief="flat", bg=paint("#f4f7fc"), activebackground=paint("#e3edfb"), bd=0, cursor="hand2",
                command=lambda value=emoji: self._set_emoji(value),
            )
            self.emoji_buttons.append(button)
        self._layout_emoji_grid(700)

    def _layout_emoji_grid(self, width: int) -> None:
        if not self.emoji_buttons:
            return
        columns = max(5, min(12, int(max(width, 1) // 56)))
        for index, button in enumerate(self.emoji_buttons):
            button.grid(row=index // columns, column=index % columns, padx=3, pady=3, sticky="nsew")

    def _set_emoji(self, value: str) -> None:
        if self.active_row is None:
            messagebox.showinfo("先选择时间点", "请先在一个时间点里点「选择表情」，再挑选下面的图案。", parent=self)
            return
        self.active_row["emoji"].set(value)
        if self.active_row["mode"].get() == "text":
            self.active_row["mode"].set("both")
        self._render_preview(self.active_row)

    def _render_preview(self, state: dict) -> None:
        value = state["emoji"].get().strip()
        preview = state["preview"]
        if not value:
            preview.configure(image="", text="未选", fg=MUTED)
            preview.image = None
            return
        path = Path(value)
        if path.exists() and path.suffix.lower() in {".png", ".gif", ".jpg", ".jpeg", ".webp"}:
            try:
                image = self.photo_refs.get(value)
                if image is None:
                    image = tk.PhotoImage(file=value)
                    image = image.subsample(max(1, image.width() // 40), max(1, image.height() // 40))
                    self.photo_refs[value] = image
                preview.configure(image=image, text="")
                preview.image = image
                return
            except tk.TclError:
                pass
        preview.configure(image="", text=value[:2], fg=INK, font=("Segoe UI Emoji", 16))
        preview.image = None

    # -------------------------------------------------------------------- save
    def save(self) -> None:
        name = self.name.get().strip()
        douyin = normalize_douyin_id(self.douyin.get())
        if not name and not douyin:
            messagebox.showwarning(
                "信息不完整", "昵称和抖音号至少填一个：知道昵称就填昵称，只记得抖音号就填抖音号。", parent=self
            )
            return
        slots: list[ScheduleSlot] = []
        for state in self.rows:
            try:
                at = f"{int(state['hour'].get()):02d}:{int(state['minute'].get()):02d}"
                datetime.strptime(at, "%H:%M")
            except (ValueError, tk.TclError):
                messagebox.showwarning("时间格式错误", "请选择有效的小时和分钟。", parent=self)
                return
            mode = state["mode"].get()
            text = state["message"].get().strip()
            emoji = state["emoji"].get().strip()
            if mode in ("text", "both") and not text:
                messagebox.showwarning("文案为空", f"{at} 选择了发文字，请填写文字内容。", parent=self)
                return
            if mode in ("emoji", "both") and not emoji:
                messagebox.showwarning("表情未选", f"{at} 包含了表情，请先在表情库里选一个。", parent=self)
                return
            try:
                slots.append(
                    ScheduleSlot(
                        day=-1,
                        at=at,
                        template="" if mode == "emoji" else text,
                        emoji_path="" if mode == "text" else emoji,
                        mode=mode,
                    )
                )
            except ValueError:
                messagebox.showwarning("内容错误", f"{at} 的内容无法保存，请检查。", parent=self)
                return
        try:
            limit = int(self.limit.get())
            if limit < 1 or limit > 99:
                raise ValueError("limit out of range")
        except (ValueError, tk.TclError):
            messagebox.showwarning("上限错误", "每日上限请输入 1 到 99 的整数。", parent=self)
            return
        friend = Friend(
            friend_id=self.friend_before.friend_id if self.friend_before else new_id(),
            name=name,
            douyin_id=douyin,
            conversation_url="",
            consent=self.consent.get(),
            enabled=self.enabled.get(),
            daily_limit=limit,
            slots=slots,
        )
        try:
            self.callback(friend)
        except Exception as exc:
            messagebox.showerror("保存失败", f"好友未能保存到本地：{exc}", parent=self)
            return
        self.destroy()


class ReminderToast(tk.Toplevel):
    def __init__(self, parent: tk.Misc, event: DueEvent, friend: Friend, callback) -> None:
        super().__init__(parent)
        self.event, self.friend, self.callback = event, friend, callback
        self.title("续火花提醒")
        self.attributes("-topmost", True)
        self.geometry("430x205+{}+{}".format(self.winfo_screenwidth() - 450, self.winfo_screenheight() - 275))
        frame = ttk.Frame(self, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=f"续火花提醒 · {friend.name}", font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
        ttk.Label(frame, text=event.due_at.strftime("%Y-%m-%d %H:%M"), foreground=paint("#6b7789")).pack(anchor="w", pady=(2, 0))
        ttk.Label(frame, text=describe_content(event.mode, event.message, event.emoji_path), wraplength=400).pack(anchor="w", pady=8)
        actions = ttk.Frame(frame)
        actions.pack(fill="x")
        ttk.Button(actions, text="复制并打开官网", command=self.copy_open).pack(side="left", padx=2)
        ttk.Button(actions, text="复制", command=self.copy).pack(side="left", padx=2)
        ttk.Button(actions, text="已手动发送", command=lambda: self.finish("sent")).pack(side="left", padx=2)
        ttk.Button(actions, text="跳过", command=lambda: self.finish("skipped")).pack(side="right")

    def copy(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self.event.message)
        self.update()

    def copy_open(self) -> None:
        self.copy()
        opener = getattr(self.master, "open_douyin", None)
        opened = opener() if callable(opener) else open_external_url(DOUYIN_CHAT_URL)
        if opened is False:
            messagebox.showerror("无法打开浏览器", "请手动打开 https://www.douyin.com/chat。", parent=self)
        if self.event.emoji_path and Path(self.event.emoji_path).exists() and hasattr(os, "startfile"):
            os.startfile(self.event.emoji_path)  # type: ignore[attr-defined]

    def finish(self, status: str) -> None:
        self.callback(self.event.event_id, status)
        self.destroy()


class AutoConfirmDialog(tk.Toplevel):
    """One-time confirmation per friend before the first automatic send."""

    def __init__(self, parent: tk.Misc, event: DueEvent, friend: Friend, text: str, note: str, on_confirm, on_skip) -> None:
        super().__init__(parent)
        self.on_confirm, self.on_skip, self.event = on_confirm, on_skip, event
        self.title("首次自动发送确认")
        self.attributes("-topmost", True)
        self.geometry("+{}+{}".format(max(80, self.winfo_screenwidth() // 2 - 260), max(80, self.winfo_screenheight() // 3 - 180)))
        self.transient(parent)
        body = tk.Frame(self, bg=SURFACE, padx=20, pady=18)
        body.pack(fill="both", expand=True)
        tk.Label(body, text=f"即将自动发给「{friend.name}」", bg=SURFACE, fg=INK, font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w")
        tk.Label(body, text=event.due_at.strftime("%Y-%m-%d %H:%M"), bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(2, 8))
        text_box = tk.Label(
            body,
            text=text if text else "（本条只发表情，不发送文字）",
            bg=paint("#f5f8fd"), fg=INK, justify="left", wraplength=wrap_px(self, 460), padx=12, pady=10, font=("Microsoft YaHei UI", 11),
        )
        text_box.pack(fill="x")
        tk.Label(body, text=f"来源：{note}　·　方式：{MODE_LABELS.get(event.mode, event.mode)}", bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(6, 2))
        if event.emoji_path:
            tk.Label(body, text=f"表情：{Path(event.emoji_path).name if Path(event.emoji_path).exists() else event.emoji_path}", bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(anchor="w")
        self.always = tk.BooleanVar(value=True)
        ttk.Checkbutton(body, text="以后这位好友都自动发送，不再询问", variable=self.always).pack(anchor="w", pady=(10, 12))
        actions = tk.Frame(body, bg=SURFACE)
        actions.pack(fill="x")
        ttk.Button(actions, text="确认并发送", command=self._confirm, style="LargePrimary.TButton").pack(side="left")
        ttk.Button(actions, text="本次跳过", command=self._skip, style="LargeSoft.TButton").pack(side="left", padx=8)

    def _confirm(self) -> None:
        self.on_confirm(self.event, self.always.get())
        self.destroy()

    def _skip(self) -> None:
        self.on_skip(self.event)
        self.destroy()


class SettingsDialog(tk.Toplevel):
    """Automation + AI configuration."""

    def __init__(self, parent: tk.Misc, settings: dict, on_save) -> None:
        super().__init__(parent)
        self.on_save = on_save
        self.host = parent
        self.title("自动化与 AI 设置")
        scale = ui_scale(self)
        self.geometry(f"{int(700 * scale)}x{int(760 * scale)}")
        self.minsize(int(620 * scale), int(660 * scale))
        self.transient(parent)
        self.grab_set()
        self.configure(bg=BG)
        cfg = settings_with_state(settings)
        auto, ai = cfg["auto"], cfg["ai"]
        self.theme_key = str(cfg.get("ui", {}).get("theme") or "light")

        footer = tk.Frame(self, bg=SURFACE, padx=16, pady=12)
        footer.pack(side="bottom", fill="x")
        ttk.Button(footer, text="保存设置", command=self._save, style="LargePrimary.TButton").pack(side="right")
        ttk.Button(footer, text="取消", command=self.destroy, style="LargeSoft.TButton").pack(side="right", padx=8)

        canvas = tk.Canvas(self, bg=BG, highlightthickness=0)
        scroll = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        holder = tk.Frame(canvas, bg=BG)
        self._holder = holder
        window = canvas.create_window((0, 0), window=holder, anchor="nw")
        holder.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))

        self.auto_enabled = tk.BooleanVar(value=bool(auto.get("enabled", True)))
        self.delay = tk.IntVar(value=int(auto.get("delay_seconds", 0)))
        self.retry = tk.IntVar(value=int(auto.get("retry", 1)))
        self.idle = tk.IntVar(value=int(auto.get("idle_minutes", 15)))
        self.late = tk.IntVar(value=int(auto.get("late_minutes", 120)))
        self.close_after = tk.BooleanVar(value=bool(auto.get("close_after_send", False)))
        self.ai_enabled = tk.BooleanVar(value=bool(ai.get("enabled", False)))
        self.provider = tk.StringVar(value=str(ai.get("provider", "zhipu")))
        self.base_url = tk.StringVar(value=str(ai.get("base_url", "")))
        self.model = tk.StringVar(value=str(ai.get("model", "")))
        self.api_key = tk.StringVar(value=str(ai.get("api_key", "")))
        self.temperature = tk.DoubleVar(value=float(ai.get("temperature", 0.8)))
        self.max_chars = tk.IntVar(value=int(ai.get("max_chars", 32)))
        self.timeout = tk.IntVar(value=int(ai.get("timeout", 20)))
        self.persona = tk.StringVar(value=str(ai.get("persona", "")))
        self.image_model = tk.StringVar(value=str(ai.get("image_model", "")))
        self._image_model_fixed = False

        theme_grid = self._section("外观主题", "点一下立即切换（本窗口会关闭并回到主界面），整窗重绘；深色更适合晚上和演示，浅色适合白天。")
        self.theme_buttons: dict[str, Any] = {}
        for index, (key, label, hint) in enumerate(available_themes()):
            row = tk.Frame(theme_grid, bg=SURFACE)
            row.grid(row=index, column=0, columnspan=3, sticky="ew", pady=3)
            chips = tk.Frame(row, bg=SURFACE)
            chips.pack(side="left")
            sample = theme.THEMES.get(key, {}).get("colors", {})
            for token in ("BG", "SURFACE", "ACCENT", "CORAL"):
                value = str(sample.get(token, "#888888"))
                tk.Frame(chips, bg=value, width=22, height=18, highlightbackground=LINE, highlightthickness=1).pack(side="left", padx=(0, 4))
            mark = "● " if key == self.theme_key else "○ "
            button = ttk.Button(
                row, text=f"{mark}{label}", style="Soft.TButton",
                command=lambda name=key: self._pick_theme(name),
            )
            button.pack(side="left", padx=10)
            tk.Label(row, text=hint, bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(side="left")
            self.theme_buttons[key] = button

        grid = self._section("自动发送", "到点后程序自己开浏览器、找会话、发消息；首次对某位好友自动发送前会确认一次。")
        self._check(grid, 0, "开启到点自动发送", self.auto_enabled, "关闭后回退为提醒窗口+手动发送")
        self._spin(grid, 1, "发送前随机延迟（秒）", self.delay, 0, 600, "0 表示到点立即发送")
        self._spin(grid, 2, "失败重试次数", self.retry, 0, 5, "每位好友每次计划最多重试次数")
        self._spin(grid, 3, "浏览器空闲关闭（分钟）", self.idle, 0, 240, "0 表示发完不自动关闭；关闭可省内存")
        self._spin(grid, 4, "错过多久不再补发（分钟）", self.late, 0, 720, "程序晚启动时，超过这个时长就跳过，避免深夜补发")
        self._check(grid, 5, "每次发送后关闭浏览器", self.close_after, "更省内存，但每条消息慢 5-10 秒")

        grid2 = self._section("AI 文案", "开启后每次到点由模型生成新文案；失败会自动回退到时间点里的模板文案。")
        self._check(grid2, 0, "用 AI 生成文案", self.ai_enabled, "需要填写 API Key，本地模板始终作为兜底")
        row = tk.Frame(grid2, bg=SURFACE)
        row.grid(row=1, column=0, columnspan=3, sticky="ew", pady=5)
        tk.Label(row, text="服务商", bg=SURFACE, fg=MUTED, width=16, anchor="w").pack(side="left")
        combo = ttk.Combobox(row, values=[f"{key}: {value['label']}" for key, value in PROVIDERS.items()], state="readonly", width=34)
        combo.set(next((f"{k}: {v['label']}" for k, v in PROVIDERS.items() if k == self.provider.get()), ""))
        combo.pack(side="left")
        combo.bind("<<ComboboxSelected>>", lambda _e: self._apply_provider(combo.get()))
        self._entry(grid2, 2, "接口地址", self.base_url, "OpenAI 兼容的 /chat/completions 前缀")
        self._entry(grid2, 3, "模型名", self.model, "如 glm-4-flash、deepseek-chat")
        self._entry(grid2, 4, "API Key", self.api_key, "只保存在本机加密数据库里", secret=True)
        self._spin(grid2, 5, "温度", self.temperature, 0.0, 1.5, "越高越发散", step=0.1)
        self._spin(grid2, 6, "文案上限（字）", self.max_chars, 8, 120, "超出会被截断")
        self._entry(grid2, 7, "语气设定", self.persona, "如：轻松自然、像熟人随口聊天")
        self._entry(grid2, 8, "图像模型", self.image_model, "用于 AI 生成表情，如 cogview-3-flash、gpt-image-1；留空则关闭表情生成")
        self._spin(grid2, 9, "接口超时（秒）", self.timeout, 5, 120, "中转站慢就调大，如 45；金句至少按 45 秒等", step=5)

        test_row = tk.Frame(grid2, bg=SURFACE)
        test_row.grid(row=10, column=0, columnspan=3, sticky="ew", pady=(6, 2))
        ttk.Button(test_row, text="测试生成一条", command=self._test_generate, style="Soft.TButton").pack(side="left")
        ttk.Button(test_row, text="测试接口连通性", command=self._test_connection, style="Soft.TButton").pack(side="left", padx=(6, 0))
        self.test_label = tk.Label(test_row, text="", bg=SURFACE, fg=MUTED, justify="left", wraplength=wrap_px(self, 470), font=("Microsoft YaHei UI", 9))
        self.test_label.pack(side="left", padx=10)

    def _pick_theme(self, name: str) -> None:
        """Apply instantly, keep other unsaved edits, then close this dialog."""
        if getattr(getattr(self.host, "automation", None), "busy", False):
            messagebox.showinfo(
                "正在自动发送",
                "这一轮发送还没走完，等它结束再换主题，避免界面重建打断进度。",
                parent=self,
            )
            return
        values = self._current_settings()
        values["ui"]["theme"] = name
        try:
            self.host.on_save(values)
        except Exception:
            pass
        self.host.set_theme(name)
        self.destroy()

    # ---------------------------------------------------------------- ui helpers
    def _section(self, title: str, hint: str) -> tk.Frame:
        card = tk.Frame(self._holder, bg=SURFACE, padx=14, pady=12, highlightbackground=LINE, highlightthickness=1)
        card.pack(fill="x", padx=14, pady=(10, 4))
        tk.Label(card, text=title, bg=SURFACE, fg=INK, font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")
        tk.Label(card, text=hint, bg=SURFACE, fg=MUTED, justify="left", wraplength=wrap_px(self, 600), font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(2, 8))
        body = tk.Frame(card, bg=SURFACE)
        body.pack(fill="x")
        body.grid_columnconfigure(1, weight=1)
        return body

    def _check(self, parent, row, text, variable, hint="") -> None:
        line = tk.Frame(parent, bg=SURFACE)
        line.grid(row=row, column=0, columnspan=3, sticky="ew", pady=4)
        ttk.Checkbutton(line, text=text, variable=variable).pack(side="left")
        if hint:
            tk.Label(line, text=hint, bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(side="left", padx=10)

    def _spin(self, parent, row, label, variable, low, high, hint="", step=1) -> None:
        tk.Label(parent, text=label, bg=SURFACE, fg=INK, width=18, anchor="w").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Spinbox(parent, from_=low, to=high, increment=step, width=8, textvariable=variable).grid(row=row, column=1, sticky="w", pady=4)
        if hint:
            tk.Label(parent, text=hint, bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 9)).grid(row=row, column=2, sticky="w", padx=10)

    def _entry(self, parent, row, label, variable, hint="", secret=False) -> None:
        tk.Label(parent, text=label, bg=SURFACE, fg=INK, width=18, anchor="w").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(parent, textvariable=variable, show="*" if secret else "", width=42).grid(row=row, column=1, sticky="ew", pady=4)
        if hint:
            tk.Label(parent, text=hint, bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 9)).grid(row=row, column=2, sticky="w", padx=10)

    def _apply_provider(self, text: str) -> None:
        key = text.split(":")[0].strip()
        preset = PROVIDERS.get(key)
        if preset:
            self.base_url.set(preset["base_url"])
            self.model.set(preset["model"])
            self.image_model.set(preset.get("image_model", ""))

    def _current_settings(self) -> dict:
        provider = self.provider.get().split(":")[0].strip()
        base_url = self.base_url.get().strip()
        image_model, self._image_model_fixed = normalize_image_model(
            {"provider": provider, "base_url": base_url, "image_model": self.image_model.get()}
        )
        return {
            "auto": {
                "enabled": bool(self.auto_enabled.get()),
                "delay_seconds": int(self.delay.get()),
                "retry": int(self.retry.get()),
                "idle_minutes": int(self.idle.get()),
                "late_minutes": int(self.late.get()),
                "close_after_send": bool(self.close_after.get()),
            },
            "ai": {
                "enabled": bool(self.ai_enabled.get()),
                "provider": provider,
                "base_url": base_url,
                "model": self.model.get().strip(),
                "api_key": self.api_key.get().strip(),
                "temperature": float(self.temperature.get()),
                "max_chars": int(self.max_chars.get()),
                "persona": self.persona.get().strip(),
                "image_model": image_model,
                "timeout": int(self.timeout.get()),
            },
            "ui": {"theme": self.theme_key},
        }
        return result

    def _test_generate(self) -> None:
        cfg = self._current_settings()["ai"]
        self.test_label.configure(text="正在请求模型…", fg=MUTED)

        def run() -> None:
            try:
                text = generate_message(cfg, "测试好友", "今天也在努力，随便聊两句", when=datetime.now())
                message = f"生成结果：{text}"
                error = None
            except AIError as exc:
                message, error = f"失败：{exc}", True
            except Exception as exc:  # pragma: no cover - defensive
                message, error = f"失败：{exc}", True

            def update() -> None:
                if not self.winfo_exists():
                    return
                self.test_label.configure(text=message, fg=DANGER if error else SUCCESS)

            self.after(0, update)

        threading.Thread(target=run, daemon=True, name="ai-test").start()

    def _test_connection(self) -> None:
        cfg = self._current_settings()["ai"]
        self.test_label.configure(text="正在连接口…", fg=MUTED)

        def run() -> None:
            result = probe_chat(cfg, timeout=8.0)
            if result.get("ok"):
                text = "接口可达 · {} · {} ms · {}".format(
                    result.get("status"), result.get("elapsed_ms"), result.get("model")
                )
                error = False
            else:
                text = "不通：{}（{} ms）\n{}".format(
                    result.get("detail"), result.get("elapsed_ms"), result.get("hint") or ""
                )
                error = True

            def update() -> None:
                if not self.winfo_exists():
                    return
                self.test_label.configure(text=text, fg=DANGER if error else SUCCESS)

            self.after(0, update)

        threading.Thread(target=run, daemon=True, name="ai-probe").start()

    def _save(self) -> None:
        try:
            values = self._current_settings()
            if values["ai"]["enabled"] and not values["ai"]["api_key"]:
                raise ValueError("开启 AI 前请先填写 API Key")
            if not values["ai"]["base_url"].startswith(("http://", "https://")):
                raise ValueError("接口地址需以 http:// 或 https:// 开头")
            if values["ai"]["max_chars"] < 8:
                raise ValueError("文案上限至少 8 个字")
        except (ValueError, tk.TclError) as exc:
            messagebox.showwarning("设置未保存", str(exc), parent=self)
            return
        if self._image_model_fixed:
            fixed = values["ai"]["image_model"]
            self.image_model.set(fixed)
            messagebox.showinfo(
                "图像模型已修正",
                "「图像模型」这一栏要填模型名，不能填接口地址。\n已自动改为：{}。".format(fixed or "（留空）"),
                parent=self,
            )
        self.on_save(values)
        self.destroy()


class AICandidateDialog(tk.Toplevel):
    """Show three AI-written lines; clicking one fills the time slot's text."""

    def __init__(self, parent: tk.Misc, settings: dict, nickname: str, template: str, on_pick) -> None:
        super().__init__(parent)
        self.settings = settings_with_state(settings)
        self.nickname = nickname
        self.template = template
        self.on_pick = on_pick
        self.title("AI 文案候选")
        self.geometry("600x460")
        self.minsize(520, 380)
        self.transient(parent)
        self.grab_set()
        self.configure(bg=BG)
        banner = tk.Frame(self, bg=NAV, padx=18, pady=14)
        banner.pack(fill="x")
        tk.Label(banner, text="AI 文案候选", bg=NAV, fg=paint("#ffffff"), font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w")
        tk.Label(
            banner, text=f"给「{nickname}」写三条，点一条就填进时间点。", bg=NAV, fg=paint("#b5c6dd"),
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", pady=(3, 0))
        self.status = tk.Label(self, text="正在让模型写…", bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 10))
        self.status.pack(anchor="w", padx=18, pady=(10, 4))
        self.list_box = tk.Frame(self, bg=BG, padx=16, pady=4)
        self.list_box.pack(fill="both", expand=True)
        footer = tk.Frame(self, bg=SURFACE, padx=16, pady=10)
        footer.pack(fill="x")
        ttk.Button(footer, text="再来一批", command=self._start, style="Soft.TButton").pack(side="left")
        ttk.Button(footer, text="关闭", command=self.destroy, style="Soft.TButton").pack(side="right")
        self.bind("<Escape>", lambda _event: self.destroy())
        self._start()

    def _start(self) -> None:
        for child in self.list_box.winfo_children():
            child.destroy()
        cfg = self.settings["ai"]
        if not str(cfg.get("api_key") or "").strip():
            self.status.configure(text="还没有填写 AI API Key，请到「自动化与 AI 设置」里配置。", fg=DANGER)
            return
        self.status.configure(text="正在让模型写…", fg=MUTED)

        def run() -> None:
            try:
                texts = generate_variants(
                    cfg, self.nickname, self.template, when=datetime.now(), count=3
                )
                error = None
            except AIError as exc:
                texts, error = [], str(exc)
            except Exception as exc:  # pragma: no cover - defensive
                texts, error = [], f"{type(exc).__name__}: {exc}"

            def done() -> None:
                if not self.winfo_exists():
                    return
                if error:
                    self.status.configure(text=f"生成失败：{error}", fg=DANGER)
                    return
                self.status.configure(text=f"共 {len(texts)} 条候选，点一条填入：", fg=SUCCESS)
                for text in texts:
                    tk.Button(
                        self.list_box, text=text, anchor="w", justify="left", wraplength=wrap_px(self, 520),
                        bg=SURFACE, activebackground=SOFT, fg=INK, relief="flat", bd=0,
                        highlightbackground=LINE, highlightthickness=1, padx=12, pady=10,
                        font=("Microsoft YaHei UI", 11), cursor="hand2",
                        command=lambda value=text: self._pick(value),
                    ).pack(fill="x", pady=4)

            self.after(0, done)

        threading.Thread(target=run, daemon=True, name="ai-candidates").start()

    def _pick(self, text: str) -> None:
        self.on_pick(text)
        self.destroy()


class StickerDialog(tk.Toplevel):
    """Manage the sticker library: import pictures, delete them, or let AI draw one."""

    def __init__(self, parent: tk.Misc, state: dict, on_change, settings: dict) -> None:
        super().__init__(parent)
        self.state = state
        self.on_change = on_change
        self.settings = settings
        self.photos: dict[str, tk.PhotoImage] = {}
        self.selected: set[str] = set()
        self.cards: dict[str, tk.Frame] = {}
        self.title("表情库管理")
        scale = ui_scale(self)
        self.geometry(f"{int(840 * scale)}x{int(720 * scale)}")
        self.minsize(int(720 * scale), int(600 * scale))
        self.transient(parent)
        self.grab_set()
        self.configure(bg=BG)

        header = tk.Frame(self, bg=NAV)
        header.pack(fill="x")
        tk.Label(header, text="表情库", bg=NAV, fg=paint("#ffffff"), font=("Microsoft YaHei UI", 16, "bold")).pack(anchor="w", padx=20, pady=(14, 2))
        tk.Label(
            header,
            text="这里决定程序能发哪些图：可以导入本地 PNG / GIF，也可以让 AI 画一张。选好的图片在好友的时间点里点「选择表情」使用。",
            bg=NAV,
            fg=paint("#b9c8dd"),
            justify="left",
            wraplength=wrap_px(self, 760),
            font=("Microsoft YaHei UI", 10),
        ).pack(anchor="w", padx=20, pady=(0, 14))

        toolbar = tk.Frame(self, bg=BG, padx=18, pady=10)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="＋  添加图片", command=self._add_files, style="LargeSoft.TButton").pack(side="left")
        ttk.Button(toolbar, text="从文件夹导入", command=self._add_folder, style="LargeSoft.TButton").pack(side="left", padx=8)
        ttk.Button(toolbar, text="删除选中", command=self._delete_selected, style="LargeDanger.TButton").pack(side="left")
        self.count_label = tk.Label(toolbar, text="", bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 10))
        self.count_label.pack(side="right")

        self.scroll = Scrollable(self, bg=BG)
        self.scroll.pack(fill="both", expand=True, padx=18, pady=(0, 8))
        self.grid_box = self.scroll.inner
        for column in range(5):
            self.grid_box.grid_columnconfigure(column, weight=1, uniform="sticker")

        ai = tk.Frame(self, bg=SURFACE, padx=16, pady=12, highlightbackground=LINE, highlightthickness=1)
        ai.pack(fill="x", padx=18, pady=(0, 10))
        head = tk.Frame(ai, bg=SURFACE)
        head.pack(fill="x")
        tk.Label(head, text="AI 生成表情", bg=SURFACE, fg=INK, font=("Microsoft YaHei UI", 12, "bold")).pack(side="left")
        ai_cfg = settings_with_state(self.settings)["ai"]
        model, _fixed = normalize_image_model(ai_cfg)
        provider = ai_cfg.get("provider", "")
        tk.Label(
            head,
            text=f"图像模型：{model or '未填（去设置里填 cogview-3-flash）'}",
            bg=SURFACE,
            fg=MUTED,
            font=("Microsoft YaHei UI", 9),
        ).pack(side="right")
        row = tk.Frame(ai, bg=SURFACE)
        row.pack(fill="x", pady=(8, 6))
        self.prompt = tk.StringVar(value="一只戴墨镜的柴犬比心，扁平插画")
        entry = tk.Entry(
            row, textvariable=self.prompt, relief="flat", bg=SURFACE, fg=INK,
            font=("Microsoft YaHei UI", 11), highlightthickness=1, highlightbackground=LINE,
            highlightcolor=ACCENT, insertbackground=INK,
        )
        entry.pack(side="left", fill="x", expand=True, ipady=6)
        self.generate_button = ttk.Button(row, text="AI 画一张", command=self._generate, style="Primary.TButton")
        self.generate_button.pack(side="left", padx=(10, 0))
        self.status_label = tk.Label(
            ai,
            text="画面会存进本机表情库；生成需要「自动化与 AI 设置」里填好 API Key 和图像模型。",
            bg=SURFACE, fg=MUTED, justify="left", wraplength=wrap_px(self, 760), font=("Microsoft YaHei UI", 9),
        )
        self.status_label.pack(anchor="w")

        footer = tk.Frame(self, bg=SURFACE, padx=16, pady=10)
        footer.pack(side="bottom", fill="x")
        ttk.Button(footer, text="完成", command=self.destroy, style="LargePrimary.TButton").pack(side="right")
        tk.Label(footer, text="改动即时保存到本机。", bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.bind("<Escape>", lambda _event: self.destroy())
        self.after(150, self.reload)

    # ---------------------------------------------------------------- library
    def sticker_paths(self) -> list[str]:
        return [str(path) for path in self.state.get("stickers", [])]

    def reload(self) -> None:
        for widget in self.grid_box.winfo_children():
            widget.destroy()
        self.photos.clear()
        self.cards.clear()
        paths = self.sticker_paths()
        self.count_label.configure(text=f"共 {len(paths)} 张 · 已选 {len(self.selected)} 张")
        if not paths:
            empty = tk.Frame(self.grid_box, bg=BG)
            empty.grid(row=0, column=0, columnspan=6, pady=40)
            tk.Label(empty, text="表情库还是空的", bg=BG, fg=INK, font=("Microsoft YaHei UI", 13, "bold")).pack()
            tk.Label(
                empty,
                text="点上面的「添加图片」，或者直接把本地图拖进来——再或者让 AI 画一张。",
                bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 10),
            ).pack(pady=(6, 0))
            return
        columns = 5
        for index, path in enumerate(paths):
            card = self._card_for(path)
            card.grid(row=index // columns, column=index % columns, padx=int(6 * ui_scale(self)), pady=int(6 * ui_scale(self)), sticky="nsew")

    def _card_for(self, path: str) -> tk.Frame:
        name = Path(path).name
        active = path in self.selected
        scale = ui_scale(self)
        card = tk.Frame(
            self.grid_box, width=int(152 * scale), height=int(190 * scale),
            bg=paint("#f8fbff") if active else SURFACE, padx=int(8 * scale), pady=int(8 * scale),
            highlightbackground=ACCENT if active else LINE, highlightthickness=2 if active else 1,
        )
        card.grid_propagate(False)
        image = self._thumb(path)
        if image is not None:
            thumb = tk.Label(card, image=image, bg=paint("#f8fbff") if active else SURFACE, cursor="hand2")
            thumb.pack()
            thumb.bind("<Button-1>", lambda _event, value=path: self._toggle(value))
        else:
            tk.Label(card, text="无法预览", bg=paint("#f8fbff") if active else SURFACE, fg=DANGER,
                     font=("Microsoft YaHei UI", 9), height=4).pack()
        tk.Label(
            card, text=name if len(name) <= 16 else name[:12] + "…", bg=paint("#f8fbff") if active else SURFACE,
            fg=INK, font=("Microsoft YaHei UI", 9), wraplength=int(110 * ui_scale(self)),
        ).pack(pady=(4, 0))
        badge = "AI 生成" if name.startswith("ai-") else "本地图片"
        tk.Label(card, text=badge, bg=paint("#f8fbff") if active else SURFACE, fg=INFO if name.startswith("ai-") else MUTED,
                 font=("Microsoft YaHei UI", 8)).pack()
        for widget in (card,) + tuple(card.winfo_children()):
            widget.bind("<Button-1>", lambda _event, value=path: self._toggle(value))
        self.cards[path] = card
        return card

    def _thumb(self, path: str) -> tk.PhotoImage | None:
        try:
            image = tk.PhotoImage(file=path)
        except tk.TclError:
            return None
        factor = max(1, int(max(image.width(), image.height()) / (88 * ui_scale(self))))
        if factor > 1:
            image = image.subsample(factor, factor)
        self.photos[path] = image
        return image

    def _toggle(self, path: str) -> None:
        if path in self.selected:
            self.selected.discard(path)
        else:
            self.selected.add(path)
        self.reload()

    def _add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            parent=self, title="选择表情图片",
            filetypes=[("图片", "*.png *.gif"), ("所有文件", "*.*")],
        )
        if not paths:
            return
        added = 0
        for raw in paths:
            if add_sticker_path(self, str(raw)):
                added += 1
        self.on_change()
        self.status_label.configure(text=f"已加入 {added} 张图片。", fg=SUCCESS if added else MUTED)
        self.reload()

    def _add_folder(self) -> None:
        folder = filedialog.askdirectory(parent=self, title="选择表情包文件夹")
        if not folder:
            return
        found = [str(path) for path in Path(folder).rglob("*") if path.suffix.lower() in {".png", ".gif"}]
        added = 0
        for path in found:
            if add_sticker_path(self, path):
                added += 1
        self.on_change()
        self.status_label.configure(text=f"从文件夹加入 {added} 张（跳过重复项）。", fg=SUCCESS if added else MUTED)
        self.reload()

    def _delete_selected(self) -> None:
        if not self.selected:
            messagebox.showinfo("先选表情", "点一下图片选中它，再点「删除选中」。", parent=self)
            return
        if not messagebox.askyesno("删除表情", f"从表情库移除 {len(self.selected)} 张？（不会删除你本地的原图文件）", parent=self):
            return
        targets = set(self.selected)
        self.state["stickers"] = [path for path in self.sticker_paths() if path not in targets]
        self.selected.clear()
        self.on_change()
        self.reload()
        self.status_label.configure(text="已从表情库移除。", fg=MUTED)

    # ------------------------------------------------------------------- ai
    def _generate(self) -> None:
        text = self.prompt.get().strip()
        if not text:
            messagebox.showinfo("写一句描述", "告诉 AI 想画什么：主体、动作、表情、配色都可以。", parent=self)
            return
        cfg = settings_with_state(self.settings)["ai"]
        if not str(cfg.get("api_key") or "").strip():
            messagebox.showwarning(
                "还没有 AI Key",
                "请先在「自动化与 AI 设置」里填写 API Key 和图像模型（例如智谱 cogview-3-flash）。",
                parent=self,
            )
            return
        self.generate_button.configure(state="disabled")
        self.status_label.configure(text="正在让 AI 画图，通常 10-30 秒…", fg=MUTED)

        def run() -> None:
            try:
                data = generate_image(cfg, sticker_prompt(text))
                saved = save_generated_sticker(data)
                error = None
            except (AIError, OSError, ValueError) as exc:
                saved, error = None, str(exc)
            except Exception as exc:  # pragma: no cover - defensive
                saved, error = None, f"{type(exc).__name__}: {exc}"

            def done() -> None:
                if not self.winfo_exists():
                    return
                self.generate_button.configure(state="normal")
                if error or saved is None:
                    self.status_label.configure(text=f"生成失败：{error}", fg=DANGER)
                    return
                self.state["stickers"] = self.sticker_paths() + [str(saved)]
                self.on_change()
                self.reload()
                self.status_label.configure(text=f"已生成并加入表情库：{Path(saved).name}", fg=SUCCESS)

            self.after(0, done)

        threading.Thread(target=run, daemon=True, name="sticker-ai").start()


def add_sticker_path(dialog: StickerDialog, path: str) -> bool:
    """Add one sticker path to the dialog state when it is new and readable."""
    if not path or not Path(path).exists():
        return False
    if path in dialog.sticker_paths():
        return False
    try:
        tk.PhotoImage(file=path).width()
    except tk.TclError:
        return False
    dialog.state.setdefault("stickers", []).append(path)
    return True


def save_generated_sticker(data: bytes) -> Path:
    """Persist AI-generated image bytes under the app data directory."""
    target_dir = Path(APP_DIR) / "stickers"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"ai-{datetime.now():%Y%m%d-%H%M%S}.png"
    target.write_bytes(data)
    try:
        tk.PhotoImage(file=str(target)).width()
    except tk.TclError as exc:
        target.unlink(missing_ok=True)
        raise ValueError("AI 返回的图片格式无法识别") from exc
    return target


class Scrollable(tk.Frame):
    """Scroll area that also answers to the mouse wheel, keys and drag."""

    def __init__(self, parent: tk.Misc, **kwargs) -> None:
        background = kwargs.pop("bg", BG)
        self._onscroll = kwargs.pop("onscroll", None)
        super().__init__(parent, **kwargs)
        self.canvas = tk.Canvas(self, highlightthickness=0, bg=background, yscrollincrement=22)
        self.inner = tk.Frame(self.canvas, bg=background)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.scrollbar = scrollbar
        self.window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self._sync_scroll)
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.inner.bind("<Configure>", lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(self.window, width=event.width))
        self._drag_last: int | None = None
        self.bind_all("<MouseWheel>", self._on_wheel, add="+")

    def _sync_scroll(self, first: str, last: str) -> None:
        try:
            self.scrollbar.set(first, last)
        except tk.TclError:
            return
        if self._onscroll is not None:
            try:
                self._onscroll(float(first), float(last))
            except (tk.TclError, ValueError):
                pass

    # ------------------------------------------------------------ wheel / keys
    def _owns(self, widget: tk.Misc | None) -> bool:
        while widget is not None:
            if widget is self:
                return True
            if isinstance(widget, (tk.Tk, tk.Toplevel)):
                return False
            widget = widget.master
        return False

    def _on_wheel(self, event: tk.Event) -> None:
        try:
            if not self.winfo_exists():
                return
            target = self.winfo_containing(event.x_root, event.y_root)
        except tk.TclError:
            return
        if not self._owns(target):
            return
        step = int(event.delta / 120 * 3) or (1 if event.delta > 0 else -1)
        self.scroll_units(-step)

    def scroll_units(self, units: int) -> None:
        try:
            self.canvas.yview_scroll(units, "units")
        except tk.TclError:
            pass

    def scroll_page(self, direction: int) -> None:
        try:
            self.canvas.yview_scroll(direction, "pages")
        except tk.TclError:
            pass

    def scroll_to(self, fraction: float) -> None:
        try:
            self.canvas.yview_moveto(fraction)
        except tk.TclError:
            pass

    # --------------------------------------------------------------- drag mode
    def begin_drag(self, y_root: int) -> None:
        self._drag_last = y_root

    def drag_to(self, y_root: int) -> None:
        if self._drag_last is None:
            return
        delta = y_root - self._drag_last
        self._drag_last = y_root
        if abs(delta) >= 1:
            self.scroll_units(int(-delta / 22))

    def end_drag(self) -> None:
        self._drag_last = None


AVATAR_COLORS = (
    ("#e8f0fb", "#1b4a75"),
    ("#fdeee7", "#a8341b"),
    ("#eef7f2", "#186046"),
    ("#fbf4e8", "#8a5c17"),
    ("#f1ecfb", "#4a3d7a"),
    ("#e6f4f6", "#1f5d66"),
)


def avatar_colors(name: str) -> tuple[str, str]:
    """Deterministic pastel pair so every friend gets a recognisable dot."""
    seed = sum(ord(char) for char in name) if name else 0
    return AVATAR_COLORS[seed % len(AVATAR_COLORS)]


class FriendCard(tk.Frame):
    def __init__(self, parent: tk.Misc, friend: Friend, main: "MainWindow", spark: dict | None = None) -> None:
        super().__init__(parent, bg=SURFACE, highlightthickness=0)
        self.friend, self.main, self.spark = friend, main, spark or {}
        self.expanded = True
        self.build()
        self.bind("<Configure>", self._resize)
        self._bind_drag()

    def build(self) -> None:
        header = tk.Frame(self, bg=SURFACE, padx=16, pady=12)
        header.pack(fill="x")
        identity = tk.Frame(header, bg=SURFACE)
        display_name = self.friend.name or "抖音号 {}".format(self.friend.douyin_id or "未填")
        avatar = tk.Canvas(identity, width=42, height=42, bg=SURFACE, highlightthickness=0)
        avatar.pack(side="left", padx=(0, 12))
        pair_bg, pair_fg = avatar_colors(display_name)
        avatar.create_oval(2, 2, 40, 40, fill=paint(pair_bg), outline="")
        avatar.create_text(
            21, 21, text=(display_name.strip()[:1] or "火"),
            fill=paint(pair_fg) if theme.polarity() == "light" else "#ffffff",
            font=("Microsoft YaHei UI", 15, "bold"),
        )
        titles = tk.Frame(identity, bg=SURFACE)
        titles.pack(side="left", fill="x", expand=True)
        self.name_label = tk.Label(titles, text=display_name, bg=SURFACE, fg=INK, anchor="w", justify="left", font=("Microsoft YaHei UI", 13, "bold"))
        self.name_label.pack(fill="x")
        self.sub_label = tk.Label(
            titles, text=("抖音号 " + self.friend.douyin_id) if self.friend.douyin_id else "抖音好友",
            bg=SURFACE, fg=MUTED, anchor="w", font=("Microsoft YaHei UI", 8),
        )
        self.sub_label.pack(fill="x", pady=(1, 0))
        actions = tk.Frame(header, bg=SURFACE)
        actions.pack(side="right")
        identity.pack(side="left", fill="x", expand=True)
        self.enabled_var = tk.BooleanVar(value=self.friend.enabled)
        self.enable_chk = tk.Checkbutton(
            actions,
            text="启用",
            variable=self.enabled_var,
            command=self._on_enable_toggle,
            bg=SURFACE,
            activebackground=SURFACE,
            selectcolor=SURFACE,
            fg=SUCCESS if self.friend.enabled else MUTED,
            font=("Microsoft YaHei UI", 9, "bold"),
            bd=0,
            highlightthickness=0,
            cursor="hand2",
        )
        self.enable_chk.pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="编辑", width=4, command=lambda: self.main.edit_friend(self.friend), style="CardSoft.TButton").pack(side="left", padx=3)
        ttk.Button(actions, text="删除", width=4, command=lambda: self.main.delete_friend(self.friend), style="CardDanger.TButton").pack(side="left", padx=3)

        self.content = tk.Frame(self, bg=SURFACE, padx=16)
        self.content.pack(fill="x", pady=(0, 12))
        for widget in (header, identity, avatar, titles, self.name_label, self.sub_label):
            widget.bind("<Double-Button-1>", self._toggle_expand)
        if not self.friend.name.strip():
            status, status_color, glyph = "待补昵称 · 点编辑补齐", WARNING, "✎"
        else:
            status = "已暂停" if not self.friend.enabled else "待确认提醒" if not self.friend.consent else "已启用"
            status_color = MUTED if not self.friend.enabled else WARNING if not self.friend.consent else SUCCESS
            glyph = "○" if not self.friend.enabled else "●"
        pills = tk.Frame(self.content, bg=SURFACE)
        pills.pack(anchor="w", pady=(0, 8))
        tk.Label(
            pills, text=f"{glyph}  {status}", bg=SURFACE, fg=status_color,
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(side="left")
        if self.friend.douyin_id:
            tk.Label(
                pills, text="  ·  抖音号 {}".format(self.friend.douyin_id), bg=SURFACE, fg=MUTED,
                font=("Microsoft YaHei UI", 9),
            ).pack(side="left")
        spark_key = self.spark.get("key")
        if spark_key and spark_key != "off":
            label, color, _chip_bg = SPARK_STYLE.get(spark_key, SPARK_STYLE["none"])
            if self.spark.get("sent"):
                label = f"{label} {self.spark['sent']}/{self.spark['total']}"
            tk.Label(
                pills, text="  ·  " + label, bg=SURFACE, fg=color,
                font=("Microsoft YaHei UI", 9, "bold"),
            ).pack(side="left")
        streak = streak_days(self.friend, self.main.state.get("events", []), datetime.now().date())
        if streak >= 2:
            tk.Label(
                pills, text=f"🔥  连续 {streak} 天", bg=SURFACE, fg=WARNING,
                font=("Microsoft YaHei UI", 9, "bold"),
            ).pack(side="left", padx=(10, 0))
        auto_text = "自动发送：已授权，到点直接发出" if self.friend.auto_approved else "自动发送：首次发送前会确认一次"
        if not self.friend.consent:
            auto_text = "自动发送：需先勾选好友提醒授权"
        tk.Label(self.content, text="⚡  " + auto_text, bg=SURFACE, fg=INFO if self.friend.auto_approved else MUTED,
                 anchor="w", font=("Microsoft YaHei UI", 9)).pack(fill="x", pady=(0, 6))
        times = ", ".join(slot.at for slot in self.friend.slots)
        self.times_label = tk.Label(self.content, text="◷  每日提醒   {}".format(times or "未设置时间"), bg=SURFACE, fg=MUTED, anchor="w", justify="left", font=("Microsoft YaHei UI", 9))
        self.times_label.pack(fill="x", anchor="w", pady=(0, 8))
        tk.Label(self.content, text="▸  今日计划", bg=SURFACE, fg=INK, font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w")
        for index, slot in enumerate(sorted(self.friend.slots, key=lambda item: item.at)):
            line = tk.Frame(self.content, bg=SURFACE)
            line.pack(fill="x", pady=(6 if index == 0 else 2, 2))
            tk.Label(line, text=slot.at, width=8, anchor="w", bg=SURFACE, fg=INK, font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
            state = self.spark.get("slots", {}).get(slot.slot_id)
            if state:
                text, tone = SLOT_STYLE.get(state, SLOT_STYLE["pending"])
                tk.Label(line, text=text, bg=SURFACE, fg=tone, width=5, anchor="e", font=("Microsoft YaHei UI", 9, "bold")).pack(side="right")
            tk.Label(line, text=describe_content(slot.mode, slot.template, slot.emoji_path), bg=SURFACE, fg=MUTED, anchor="w", justify="left", wraplength=wrap_px(self, 460)).pack(side="left", fill="x", expand=True)
        if not self.friend.slots:
            tk.Label(self.content, text="尚未配置提醒时间", bg=SURFACE, fg=MUTED).pack(anchor="w")

    def _on_enable_toggle(self) -> None:
        value = self.enabled_var.get()
        self.enable_chk.configure(fg=SUCCESS if value else MUTED)
        self.main.set_friend_enabled(self.friend, value)

    def _toggle_expand(self, _event: tk.Event | None = None) -> None:
        if self.expanded:
            self.content.pack_forget()
        else:
            self.content.pack(fill="x", pady=(0, 12))
        self.expanded = not self.expanded

    def _bind_drag(self) -> None:
        widgets = [self, self.content]

        def walk(widget: tk.Misc) -> None:
            for child in widget.winfo_children():
                if isinstance(child, ttk.Widget):
                    continue
                if isinstance(child, (tk.Frame, tk.Label)):
                    widgets.append(child)
                walk(child)

        walk(self)
        for widget in widgets:
            widget.bind("<Button-1>", self._drag_start, add="+")
            widget.bind("<B1-Motion>", self._drag_move, add="+")
            widget.bind("<ButtonRelease-1>", self._drag_end, add="+")

    def _drag_start(self, event: tk.Event) -> None:
        self.main.friend_scroll.begin_drag(event.y_root)

    def _drag_move(self, event: tk.Event) -> None:
        self.main.friend_scroll.drag_to(event.y_root)

    def _drag_end(self, _event: tk.Event) -> None:
        self.main.friend_scroll.end_drag()

    def _resize(self, event: tk.Event) -> None:
        if event.widget is not self:
            return
        self.name_label.configure(wraplength=max(90, event.width - 250))
        self.times_label.configure(wraplength=max(140, event.width - 50))


SENT_STATUSES = {"sent", "auto_sent"}
PENDING_STATUSES = {
    "reminded",
    "pending",
    "auto_queued",
    "auto_sending",
    "auto_needs_confirm",
    "needs_review",
}
FAILED_STATUSES = {"auto_failed"}
SKIPPED_STATUSES = {"skipped", "auto_skipped", "skipped_limit"}

SPARK_STYLE = {
    "sent": ("今日已续", paint("#1c6b4d"), paint("#eef7f2")),
    "pending": ("今日未续", paint("#96651c"), paint("#fbf4e8")),
    "missed": ("今日漏发", DANGER, paint("#fbeeef")),
    "failed": ("发送失败", DANGER, paint("#fbeeef")),
    "off": ("未启用", paint("#6b7789"), paint("#f1f4f8")),
    "none": ("未设时间", paint("#6b7789"), paint("#f1f4f8")),
}

SLOT_STYLE = {
    "sent": ("已发", paint("#1c6b4d")),
    "pending": ("待发", paint("#96651c")),
    "missed": ("未发", DANGER),
    "failed": ("失败", DANGER),
    "off": ("停用", paint("#6b7789")),
}


def streak_days(friend: Friend, events: list[dict], today: date) -> int:
    """How many days in a row this friend's spark was kept (today counts if already sent)."""
    dates = {
        str(event.get("date"))
        for event in events
        if event.get("friend_id") == friend.friend_id and event.get("status") in SENT_STATUSES
    }
    if not dates:
        return 0
    day = today if today.isoformat() in dates else today - timedelta(days=1)
    count = 0
    while day.isoformat() in dates:
        count += 1
        day -= timedelta(days=1)
    return count


def spark_history(friend: Friend, events: list[dict], today: date, days: int = 14) -> list[dict]:
    """The last `days` days of one friend's spark, oldest first."""
    history = []
    for offset in range(days - 1, -1, -1):
        day = (today - timedelta(days=offset)).isoformat()
        day_events = [
            event
            for event in events
            if event.get("date") == day and event.get("friend_id") == friend.friend_id
        ]
        history.append(
            {
                "date": day,
                "plan": len(day_events),
                "sent": sum(1 for event in day_events if event.get("status") in SENT_STATUSES),
                "skipped": sum(1 for event in day_events if event.get("status") in SKIPPED_STATUSES),
            }
        )
    return history


def spark_rows(friends: list[Friend], events: list[dict], now: datetime) -> list[dict]:
    """Summarise, per friend, whether today's spark has been kept."""
    today = now.date().isoformat()
    now_time = now.time()
    rows: list[dict] = []
    for friend in friends:
        today_events = [
            event
            for event in events
            if event.get("date") == today and event.get("friend_id") == friend.friend_id
        ]
        slots = [slot for slot in friend.slots if slot.enabled]
        slot_states: dict[str, str] = {}
        for slot in slots:
            matches = [event for event in today_events if event.get("slot_id") == slot.slot_id]
            state = "pending"
            if any(event.get("status") in SENT_STATUSES for event in matches):
                state = "sent"
            elif any(event.get("status") in FAILED_STATUSES for event in matches):
                state = "failed"
            elif any(event.get("status") in PENDING_STATUSES for event in matches):
                state = "pending"
            elif matches and all(event.get("status") in SKIPPED_STATUSES for event in matches):
                state = "missed"
            else:
                try:
                    if slot.parsed_time() <= now_time:
                        state = "missed"
                except ValueError:
                    state = "missed"
            slot_states[slot.slot_id] = state
        sent_count = sum(1 for state in slot_states.values() if state == "sent")
        failed = any(state == "failed" for state in slot_states.values())
        pending = any(state == "pending" for state in slot_states.values())
        if not friend.enabled:
            key = "off"
        elif not slots:
            key = "none"
        elif sent_count:
            key = "sent"
        elif failed:
            key = "failed"
        elif pending:
            key = "pending"
        else:
            key = "missed"
        sent_events = [event for event in today_events if event.get("status") in SENT_STATUSES]
        last_at = ""
        last_detail = ""
        if sent_events:
            newest = max(sent_events, key=lambda event: str(event.get("due_at", "")))
            last_at = str(newest.get("due_at", ""))
            last_detail = str(newest.get("detail") or "")
        rows.append(
            {
                "friend": friend,
                "key": key,
                "sent": sent_count,
                "total": len(slots),
                "slots": slot_states,
                "last_at": last_at,
                "last_detail": last_detail,
            }
        )
    return rows


class EnablePickerDialog(tk.Toplevel):
    """Pick one or several friends that should be enabled."""

    def __init__(self, parent: tk.Misc, friends: list[Friend], on_apply) -> None:
        super().__init__(parent)
        self.on_apply = on_apply
        self.vars: dict[str, tk.BooleanVar] = {}
        self.rows: list[tuple[tk.Checkbutton, tk.BooleanVar]] = []
        self.title("批量启用好友")
        self.configure(bg=BG)
        self.bind("<Escape>", lambda _event: self.destroy())
        self.transient(parent)
        width = 640
        height = max(400, min(660, 250 + 58 * len(friends)))
        self.geometry(f"{width}x{height}+{max(30, parent.winfo_screenwidth() // 2 - width // 2)}+{max(30, parent.winfo_screenheight() // 3 - height // 3)}")
        self.minsize(520, 380)
        banner = tk.Frame(self, bg=NAV, padx=20, pady=16)
        banner.pack(fill="x")
        tk.Frame(self, bg=paint("#2a3f58"), height=1).pack(fill="x")
        tk.Label(banner, text="批量启用 / 停用", bg=NAV, fg=ON_NAV, font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")
        tk.Label(
            banner,
            text="勾选即启用，取消勾选即停用；只勾一位就是单独启用。选好后点右下角「确认启用」生效。",
            bg=NAV, fg=paint("#8fa4bd"), font=("Microsoft YaHei UI", 9), wraplength=wrap_px(self, 560), justify="left",
        ).pack(anchor="w", pady=(4, 0))
        tools = tk.Frame(self, bg=BG, padx=18, pady=8)
        tools.pack(fill="x")
        ttk.Button(tools, text="全选", command=lambda: self._set_all(True), style="Soft.TButton").pack(side="left", padx=(0, 6))
        ttk.Button(tools, text="全不选", command=lambda: self._set_all(False), style="Soft.TButton").pack(side="left")
        self.count_label = tk.Label(tools, text="", bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 9))
        self.count_label.pack(side="right")
        footer = tk.Frame(self, bg=BG, padx=18, pady=12)
        footer.pack(side="bottom", fill="x")
        self.apply_button = ttk.Button(footer, text="确认启用", command=self._apply, style="LargePrimary.TButton")
        self.apply_button.pack(side="right")
        ttk.Button(footer, text="取消", command=self.destroy, style="Soft.TButton").pack(side="right", padx=(0, 10))
        box = Scrollable(self, bg=BG)
        box.pack(fill="both", expand=True, padx=14, pady=(0, 6))
        if not friends:
            tk.Label(box.inner, text="还没有好友，先点「＋ 新增好友」。", bg=BG, fg=MUTED, padx=16, pady=16).pack(anchor="w")
        for friend in friends:
            var = tk.BooleanVar(value=friend.enabled)
            self.vars[friend.friend_id] = var
            row = tk.Frame(box.inner, bg=SURFACE, highlightbackground=LINE, highlightthickness=1, padx=12, pady=10)
            row.pack(fill="x", pady=(0, 8))
            check = tk.Checkbutton(
                row, text=friend.name, variable=var, bg=SURFACE, activebackground=SURFACE,
                fg=INK if friend.enabled else MUTED, font=("Microsoft YaHei UI", 11, "bold"),
                anchor="w", highlightthickness=0, selectcolor=SURFACE,
            )
            check.pack(side="left", fill="x", expand=True)
            times = ", ".join(slot.at for slot in friend.slots) or "未设置时间"
            note = tk.Label(row, text=times, bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 9))
            note.pack(side="right")
            check.configure(command=lambda c=check, v=var: self._sync_row(c, v))
            self.rows.append((check, var))
        self._update_count()

    def _set_all(self, value: bool) -> None:
        for check, var in self.rows:
            var.set(value)
            self._sync_row(check, var)
        self._update_count()

    def _sync_row(self, check: tk.Checkbutton, var: tk.BooleanVar) -> None:
        check.configure(fg=INK if var.get() else MUTED)
        self._update_count()

    def _update_count(self) -> None:
        total = len(self.vars)
        chosen = sum(1 for var in self.vars.values() if var.get())
        self.count_label.configure(text=f"共 {total} 位 · 勾选 {chosen} 位")
        if total == 0:
            self.apply_button.configure(text="确认启用")
            return
        text = f"确认启用 {chosen} 位"
        if chosen < total:
            text += f"，停用 {total - chosen} 位"
        self.apply_button.configure(text=text)

    def _apply(self) -> None:
        self.on_apply({friend_id: var.get() for friend_id, var in self.vars.items()})
        self.destroy()


class TodaySparkDialog(tk.Toplevel):
    """Today's spark status for every friend, with a one-click re-send."""

    def __init__(self, parent: "MainWindow") -> None:
        super().__init__(parent)
        self.main = parent
        self.only_missing = tk.BooleanVar(value=False)
        self.title("今日续火花")
        self.configure(bg=BG)
        self.transient(parent)
        width, height = 820, min(720, max(520, parent.winfo_screenheight() - 160))
        self.geometry(f"{width}x{height}+{max(30, parent.winfo_screenwidth() // 2 - width // 2)}+{max(30, parent.winfo_screenheight() // 4 - height // 4)}")
        banner = tk.Frame(self, bg=NAV, padx=22, pady=16)
        banner.pack(fill="x")
        tk.Frame(self, bg=paint("#2a3f58"), height=1).pack(fill="x")
        self.title_label = tk.Label(banner, text="今日续火花", bg=NAV, fg=ON_NAV, font=("Microsoft YaHei UI", 15, "bold"))
        self.title_label.pack(anchor="w")
        self.summary_label = tk.Label(banner, text="", bg=NAV, fg=paint("#8fa4bd"), font=("Microsoft YaHei UI", 10))
        self.summary_label.pack(anchor="w", pady=(4, 0))
        self.missing_label = tk.Label(banner, text="", bg=NAV, fg=paint("#f1b9c1"), font=("Microsoft YaHei UI", 10, "bold"))
        self.missing_label.pack(anchor="w", pady=(3, 0))
        tools = tk.Frame(self, bg=BG, padx=18, pady=10)
        tools.pack(fill="x")
        tk.Checkbutton(
            tools, text="只看今日未续", variable=self.only_missing, command=self.refresh,
            bg=BG, activebackground=BG, fg=INK, font=("Microsoft YaHei UI", 9), highlightthickness=0, selectcolor=SURFACE,
        ).pack(side="left")
        self.copy_button = ttk.Button(tools, text="复制今天没续的名单", command=self._copy_missing, style="Soft.TButton")
        self.copy_button.pack(side="left", padx=(10, 0))
        self.weekly_button = ttk.Button(tools, text="✨ AI 本周小结", command=self._ai_weekly, style="Soft.TButton")
        self.weekly_button.pack(side="left", padx=(8, 0))
        ttk.Button(tools, text="立即自动发送一轮", command=self.send_round, style="Primary.TButton").pack(side="right", padx=(6, 0))
        ttk.Button(tools, text="刷新", command=self.refresh, style="Soft.TButton").pack(side="right")
        self.weekly_panel = tk.Frame(self, bg=SURFACE, padx=16, pady=12, highlightbackground=LINE, highlightthickness=1)
        self.weekly_label = tk.Label(
            self.weekly_panel, text="", bg=SURFACE, fg=INK, justify="left", wraplength=wrap_px(self, 740),
            font=("Microsoft YaHei UI", 10),
        )
        self.weekly_label.pack(anchor="w")
        self.box = Scrollable(self, bg=BG)
        self.box.pack(fill="both", expand=True, padx=14)
        footer = tk.Frame(self, bg=BG, padx=18, pady=12)
        footer.pack(fill="x")
        tk.Label(
            footer, text="近 14 天：绿=已续　黄=跳过　红=漏发　灰=无计划", bg=BG, fg=MUTED,
            font=("Microsoft YaHei UI", 8),
        ).pack(side="left")
        ttk.Button(footer, text="关闭", command=self.destroy, style="Soft.TButton").pack(side="right")
        self.bind("<Escape>", lambda _event: self.destroy())
        self.refresh()

    def _copy_missing(self) -> None:
        rows = [row for row in self.main.spark_snapshot() if row["key"] in {"missed", "failed", "pending"}]

        def restore() -> None:
            try:
                if self.winfo_exists():
                    self.copy_button.configure(text="复制今天没续的名单")
            except tk.TclError:
                pass

        if not rows:
            self.copy_button.configure(text="今天都续上了 ✓")
            self.after(1800, restore)
            return
        names = "、".join(row["friend"].name for row in rows)
        try:
            self.clipboard_clear()
            self.clipboard_append(f"今日未续：{names}")
        except tk.TclError:
            return
        self.copy_button.configure(text=f"已复制 {len(rows)} 位")
        self.after(1800, restore)

    def _ai_weekly(self) -> None:
        events = self.main.state.get("events", [])
        today = datetime.now().date()
        rows = self.main.spark_snapshot()
        label_map = {
            "sent": "今天已续", "missed": "今天漏了", "failed": "今天发送失败",
            "pending": "今天还没到点", "off": "已暂停提醒", "none": "今天没有计划",
        }
        lines = []
        for row in rows:
            friend = row["friend"]
            missed_week = sum(
                1 for item in spark_history(friend, events, today, days=7)
                if item["plan"] and not item["sent"] and not item["skipped"]
            )
            lines.append(
                f"{friend.name}：{label_map.get(row['key'], row['key'])}，连续 {streak_days(friend, events, today)} 天，"
                f"近 7 天漏发 {missed_week} 次"
            )
        if not lines:
            self.weekly_label.configure(text="还没有好友，先添加一位再来看看本周小结。", fg=MUTED)
            self.weekly_panel.pack(fill="x", padx=18, pady=(0, 8))
            return
        kept = sum(1 for row in rows if row["key"] == "sent")
        cfg = settings_with_state(self.main.settings)["ai"]
        if not str(cfg.get("api_key") or "").strip():
            self.weekly_label.configure(text="还没有填写 AI API Key，请先到「自动化与 AI 设置」配置。", fg=DANGER)
            self.weekly_panel.pack(fill="x", padx=18, pady=(0, 8))
            return
        self.weekly_panel.pack(fill="x", padx=18, pady=(0, 8))
        self.weekly_label.configure(text="正在让 AI 复盘这一周…", fg=MUTED)
        self.weekly_button.configure(state="disabled")
        system = (
            "你是用户的私人助理，帮他复盘抖音续火花的情况。用中文口语，共 4 句以内："
            "第一句总结完成情况，第二句点名最容易漏的那位，第三句给一个具体可执行的小建议。"
            "不要用 Markdown 符号，不要编造没有的数据。"
        )
        user = (
            f"今天日期：{today.isoformat()}；今天已续 {kept} 位 / 共 {len(rows)} 位。\n"
            + "\n".join(lines)
        )

        def run() -> None:
            try:
                text = chat_text(cfg, system, user, max_tokens=400)
                error = None
            except AIError as exc:
                text, error = "", str(exc)
            except Exception as exc:  # pragma: no cover - defensive
                text, error = "", f"{type(exc).__name__}: {exc}"

            def done() -> None:
                if not self.winfo_exists():
                    return
                self.weekly_button.configure(state="normal")
                if error:
                    self.weekly_label.configure(text=f"生成失败：{error}", fg=DANGER)
                    return
                self.weekly_label.configure(text=f"本周小结（AI）：{text}", fg=INK)

            self.after(0, done)

        threading.Thread(target=run, daemon=True, name="ai-weekly").start()

    def refresh(self) -> None:
        rows = self.main.spark_snapshot()
        for child in self.box.inner.winfo_children():
            child.destroy()
        order = {"missed": 0, "failed": 1, "pending": 2, "sent": 3, "off": 4, "none": 5}
        rows = sorted(rows, key=lambda row: (order.get(row["key"], 9), row["friend"].name))
        total = len(rows)
        kept = [row for row in rows if row["key"] == "sent"]
        unfinished = [row for row in rows if row["key"] in {"missed", "failed", "pending"}]
        inactive = [row for row in rows if row["key"] in {"off", "none"}]
        if self.only_missing.get():
            rows = list(unfinished)
        summary = f"{datetime.now().strftime('%Y-%m-%d')} · 已续 {len(kept)} 位 / 共 {total} 位"
        self.summary_label.configure(text=summary)
        if unfinished:
            names = "、".join(row["friend"].name for row in unfinished[:3])
            if len(unfinished) > 3:
                names += f" 等 {len(unfinished)} 位"
            self.missing_label.configure(text=f"今天还没续：{names}", fg=paint("#f1b9c1"))
        else:
            self.missing_label.configure(text="今天全部已续上 ✓", fg=paint("#a9d9bd"))
        if not rows:
            tk.Label(self.box.inner, text="今天还没有需要续火花的好友。", bg=BG, fg=MUTED, padx=16, pady=20).pack(anchor="w")

        def section(title: str, color: str, count: int) -> None:
            tk.Label(
                self.box.inner, text=f"{title} · {count} 位",
                bg=BG, fg=color, font=("Microsoft YaHei UI", 10, "bold"), padx=6,
            ).pack(anchor="w", pady=(8, 4))

        groups = []
        if unfinished and not self.only_missing.get():
            groups.append(("今日还没续", DANGER, unfinished))
        if self.only_missing.get():
            groups.append(("今日还没续", DANGER, unfinished))
        if not self.only_missing.get():
            if kept:
                groups.append(("今日已续", paint("#1c6b4d"), kept))
            if inactive:
                groups.append(("未启用 / 未设时间", paint("#6b7789"), inactive))
        for title, color, members in groups:
            if members:
                section(title, color, len(members))
                for row in members:
                    self._build_row(row)

    def _build_row(self, row: dict) -> None:
        friend: Friend = row["friend"]
        label, color, chip_bg = SPARK_STYLE.get(row["key"], SPARK_STYLE["none"])
        card = tk.Frame(self.box.inner, bg=SURFACE, highlightbackground=LINE, highlightthickness=1, padx=14, pady=12)
        card.pack(fill="x", pady=(0, 10))
        head = tk.Frame(card, bg=SURFACE)
        head.pack(fill="x")
        tk.Label(head, text=friend.name, bg=SURFACE, fg=INK, font=("Microsoft YaHei UI", 12, "bold")).pack(side="left")
        tk.Label(
            head, text=label, bg=SURFACE, fg=color, padx=9, pady=2,
            highlightbackground=chip_bg, highlightthickness=1, font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(side="left", padx=(10, 0))
        ttk.Button(head, text="编辑", command=lambda item=friend: self.main.edit_friend(item), style="Soft.TButton").pack(side="right")
        meta = f"今日计划 {row['total']} 个 · 已发 {row['sent']} 个"
        if row["last_at"]:
            meta += f" · 最近 {str(row['last_at'])[11:16]}"
        if not friend.enabled:
            meta += " · 已停用"
        streak = streak_days(friend, self.main.state.get("events", []), datetime.now().date())
        if streak >= 2:
            meta += f" · 已连续 {streak} 天"
        tk.Label(card, text=meta, bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(6, 0))
        history = spark_history(friend, self.main.state.get("events", []), datetime.now().date(), 14)
        strip = tk.Frame(card, bg=SURFACE)
        strip.pack(anchor="w", pady=(8, 0))
        tk.Label(strip, text="近 14 天", bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(0, 8))
        canvas = tk.Canvas(strip, width=14 * 14, height=14, bg=SURFACE, highlightthickness=0, bd=0)
        canvas.pack(side="left")
        for index, item in enumerate(history):
            if item["sent"]:
                fill = SUCCESS
            elif item.get("skipped"):
                fill = WARNING
            elif item["plan"]:
                fill = DANGER
            else:
                fill = paint("#eef1f5")
            left = index * 14
            canvas.create_rectangle(left, 1, left + 10, 13, outline="", fill=fill)
        tk.Label(strip, text="今天", bg=SURFACE, fg=paint("#9aa6b6"), font=("Microsoft YaHei UI", 8)).pack(side="left", padx=(6, 0))
        note = self._row_note(row, friend)
        if note:
            tk.Label(
                card, text=note[0], bg=SURFACE, fg=note[1],
                font=("Microsoft YaHei UI", 9, "bold"), wraplength=wrap_px(self, 600), justify="left",
            ).pack(anchor="w", pady=(4, 0))
        lines = tk.Frame(card, bg=PANEL_SOFT, padx=10, pady=6)
        lines.pack(fill="x", pady=(8, 0))
        if not friend.slots:
            tk.Label(lines, text="未设置时间点", bg=PANEL_SOFT, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(anchor="w")
        for slot in sorted(friend.slots, key=lambda item: item.at):
            state = row["slots"].get(slot.slot_id, "pending")
            text, tone = SLOT_STYLE.get(state, SLOT_STYLE["pending"])
            line = tk.Frame(lines, bg=PANEL_SOFT)
            line.pack(fill="x")
            tk.Label(line, text=slot.at, bg=PANEL_SOFT, fg=INK, font=("Microsoft YaHei UI", 9, "bold")).pack(side="left")
            tk.Label(line, text=MODE_LABELS.get(slot.mode, slot.mode), bg=PANEL_SOFT, fg=INFO, font=("Microsoft YaHei UI", 8)).pack(side="left", padx=(8, 0))
            tk.Label(line, text=text, bg=PANEL_SOFT, fg=tone, font=("Microsoft YaHei UI", 9, "bold")).pack(side="right")

    def _row_note(self, row: dict, friend: Friend) -> tuple[str, str] | None:
        key = row["key"]
        if key == "pending":
            future = [slot.at for slot in friend.slots if row["slots"].get(slot.slot_id) == "pending"]
            upcoming = min(future) if future else ""
            text = "今天还没续" + (f"，下一个时间点 {upcoming} 到点会自动发" if upcoming else "，等待发送")
            return text, paint("#96651c")
        if key == "missed":
            past = [slot.at for slot in friend.slots if row["slots"].get(slot.slot_id) == "missed"]
            at = past[0] if past else ""
            text = f"今天漏了{(' ' + at + ' 这个点') if at else ''}，还没续上" if at else "今天还没续上"
            return text, DANGER
        if key == "failed":
            return "今天发送失败，还没续上", DANGER
        if key == "off":
            return "已停用，今天不会自动发送", paint("#6b7789")
        if key == "none":
            return "没有设置时间点，今天不会发送", paint("#6b7789")
        return None

    def send_round(self) -> None:
        self.main.prepare_round()
        self.refresh()


class MainWindow(tk.Tk):
    def __init__(self, store: EncryptedStore) -> None:
        super().__init__()
        self.scale = ui_scale(self)
        self.store, self.state = store, store.load("app", {"friends": [], "events": []})
        self.state.setdefault("logs", [])
        self.state.setdefault("stickers", [])
        self.state.setdefault("settings", settings_with_state(None))
        self.settings = settings_with_state(self.state.get("settings"))
        self.theme_name = use_theme(str(self.settings.get("ui", {}).get("theme") or "light"))
        self._migrate_daily_schedule()
        self.scheduler = Scheduler(self.state)
        self.automation = AutomationService(
            idle_seconds=float(self.settings["auto"].get("idle_minutes", 15)) * 60
        )
        self._confirming: set[str] = set()
        self._retry_count: dict[str, int] = {}
        self._lookup_dialog = None
        self.title("抖音续火花  v{}".format(__version__))
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        base_w, base_h = 1450 * self.scale, 900 * self.scale
        self.geometry("{}x{}".format(min(base_w, max(860, screen_w - 80)), min(base_h, max(600, screen_h - 100))))
        self.minsize(int(860 * self.scale), int(600 * self.scale))
        self.configure(bg=BG)
        self._setup_style()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._build_layout()
        self.refresh()
        self._clock_job = self.after(1000, self._tick_quote_clock)
        self._tick_job = self.after(1000, self.tick)
        self._poll_job = self.after(500, self.poll_automation)
        self.bind("<Configure>", self._on_resize, add="+")
        self._bind_shortcuts()

    # ---------------------------------------------------------------- shortcuts
    def _bind_shortcuts(self) -> None:
        self.bind("<Control-n>", lambda _event: self.add_friend())
        self.bind("<Control-b>", lambda _event: self.batch_enable())
        self.bind("<Control-e>", lambda _event: self.show_today_spark())
        self.bind("<Control-f>", lambda _event: self.focus_search())
        self.bind("<F5>", lambda _event: self.refresh())
        self.bind("<Control-Key-1>", lambda _event: self.focus_friends())
        self.bind("<Control-Key-2>", lambda _event: self.show_today_spark())
        self.bind("<Control-Key-3>", lambda _event: self.show_settings())
        self.bind("<Control-Key-4>", lambda _event: self.show_logs())
        self.bind("<Prior>", lambda _event: self._scroll_key(-1))
        self.bind("<Next>", lambda _event: self._scroll_key(1))
        self.bind("<Home>", lambda _event: self._scroll_edge("home"))
        self.bind("<End>", lambda _event: self._scroll_edge("end"))

    def _typing(self) -> bool:
        widget = self.focus_get()
        return isinstance(widget, (tk.Entry, tk.Text)) or widget is self.search_entry

    def _scroll_key(self, direction: int) -> None:
        if not self._typing():
            self.friend_scroll.scroll_page(direction)

    def _scroll_edge(self, where: str) -> None:
        if self._typing():
            return
        self.friend_scroll.scroll_to(0.0 if where == "home" else 1.0)

    def focus_search(self) -> None:
        self.search_entry.focus_set()
        self.search_entry.select_range(0, "end")

    def clear_search(self) -> None:
        if self.search_var.get():
            self.search_var.set("")
        self.search_entry.focus_set()

    def _migrate_daily_schedule(self) -> None:
        if self.state.get("schedule_version", 1) >= 2:
            return
        for friend in self.state.get("friends", []):
            for slot in friend.get("slots", []):
                slot["day"] = -1
        self.state["schedule_version"] = 2

    def _setup_style(self) -> None:
        style = ttk.Style(self)
        try:
            if style.theme_use() != "clam":
                style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Nav.TFrame", background=NAV)
        style.configure(
            "LargePrimary.TButton", background=ACCENT, foreground=ON_ACCENT, borderwidth=0,
            padding=(18, 13), font=("Microsoft YaHei UI", 11, "bold"),
        )
        style.map("LargePrimary.TButton", background=[("active", ACCENT_HOVER)])
        style.configure(
            "LargeSoft.TButton", background=SURFACE, foreground=INK, borderwidth=0,
            padding=(14, 9), font=("Microsoft YaHei UI", 10),
        )
        style.map("LargeSoft.TButton", background=[("active", SOFT)])
        style.configure(
            "LargeDanger.TButton", background=SURFACE, foreground=DANGER,
            borderwidth=0, padding=(14, 9), font=("Microsoft YaHei UI", 10),
        )
        style.map("LargeDanger.TButton", background=[("active", paint("#faf1f2"))])
        style.configure(
            "Add.TButton", background=ACCENT, foreground=ON_ACCENT, borderwidth=0,
            padding=(16, 10), font=("Microsoft YaHei UI", 10, "bold"),
        )
        style.map("Add.TButton", background=[("active", ACCENT_HOVER)])
        style.configure(
            "Soft.TButton", background=SURFACE, foreground=INK, borderwidth=0,
            padding=(10, 6), font=("Microsoft YaHei UI", 9),
        )
        style.map("Soft.TButton", background=[("active", SOFT)])
        style.configure(
            "Primary.TButton", background=ACCENT, foreground=ON_ACCENT, borderwidth=0,
            padding=(12, 7), font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.map("Primary.TButton", background=[("active", ACCENT_HOVER)])
        style.configure(
            "Danger.TButton", background=SURFACE, foreground=DANGER, borderwidth=0,
            padding=(10, 6), font=("Microsoft YaHei UI", 9),
        )
        style.map("Danger.TButton", background=[("active", paint("#faf1f2"))])
        style.configure(
            "CardSoft.TButton", background=SURFACE, foreground=MUTED, borderwidth=0,
            padding=(6, 4), font=("Microsoft YaHei UI", 9),
        )
        style.map("CardSoft.TButton", background=[("active", SOFT)])
        style.configure(
            "CardDanger.TButton", background=SURFACE, foreground=DANGER, borderwidth=0,
            padding=(6, 4), font=("Microsoft YaHei UI", 9),
        )
        style.map("CardDanger.TButton", background=[("active", paint("#faf1f2"))])
        style.configure("Nav.TButton", background=NAV, foreground=paint("#93a4ba"), borderwidth=0, padding=(18, 12), font=("Microsoft YaHei UI", 9))
        style.map("Nav.TButton", background=[("active", NAV_HOVER)])
        style.configure(
            "WarmPrimary.TButton", background=CORAL, foreground=paint("#ffffff"), borderwidth=0,
            padding=(10, 9), font=("Microsoft YaHei UI", 10, "bold"),
        )
        style.map("WarmPrimary.TButton", background=[("active", CORAL_DARK)])
        style.configure(
            "WarmSoft.TButton", background=paint("#fffdfb"), foreground=WARM_MUTED, borderwidth=1,
            bordercolor=WARM_LINE, padding=(10, 8), font=("Microsoft YaHei UI", 9),
        )
        style.map("WarmSoft.TButton", background=[("active", CORAL_SOFT)])

    def _build_layout(self) -> None:
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(0, minsize=int(210 * self.scale))
        self.grid_columnconfigure(2, minsize=int(280 * self.scale))
        sidebar = tk.Frame(self, bg=NAV, width=int(260 * self.scale))
        self.sidebar = sidebar
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)
        logo = tk.Canvas(sidebar, width=int(92 * self.scale), height=int(92 * self.scale), bg=NAV, highlightthickness=0)
        logo.pack(pady=(int(26 * self.scale), int(8 * self.scale)))
        span = int(92 * self.scale)
        centre = span / 2
        for angle in range(0, 360, 30):
            rad = math.radians(angle)
            inner, outer = centre * 0.40, centre * 0.45
            logo.create_oval(
                centre + inner * math.cos(rad) - 1, centre + inner * math.sin(rad) - 1,
                centre + outer * math.cos(rad) + 1, centre + outer * math.sin(rad) + 1,
                fill=paint("#7c8ea6"), outline="",
            )
        logo.create_oval(centre * 0.24, centre * 0.24, span - centre * 0.24, span - centre * 0.24, outline=ACCENT, width=1)
        logo.create_oval(centre * 0.34, centre * 0.34, span - centre * 0.34, span - centre * 0.34, fill=ACCENT, outline="")
        logo.create_text(centre, centre, text="火", fill=ON_ACCENT, font=("Microsoft YaHei UI", int(21 * self.scale), "bold"))
        tk.Label(sidebar, text="抖音续火花", bg=NAV, fg=ON_NAV, font=("Microsoft YaHei UI", 18, "bold")).pack()
        tk.Label(sidebar, text="DOUYIN SPARK", bg=NAV, fg=paint("#7c8ea6"), font=("Segoe UI", 8)).pack(pady=(6, 2))
        tk.Label(sidebar, text="v{}  ·  本地提醒版".format(__version__), bg=NAV, fg=paint("#93a4ba"), font=("Microsoft YaHei UI", 9)).pack(pady=(0, 20))
        self.nav_rows: list[tk.Frame] = []
        for text, icon, callback, selected in (
            ("好友管理", "▦", self.focus_friends, True),
            ("今日续火花", "🔥", self.show_today_spark, False),
            ("全局设置", "⚙", self.show_settings, False),
            ("运行日志", "▤", self.show_logs, False),
            ("关于", "ⓘ", self.show_about, False),
        ):
            bg = NAV_SELECTED if selected else NAV
            row = tk.Frame(sidebar, bg=bg, cursor="hand2", padx=14, pady=11)
            row.pack(fill="x", pady=2)
            icon_label = tk.Label(
                row, text=icon, bg=bg, fg=ACCENT_HOVER if selected else paint("#8fa0b6"),
                font=("Segoe UI Emoji", 10), cursor="hand2", width=2, anchor="w",
            )
            icon_label.pack(side="left")
            label = tk.Label(
                row, text=text, bg=bg, fg=ON_NAV if selected else paint("#b7c6d8"), anchor="w",
                cursor="hand2", font=("Microsoft YaHei UI", 10, "bold" if selected else "normal"),
            )
            label.pack(side="left", fill="x", expand=True)
            for widget in (row, icon_label, label):
                widget.bind("<Button-1>", lambda event, cb=callback: cb())
                widget.bind("<Enter>", lambda event, r=row: self._nav_hover(r, True))
                widget.bind("<Leave>", lambda event, r=row: self._nav_hover(r, False))
            row.normal_bg = bg  # type: ignore[attr-defined]
            self.nav_rows.append(row)
        tk.Frame(sidebar, bg=NAV, height=10).pack(side="bottom", fill="x")
        exit_row = tk.Frame(sidebar, bg=NAV, cursor="hand2")
        exit_row.pack(side="bottom", fill="x")
        exit_label = tk.Label(
            exit_row, text="‹  退出程序", bg=NAV, fg=paint("#8fa0b6"), anchor="w", padx=19, pady=14,
            cursor="hand2", font=("Microsoft YaHei UI", 9),
        )
        exit_label.pack(fill="x")
        for widget in (exit_row, exit_label):
            widget.bind("<Button-1>", lambda event: self.close())

        center = tk.Frame(self, bg=BG, padx=int(22 * self.scale), pady=int(22 * self.scale))
        self.center = center
        center.grid(row=0, column=1, sticky="nsew")
        center.grid_rowconfigure(1, weight=0)
        center.grid_columnconfigure(0, weight=1)
        top = tk.Frame(center, bg=BG)
        self.main_top = top
        top.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        top.grid_columnconfigure(1, weight=1)
        self.page_title = tk.Label(top, text="好友列表", bg=BG, fg=INK, font=("Microsoft YaHei UI", 20, "bold"))
        self.page_title.grid(row=0, column=0, sticky="w")
        self.friend_count = tk.Label(top, text="共 0 位 · 启用 0 位", bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 10))
        self.friend_count.grid(row=0, column=1, sticky="w", padx=14)
        self.add_button = ttk.Button(top, text="＋ 新增好友", command=self.add_friend, style="Add.TButton")
        self.add_button.grid(row=0, column=2, padx=8)
        self.all_enabled = tk.BooleanVar(value=True)
        self.batch_button = ttk.Button(top, text="批量启用", command=self.batch_enable, style="Soft.TButton")
        self.batch_button.grid(row=0, column=3, padx=(0, 6))
        self.all_enabled_button = tk.Checkbutton(
            top,
            text="全部启用",
            variable=self.all_enabled,
            command=self.toggle_all,
            bg=BG,
            activebackground=BG,
            selectcolor=BG,
            fg=INK,
            font=("Microsoft YaHei UI", 9, "bold"),
            bd=0,
            highlightthickness=0,
            cursor="hand2",
        )
        self.all_enabled_button.grid(row=0, column=4)
        search_row = tk.Frame(center, bg=BG)
        self.search_row = search_row
        search_row.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        search_box = tk.Frame(search_row, bg=SOFT, highlightthickness=0)
        search_box.pack(side="left", fill="x", expand=True)
        tk.Label(search_box, text="🔍", bg=SOFT, fg=ACCENT, font=("Segoe UI Emoji", 10)).pack(side="left", padx=(14, 8), pady=6)
        self.search_var = tk.StringVar()
        self.search_entry = tk.Entry(
            search_box, textvariable=self.search_var, bd=0, bg=SOFT, fg=INK,
            insertbackground=INK, font=("Microsoft YaHei UI", 10), relief="flat",
        )
        self.search_entry.pack(side="left", fill="x", expand=True, pady=8)
        tk.Label(search_box, text="昵称 / 文案 / 时间", bg=SOFT, fg=paint("#9aa6b6"), font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(6, 10))
        clear = tk.Label(search_box, text="✕", bg=SOFT, fg=MUTED, cursor="hand2", font=("Microsoft YaHei UI", 9), padx=10)
        clear.pack(side="right")
        clear.bind("<Button-1>", lambda _event: self.clear_search())
        self.search_var.trace_add("write", lambda *_args: self.refresh())
        self.search_entry.bind("<Escape>", lambda _event: self.clear_search())
        center.grid_rowconfigure(2, weight=1)
        self.friend_scroll = Scrollable(center, onscroll=self._on_list_scroll)
        self.friend_scroll.grid(row=2, column=0, sticky="nsew")
        self.to_top = tk.Label(
            self.friend_scroll.canvas, text="↑  回到顶部", bg=SURFACE, fg=ACCENT, padx=12, pady=6,
            highlightthickness=0, cursor="hand2",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.to_top.bind("<Button-1>", lambda _event: self.friend_scroll.scroll_to(0.0))

        right = Scrollable(self, bg=BG, width=330, padx=0, pady=22)
        right.pack_propagate(False)
        self.right_panel = right
        right.grid(row=0, column=2, sticky="nsew")
        self._build_right_panel(right.inner)

    def _nav_hover(self, row: tk.Frame, active: bool) -> None:
        bg = NAV_HOVER if active else getattr(row, "normal_bg", NAV)
        row.configure(bg=bg)
        for child in row.winfo_children():
            child.configure(bg=bg)

    def _on_list_scroll(self, first: float, _last: float) -> None:
        if first > 0.02:
            if not self.to_top.winfo_ismapped():
                self.to_top.place(relx=1.0, rely=1.0, x=-20, y=-16, anchor="se")
                self.to_top.lift()
        elif self.to_top.winfo_ismapped():
            self.to_top.place_forget()

    def _on_resize(self, event: tk.Event) -> None:
        if event.widget is not self:
            return
        compact = event.width < int(1240 * self.scale)
        sidebar_width = int((210 if compact else 260) * self.scale)
        right_width = int((280 if compact else 330) * self.scale)
        self.grid_columnconfigure(0, minsize=sidebar_width)
        self.grid_columnconfigure(2, minsize=right_width)
        self.sidebar.configure(width=sidebar_width)
        self.right_panel.configure(width=right_width)
        self.center.configure(padx=int((14 if compact else 22) * self.scale))
        self.main_top.grid_configure(pady=(0, 10 if compact else 16))
        self.page_title.configure(font=("Microsoft YaHei UI", 17 if compact else 20, "bold"))
        if compact:
            for column in range(5):
                self.main_top.grid_columnconfigure(column, weight=0)
            self.main_top.grid_columnconfigure(0, weight=1)
            self.page_title.grid_configure(row=0, column=0, sticky="w")
            self.add_button.grid_configure(row=0, column=1, sticky="e", padx=0)
            self.friend_count.grid_configure(row=1, column=0, sticky="w", padx=0)
            self.batch_button.grid_configure(row=1, column=1, sticky="e", padx=(0, 8))
            self.all_enabled_button.grid_configure(row=1, column=2, sticky="e")
        else:
            for column in range(5):
                self.main_top.grid_columnconfigure(column, weight=0)
            self.main_top.grid_columnconfigure(1, weight=1)
            self.page_title.grid_configure(row=0, column=0, sticky="w")
            self.friend_count.grid_configure(row=0, column=1, sticky="w", padx=16)
            self.add_button.grid_configure(row=0, column=2, sticky="", padx=8)
            self.batch_button.grid_configure(row=0, column=3, sticky="", padx=(0, 6))
            self.all_enabled_button.grid_configure(row=0, column=4, sticky="")

    def _card(self, parent: tk.Misc, title: str) -> tk.Frame:
        pad = int(16 * self.scale)
        icons = {"自动发送": "▶", "浏览器": "◎", "运行状态": "◷"}
        card = tk.Frame(parent, bg=SURFACE, padx=pad, pady=pad, highlightthickness=0)
        card.pack(fill="x", pady=(0, int(14 * self.scale)))
        head = tk.Frame(card, bg=SURFACE)
        head.pack(fill="x", pady=(0, 10))
        tk.Label(
            head, text=icons.get(title, "◆"), bg=SURFACE, fg=ACCENT,
            font=("Segoe UI Emoji", 10, "bold"),
        ).pack(side="left", padx=(0, 7))
        tk.Label(head, text=title, bg=SURFACE, fg=INK, font=("Microsoft YaHei UI", 13, "bold")).pack(side="left")
        return card

    def _build_right_panel(self, parent: tk.Misc) -> None:
        control = self._card(parent, "自动发送")
        ttk.Button(control, text="▶  立即自动发送一轮", command=self.prepare_round, style="LargePrimary.TButton").pack(fill="x", pady=4)
        ttk.Button(control, text="◎  今日续火花面板", command=self.show_today_spark, style="LargeSoft.TButton").pack(fill="x", pady=4)
        ttk.Button(control, text="■  停止自动调度", command=self.pause_all, style="LargeDanger.TButton").pack(fill="x", pady=4)
        ttk.Button(control, text="⚙  自动化与 AI 设置", command=self.show_settings, style="LargeSoft.TButton").pack(fill="x", pady=4)
        pad = int(15 * self.scale)
        quote = tk.Frame(
            parent, bg=WARM_BG, padx=pad, pady=pad,
            highlightthickness=0,
        )
        quote.pack(fill="x", pady=(0, int(14 * self.scale)))
        head = tk.Frame(quote, bg=WARM_BG)
        head.pack(fill="x")
        tk.Label(
            head, text="今天，听我的", bg=WARM_BG, fg=WARM_INK,
            font=("Microsoft YaHei UI", 14, "bold"),
        ).pack(side="left")
        tk.Label(
            head, text="◆", bg=WARM_BG, fg=CORAL, font=("Segoe UI", 9, "bold"),
        ).pack(side="left", padx=(6, 0))
        tk.Label(
            quote, text="别磨蹭，先把今天拿下", bg=WARM_BG, fg=WARM_MUTED,
            font=("Microsoft YaHei UI", 9), anchor="w",
        ).pack(fill="x", pady=(2, 0))
        copy_hint = tk.Label(
            head, text="复制", bg=WARM_BG, fg=CORAL_DARK, cursor="hand2",
            font=("Microsoft YaHei UI", 9, "underline"),
        )
        copy_hint.pack(side="right")
        copy_hint.bind("<Button-1>", lambda _event: self.copy_quote())
        tk.Frame(quote, bg=WARM_BG, height=8).pack(fill="x")

        clock_row = tk.Frame(quote, bg=WARM_BG)
        clock_row.pack(fill="x")
        self.quote_date_label = tk.Label(
            clock_row, text="--", bg=WARM_BG, fg=WARM_INK,
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.quote_date_label.pack(side="left")
        self.quote_clock_label = tk.Label(
            clock_row, text="--:--:--", bg=WARM_BG, fg=CORAL,
            font=("Segoe UI", 12, "bold"),
        )
        self.quote_clock_label.pack(side="right")
        self.quote_hi_label = tk.Label(
            quote, text="", bg=WARM_BG, fg=WARM_MUTED, anchor="w",
            font=("Microsoft YaHei UI", 9),
        )
        self.quote_hi_label.pack(fill="x", pady=(2, 8))

        quote_box = tk.Frame(quote, bg=paint("#fffdfb"), highlightthickness=0)
        quote_box.pack(fill="x")
        inner = tk.Frame(quote_box, bg=paint("#fffdfb"), padx=int(14 * self.scale), pady=int(10 * self.scale))
        inner.pack(fill="both", expand=True)
        watermark = tk.Label(
            quote_box, text="❞", bg=paint("#fffdfb"), fg=paint("#e08368"),
            font=("Georgia", 26, "bold"),
        )
        watermark.place(relx=0.97, rely=0.02, anchor="ne")
        self.quote_mark = tk.Label(
            inner, text="“", bg=paint("#fffdfb"), fg=paint("#e08368"), font=("Georgia", 18, "bold")
        )
        self.quote_mark.pack(anchor="w")
        self.quote_text_label = tk.Label(
            inner, text="正在挑一句狠的…", bg=paint("#fffdfb"), fg=WARM_INK, justify="left", anchor="w",
            font=("Microsoft YaHei UI", 12, "bold"), wraplength=int(238 * self.scale),
        )
        self.quote_text_label.pack(fill="x", pady=(1, 6))
        self.quote_from_label = tk.Label(
            inner, text="", bg=paint("#fffdfb"), fg=WARM_MUTED, anchor="e", justify="right",
            font=("Microsoft YaHei UI", 9),
        )
        self.quote_from_label.pack(fill="x")

        actions_row = tk.Frame(quote, bg=WARM_BG)
        actions_row.pack(fill="x", pady=(10, 0))
        actions_row.columnconfigure(0, weight=1)
        actions_row.columnconfigure(1, weight=1)
        self.quote_next_button = ttk.Button(
            actions_row, text="换句更狠的", command=self.next_quote, style="WarmPrimary.TButton"
        )
        self.quote_next_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.quote_ai_button = ttk.Button(
            actions_row, text="AI 也来一句", command=self.ai_quote, style="WarmSoft.TButton"
        )
        self.quote_ai_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        footer_row = tk.Frame(quote, bg=WARM_BG)
        footer_row.pack(fill="x", pady=(7, 0))
        self.quote_source_label = tk.Label(
            footer_row, text="点「换句更狠的」，或者让 AI 来一句", bg=WARM_BG, fg=paint("#a8917f"),
            font=("Microsoft YaHei UI", 9), anchor="w",
        )
        self.quote_source_label.pack(fill="x")

        account = self._card(parent, "浏览器")
        self.account_label = tk.Label(
            account,
            text="登录态：独立自动化浏览器（一次性扫码）",
            bg=SURFACE,
            fg=MUTED,
            justify="left",
            wraplength=int(280 * self.scale),
            font=("Microsoft YaHei UI", 10),
        )
        self.account_label.pack(anchor="w", pady=(0, 10))
        ttk.Button(account, text="打开自动化浏览器（扫码登录）", command=self.open_automation_browser, style="LargeSoft.TButton").pack(fill="x", pady=3)
        ttk.Button(account, text="🧪  浏览器自检（不发送）", command=self.self_check, style="LargeSoft.TButton").pack(fill="x", pady=3)
        ttk.Button(account, text="测试发送给某位好友", command=self.test_send, style="LargeSoft.TButton").pack(fill="x", pady=3)
        ttk.Button(account, text="导出加密备份", command=self.export_config, style="LargeSoft.TButton").pack(fill="x", pady=3)
        status = self._card(parent, "运行状态")
        self.next_label = tk.Label(status, text="下次提醒\n--", bg=SURFACE, fg=MUTED, justify="left", font=("Microsoft YaHei UI", 10))
        self.next_label.pack(anchor="w", pady=(2, 6))
        tk.Frame(status, bg=SURFACE, height=8).pack(fill="x")
        self.plan_label = tk.Label(status, text="今日计划\n0 条", bg=SURFACE, fg=MUTED, justify="left", font=("Microsoft YaHei UI", 10))
        self.plan_label.pack(anchor="w", pady=(0, 6))
        tk.Frame(status, bg=SURFACE, height=8).pack(fill="x")
        self.spark_label = tk.Label(status, text="今日续火花\n--", bg=SURFACE, fg=MUTED, justify="left", font=("Microsoft YaHei UI", 10))
        self.spark_label.pack(anchor="w", pady=(0, 6))
        tk.Frame(status, bg=SURFACE, height=8).pack(fill="x")
        self.browser_label = tk.Label(status, text="自动化\n就绪", bg=SURFACE, fg=MUTED, justify="left", font=("Microsoft YaHei UI", 10))
        self.browser_label.pack(anchor="w", pady=(0, 6))
        tk.Frame(status, bg=SURFACE, height=8).pack(fill="x")
        self.current_label = tk.Label(status, text="当前状态\n调度运行中", bg=SURFACE, fg=SUCCESS, justify="left", font=("Microsoft YaHei UI", 10, "bold"))
        self.current_label.pack(anchor="w", pady=(0, 2))

    # ------------------------------------------------------------------ 今天一句话
    WEEKDAY_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

    def _quote_record(self) -> dict:
        return self.state.setdefault("quote", {})

    def _set_quote(self, text: str, author: str, source: str) -> None:
        today = datetime.now().date().isoformat()
        first = str(self._quote_record().get("first_date") or today)
        self.state["quote"] = {
            "date": today,
            "first_date": first,
            "text": text,
            "author": author,
            "source": source,
        }
        self.save()
        self._render_quote(self.state["quote"])

    def refresh_quote(self, force: bool = False) -> None:
        today = datetime.now().date()
        record = self._quote_record()
        if force or record.get("date") != today.isoformat() or not record.get("text"):
            text, author = quote_for(today)
            self.state["quote"] = {
                "date": today.isoformat(),
                "first_date": str(record.get("first_date") or today.isoformat()),
                "text": text,
                "author": author,
                "source": "local",
            }
            self.save()
            record = self.state["quote"]
        elif not record.get("first_date"):
            record["first_date"] = today.isoformat()
            self.save()
        self._render_quote(record)
        try:
            self._paint_clock()
        except tk.TclError:
            pass

    def _days_together(self, record: dict) -> int:
        try:
            first = date.fromisoformat(str(record.get("first_date") or ""))
        except ValueError:
            return 1
        return max(1, (datetime.now().date() - first).days + 1)

    def _render_quote(self, record: dict) -> None:
        text = str(record.get("text") or "").strip()
        author = str(record.get("author") or "佚名").strip()
        source = str(record.get("source") or "local")
        days = self._days_together(record)
        self.quote_text_label.configure(text=text or "今天还没挑句子")
        self.quote_from_label.configure(text=f"—— {author}")
        if source == "ai":
            self.quote_source_label.configure(text=f"AI 写的 · 盯你第 {days} 天", fg=CORAL_DARK)
        else:
            pool = [item[0] for item in QUOTE_POOL]
            index = pool.index(text) + 1 if text in pool else 0
            self.quote_source_label.configure(
                text=f"本地第 {index}/{len(pool)} 句 · 盯你第 {days} 天" if index else f"盯你第 {days} 天",
                fg=paint("#a8917f"),
            )

    def _greeting(self) -> str:
        hour = datetime.now().hour
        if hour < 5:
            return "还没睡？明天照样给我准时起"
        if hour < 9:
            return "起床，别赖，今天先赢一局"
        if hour < 12:
            return "上午别飘，先把最难的干掉"
        if hour < 14:
            return "吃饭，别拿忙当借口"
        if hour < 18:
            return "别摸鱼，收尾阶段了"
        if hour < 23:
            return "今天的事，今天做完"
        return "现在关灯，明天继续赢"

    def _paint_clock(self) -> None:
        now = datetime.now()
        self.quote_clock_label.configure(text=now.strftime("%H:%M:%S"))
        self.quote_date_label.configure(
            text="{}年{}月{}日 · {}".format(now.year, now.month, now.day, self.WEEKDAY_CN[now.weekday()])
        )
        self.quote_hi_label.configure(text=self._greeting())

    def _tick_quote_clock(self) -> None:
        try:
            self._paint_clock()
        except tk.TclError:
            return
        self._clock_job = self.after(1000, self._tick_quote_clock)

    def next_quote(self) -> None:
        record = self._quote_record()
        text, author = next_local_quote(str(record.get("text") or ""))
        self._set_quote(text, author, "local")

    def ai_quote(self) -> None:
        cfg = self.settings.get("ai", {})
        if not str(cfg.get("api_key") or "").strip():
            messagebox.showinfo(
                "先接入 GPT 接口",
                "到「自动化与 AI 设置」里选「OpenAI 兼容 / GPT」：\n"
                "接口地址 https://api.openai.com/v1，模型 gpt-4o-mini，再填你的 API Key。\n"
                "国内中转服务只要是 OpenAI 兼容格式，同样可用。",
                parent=self,
            )
            return
        current = str(self._quote_record().get("text") or "")
        self.quote_ai_button.configure(state="disabled")
        self.quote_source_label.configure(text="正在写，最多等 45 秒…", fg=CORAL_DARK)

        def run() -> None:
            try:
                text, author = suggest_quote(cfg, recent=[current])
            except Exception as exc:  # 包含 AIError 与网络异常
                self.after(0, lambda message=str(exc): self._ai_quote_failed(message))
                return
            self.after(0, lambda: self._apply_ai_quote(text, author))

        threading.Thread(target=run, name="quote-ai", daemon=True).start()

    def _apply_ai_quote(self, text: str, author: str) -> None:
        try:
            self.quote_ai_button.configure(state="normal")
        except tk.TclError:
            return
        self._set_quote(text, author, "ai")

    def _ai_quote_failed(self, message: str) -> None:
        try:
            self.quote_ai_button.configure(state="normal")
        except tk.TclError:
            return
        self._render_quote(self._quote_record())
        cfg = self.settings.get("ai", {})
        address = str(cfg.get("base_url") or "未填写")
        model = str(cfg.get("model") or "未填写")
        hint = (
            f"当前接口：{address}\n当前模型：{model}\n\n"
            "排查顺序：\n"
            "1. 到「自动化与 AI 设置」点新加的「测试接口连通性」，它会直接告诉你通不通、耗时多少毫秒；\n"
            "2. 国内直连 api.openai.com 经常超时或连不上，建议把服务商换成智谱 GLM（glm-4-flash）或 DeepSeek，"
            "也可以填一个可用的中转地址；\n"
            "3. 接口地址要写到 /v1 这一级，模型名不能填网址；\n"
            "4. 如果开着代理，确认它对本程序生效。"
        )
        messagebox.showwarning(
            "AI 没能写出金句", f"{message or '请检查 AI 设置'}\n\n{hint}", parent=self
        )

    def copy_quote(self) -> None:
        record = self._quote_record()
        text = format_quote(str(record.get("text") or ""), str(record.get("author") or "佚名"))
        if not text:
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.quote_source_label.configure(text="已复制到剪贴板", fg=SUCCESS)

    def friends(self) -> list[Friend]:
        return self.scheduler.friends

    def selected(self) -> Friend | None:
        return None

    def save(self) -> None:
        self.store.save("app", self.state)

    def sticker_paths(self) -> list[str]:
        return [str(path) for path in self.state.get("stickers", []) if Path(path).exists()]

    def replace_friend(self, friend: Friend) -> None:
        raw = self.state.setdefault("friends", [])
        for index, current in enumerate(raw):
            if current.get("friend_id") == friend.friend_id:
                raw[index] = friend.to_dict()
                break
        else:
            raw.append(friend.to_dict())
        self.scheduler = Scheduler(self.state)
        self.save()
        self.refresh()

    def add_friend(self) -> None:
        FriendDialog(self, None, self.replace_friend, self.sticker_paths(), self.settings, lookup=self.lookup_douyin, open_library=self.show_sticker_library)

    def edit_friend(self, friend: Friend) -> None:
        FriendDialog(self, friend, self.replace_friend, self.sticker_paths(), self.settings, lookup=self.lookup_douyin, open_library=self.show_sticker_library)

    def lookup_douyin(self, dialog: FriendDialog, keyword: str) -> None:
        self._lookup_dialog = dialog
        self.automation.submit(Job(kind="lookup", keyword=keyword))
        self.add_log(f"正在按抖音号「{keyword}」搜索用户…")

    def delete_friend(self, friend: Friend) -> None:
        if not messagebox.askyesno("删除好友", f"确定删除“{friend.name}”吗？", parent=self):
            return
        self.state["friends"] = [raw for raw in self.state.get("friends", []) if raw.get("friend_id") != friend.friend_id]
        self.scheduler = Scheduler(self.state)
        self.save()
        self.refresh()

    def toggle_all(self) -> None:
        enabled = self.all_enabled.get()
        for friend in self.friends():
            friend.enabled = enabled
            self.replace_friend(friend)

    def set_friend_enabled(self, friend: Friend, enabled: bool) -> None:
        if friend.enabled == enabled:
            return
        friend.enabled = enabled
        self.replace_friend(friend)
        self.add_log(f"{'启用' if enabled else '停用'}好友：{friend.name}")

    def batch_enable(self) -> None:
        EnablePickerDialog(self, self.friends(), self.apply_enable_map)

    def apply_enable_map(self, mapping: dict[str, bool]) -> None:
        changed = 0
        for friend in self.friends():
            if friend.friend_id not in mapping:
                continue
            target = mapping[friend.friend_id]
            if friend.enabled != target:
                friend.enabled = target
                self.replace_friend(friend)
                changed += 1
        if changed:
            self.add_log(f"批量调整启用状态：{changed} 位好友")
        else:
            self.refresh()

    def spark_snapshot(self) -> list[dict]:
        return spark_rows(self.friends(), self.state.get("events", []), datetime.now())

    def show_today_spark(self) -> None:
        TodaySparkDialog(self)

    def refresh(self) -> None:
        for child in self.friend_scroll.inner.winfo_children():
            child.destroy()
        friends = self.friends()
        enabled_count = sum(1 for friend in friends if friend.enabled and friend.consent)
        query = self.search_var.get().strip() if hasattr(self, "search_var") else ""
        shown = [friend for friend in friends if friend_matches(friend, query)]
        self.friend_count.configure(
            text=f"共 {len(friends)} 位 · 提醒已启用 {enabled_count} 位" + (f" · 匹配 {len(shown)} 位" if query else "")
        )
        self.all_enabled.set(bool(friends) and all(friend.enabled for friend in friends))
        spark = spark_rows(friends, self.state.get("events", []), datetime.now())
        spark_by_id = {row["friend"].friend_id: row for row in spark}
        for friend in shown:
            FriendCard(self.friend_scroll.inner, friend, self, spark_by_id.get(friend.friend_id)).pack(fill="x", pady=(0, 12))
        if not shown:
            empty = (
                f"没有匹配「{query}」的好友，换个昵称、时间或文案关键词试试。"
                if query and friends
                else "还没有好友，先点右上角「＋ 新增好友」。"
            )
            tk.Label(
                self.friend_scroll.inner, text=empty, bg=BG, fg=MUTED,
                font=("Microsoft YaHei UI", 10), padx=10, pady=24, anchor="w", justify="left",
            ).pack(fill="x")
        if query != getattr(self, "_last_query", None):
            self._last_query = query
            self.friend_scroll.scroll_to(0.0)
        self.friend_scroll.canvas.update_idletasks()
        inner_h = self.friend_scroll.inner.winfo_reqheight()
        if 0 < inner_h <= self.friend_scroll.canvas.winfo_height() + 2:
            self.friend_scroll.scroll_to(0.0)
        self.refresh_quote()
        today = datetime.now().date().isoformat()
        events = [
            event
            for event in self.state.get("events", [])
            if event.get("date") == today
            and event.get("status")
            in {
                "reminded",
                "pending",
                "sent",
                "needs_review",
                "skipped",
                "skipped_limit",
                "auto_queued",
                "auto_sending",
                "auto_sent",
                "auto_failed",
                "auto_skipped",
                "auto_needs_confirm",
            }
        ]
        self.plan_label.configure(text=f"今日计划\n{len(events)} 条")
        kept = sum(1 for row in spark if row["key"] == "sent")
        need = sum(1 for row in spark if row["key"] in {"missed", "failed", "pending"})
        spark_text = f"今日续火花\n已续 {kept} / {len(friends)} 位"
        if need:
            spark_text += f"\n还没续 {need} 位"
        self.spark_label.configure(text=spark_text, fg=DANGER if need else (SUCCESS if kept else MUTED))
        next_due = []
        now = datetime.now()
        for friend in friends:
            due = self.scheduler.next_due(friend, now)
            if due:
                next_due.append(due)
        self.next_label.configure(text="下次提醒\n" + (min(next_due).strftime("%H:%M") if next_due else "--"))
        self.current_label.configure(text="当前状态\n提醒调度运行中" if friends else "当前状态\n等待添加好友", fg=SUCCESS if friends else MUTED)

    def prepare_round(self) -> None:
        events = self.scheduler.prepare_round(datetime.now().replace(second=0, microsecond=0))
        if not events:
            messagebox.showinfo("暂无待准备任务", "没有启用且已授权的好友，或已达到今日提醒上限。", parent=self)
            return
        self.save()
        self.refresh()
        friends = {friend.friend_id: friend for friend in self.friends()}
        for event in events:
            friend = friends.get(event.friend_id)
            if not friend:
                continue
            if self.settings["auto"].get("enabled"):
                self.queue_auto(event, friend)
            else:
                ReminderToast(self, event, friend, self.event_status)
        self.add_log(f"已处理 {len(events)} 位好友的发送任务。")

    def _slot_emoji(self, friend: Friend, event_id: str) -> str:
        event = next((item for item in self.scheduler.events() if item.get("event_id") == event_id), None)
        if not event:
            return ""
        slot = next((s for s in friend.slots if s.slot_id == event.get("slot_id")), None)
        return slot.emoji_path if slot and slot.sends_emoji else ""

    def queue_auto(self, event: DueEvent, friend: Friend, *, delay_override: int | None = None) -> bool:
        if event.event_id in self._confirming:
            return False
        if not self.scheduler.claim_auto(event.event_id):
            return False
        self.save()
        delay = float(self.settings["auto"].get("delay_seconds", 0)) if delay_override is None else float(delay_override)
        self.automation.submit(
            Job(
                kind="auto",
                event_id=event.event_id,
                nickname=friend.name,
                message=event.message,
                fallback=event.message,
                ai=dict(self.settings["ai"]),
                recent=list(friend.recent_sent),
                approved=friend.auto_approved,
                emoji=event.emoji_path,
                when=event.due_at,
                delay=delay,
                mode=event.mode,
            )
        )
        self.add_log(f"已排入自动发送：{friend.name}（{event.due_at.strftime('%H:%M')} · {MODE_LABELS.get(event.mode, event.mode)}）")
        return True

    def pause_all(self) -> None:
        for friend in self.friends():
            friend.enabled = False
            self.replace_friend(friend)
        self.add_log("已暂停全部好友调度。")

    def show_settings(self) -> None:
        SettingsDialog(self, self.settings, self._save_settings)

    def set_theme(self, name: str, persist: bool = True) -> None:
        """Switch palette and rebuild the chrome so every widget repaints."""
        self.theme_name = use_theme(name)
        self.settings.setdefault("ui", {})["theme"] = self.theme_name
        if persist:
            self.state["settings"] = self.settings
            self.save()
        for child in self.winfo_children():
            if isinstance(child, tk.Toplevel):
                continue  # keep open dialogs alive while the shell rebuilds
            child.destroy()
        self.configure(bg=BG)
        self._setup_style()
        self._build_layout()
        self.refresh()

    def _save_settings(self, values: dict) -> None:
        self.settings = settings_with_state(values)
        self.state["settings"] = self.settings
        self.automation.idle_seconds = float(self.settings["auto"].get("idle_minutes", 15)) * 60
        self.save()
        self.refresh()
        self.add_log("自动化与 AI 设置已保存。")

    def open_automation_browser(self) -> None:
        self.automation.submit(Job(kind="login"))
        self.add_log("正在打开自动化浏览器，请扫码登录抖音。")
        messagebox.showinfo(
            "扫码登录",
            "自动化浏览器将打开抖音聊天页，请用抖音 App 扫码登录。\n"
            "登录成功后保持窗口打开，回到这里点「浏览器自检」确认可用。",
            parent=self,
        )

    def self_check(self) -> None:
        self.automation.submit(Job(kind="probe"))
        self.add_log("已发起浏览器自检（只读取页面，不发送消息）。")

    def test_send(self) -> None:
        friends = [friend for friend in self.friends() if friend.enabled]
        if not friends:
            messagebox.showinfo("没有可用好友", "请先新增并启用至少一位好友。", parent=self)
            return
        dialog = tk.Toplevel(self)
        dialog.title("测试发送")
        dialog.transient(self)
        dialog.grab_set()
        dialog.geometry("520x300")
        body = tk.Frame(dialog, padx=16, pady=14)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="选一位好友试发一条（不占用今日计划次数）", font=("Microsoft YaHei UI", 10)).pack(anchor="w")
        picks = [f"{friend.name}  ·  {friend.friend_id[:6]}" for friend in friends]
        combo = ttk.Combobox(body, values=picks, state="readonly", width=44)
        combo.current(0)
        combo.pack(anchor="w", pady=8)
        text = tk.Text(body, height=5, wrap="word", relief="flat", bg=paint("#f5f8fd"), padx=10, pady=8, font=("Microsoft YaHei UI", 10))
        text.insert("1.0", "这是一条测试消息，确认自动发送链路可用。")
        text.pack(fill="both", expand=True, pady=6)
        note = tk.Label(body, text="会真实发送到你选择的会话，请确认昵称与文案。", fg=MUTED, font=("Microsoft YaHei UI", 9))
        note.pack(anchor="w")

        def send() -> None:
            index = combo.current()
            friend = friends[index] if 0 <= index < len(friends) else None
            message = text.get("1.0", "end").strip()
            if not friend or not message:
                messagebox.showwarning("无法测试", "请选择好友并填写文案。", parent=dialog)
                return
            self.automation.submit(
                Job(kind="send", event_id=f"test-{friend.friend_id}", nickname=friend.name, message=message)
            )
            self.add_log(f"已提交测试发送：{friend.name}")
            dialog.destroy()

        actions = tk.Frame(body)
        actions.pack(fill="x", pady=(6, 0))
        ttk.Button(actions, text="确认发送", command=send, style="LargePrimary.TButton").pack(side="right")
        ttk.Button(actions, text="取消", command=dialog.destroy, style="LargeSoft.TButton").pack(side="right", padx=8)

    def poll_automation(self) -> None:
        for result in self.automation.poll():
            self._handle_result(result)
        busy = self.automation.busy
        self.browser_label.configure(text="自动化\n{}".format("发送中…" if busy else "就绪"),
                                     fg=WARNING if busy else MUTED)
        self._poll_job = self.after(500, self.poll_automation)

    def _handle_result(self, result: JobResult) -> None:
        if result.kind == "login":
            self.add_log(result.detail or "已打开自动化浏览器")
            if result.status == "login_ok":
                messagebox.showinfo("登录成功", "自动化浏览器已登录，可以开始自动发送。", parent=self)
            return
        if result.kind == "probe":
            report = result.report or {}
            detail = result.detail or "自检完成"
            self.add_log(f"浏览器自检：{detail}")
            dump = report.get("dump")
            if dump:
                self.add_log(f"已保存页面结构快照：{dump}")
            if report.get("shot"):
                shot = report.get("shot")
                if messagebox.askyesno("自检完成", f"{detail}\n\n是否打开自检截图？", parent=self) and os.name == "nt":
                    try:
                        os.startfile(shot)  # type: ignore[attr-defined]
                    except OSError:
                        pass
            else:
                messagebox.showinfo("自检完成", detail, parent=self)
            return
        if result.kind == "lookup":
            dialog = self._lookup_dialog
            if dialog is not None:
                try:
                    dialog.show_lookup(list((result.report or {}).get("candidates") or []), result.detail)
                except tk.TclError:
                    pass
            self.add_log(f"按抖音号查找：{result.detail}")
            if result.status == "lookup_error":
                messagebox.showwarning("查找失败", result.detail or "搜索页打不开，请稍后再试。", parent=self)
            return
        if result.kind == "send":
            self.add_log(f"测试发送：{result.status} · {result.detail}")
            messagebox.showinfo(
                "测试发送结果",
                f"状态：{result.status}\n{result.detail}\n\n文案：{result.message}",
                parent=self,
            )
            return
        if result.kind != "auto":
            return
        friends = {friend.friend_id: friend for friend in self.friends()}
        event = next((item for item in self.scheduler.events() if item.get("event_id") == result.event_id), None)
        friend_id = event.get("friend_id") if event else ""
        friend = friends.get(friend_id)
        if result.status == "needs_confirm":
            if result.event_id in self._confirming or not friend:
                return
            self._confirming.add(result.event_id)
            slot = None
            if event and event.get("slot_id"):
                slot = next((s for s in friend.slots if s.slot_id == event.get("slot_id")), None)
            due = DueEvent(
                event_id=result.event_id,
                friend_id=friend.friend_id,
                slot_id=str(event.get("slot_id", "")) if event else "",
                due_at=datetime.fromisoformat(event.get("due_at")) if event and event.get("due_at") else datetime.now(),
                message=result.message,
                emoji_path=slot.emoji_path if slot and slot.sends_emoji else "",
                mode=slot.mode if slot else result.mode,
            )

            def confirm(target: DueEvent, always: bool) -> None:
                self._confirming.discard(result.event_id)
                if always:
                    friend.auto_approved = True
                    self.replace_friend(friend)
                self.scheduler.set_event_status(result.event_id, "auto_sending")
                self.save()
                self.automation.submit(
                    Job(
                        kind="auto",
                        event_id=result.event_id,
                        nickname=friend.name,
                        message=result.message,
                        fallback=result.message,
                        approved=True,
                        emoji=target.emoji_path,
                        when=datetime.now(),
                        mode=target.mode,
                    )
                )

            def skip(target: DueEvent) -> None:
                self._confirming.discard(result.event_id)
                self.scheduler.set_event_status(result.event_id, "auto_skipped", "用户在首次确认时跳过")
                self.save()
                self.refresh()

            AutoConfirmDialog(self, due, friend, result.message, result.detail or "AI 生成", confirm, skip)
            return

        if result.status == "sent":
            self.scheduler.set_event_status(result.event_id, "auto_sent", result.detail)
            self._retry_count.pop(result.event_id, None)
            if friend:
                entry = result.message or "［仅表情］"
                friend.recent_sent = (friend.recent_sent + [entry])[-8:]
                self.replace_friend(friend)
            self.add_log(f"已自动发送给 {friend.name if friend else '好友'}：{result.message or '［仅表情］'}")
            self.save()
            self.refresh()
            return
        if result.status == "cancelled":
            self.scheduler.set_event_status(result.event_id, "auto_skipped", result.detail)
            self.save()
            self.refresh()
            return

        self.scheduler.set_event_status(result.event_id, "auto_failed", result.detail)
        retries = self._retry_count.get(result.event_id, 0)
        limit = int(self.settings["auto"].get("retry", 1))
        if friend and retries < limit and result.status != "failed_login":
            self._retry_count[result.event_id] = retries + 1
            self.add_log(f"自动发送失败，准备第 {retries + 1} 次重试：{result.detail}")
            self.automation.submit(
                Job(
                    kind="auto",
                    event_id=result.event_id,
                    nickname=friend.name,
                    message=result.message,
                    fallback=result.message,
                    approved=True,
                    emoji="" if result.message.strip() else self._slot_emoji(friend, result.event_id),
                    when=datetime.now(),
                    mode=result.mode,
                )
            )
        else:
            self.add_log(f"自动发送失败（{friend.name if friend else '好友'}）：{result.detail}")
            messagebox.showerror(
                "自动发送失败",
                f"{friend.name if friend else '好友'} 的消息没有发出去。\n原因：{result.detail}\n\n可在「运行日志」里查看，或手动补发。",
                parent=self,
            )
        self.save()
        self.refresh()

    def open_douyin(self) -> bool:
        if not open_external_url(DOUYIN_CHAT_URL):
            messagebox.showerror("无法打开浏览器", "请手动打开 https://www.douyin.com/chat。", parent=self)
            return False
        self.add_log("已打开抖音聊天页，请在官方页面扫码登录并手动进入会话。")
        return True

    def manual_status(self) -> None:
        self.open_douyin()
        messagebox.showinfo("手动查看", "请在 Edge 聊天页查看火花状态。当前版本不读取账户火花状态。", parent=self)

    def show_sticker_library(self) -> None:
        StickerDialog(self, self.state, self._stickers_changed, self.settings)

    def _stickers_changed(self) -> None:
        self.save()
        self.refresh()

    def add_sticker_files(self, paths: list[str]) -> int:
        existing = [str(path) for path in self.state.get("stickers", [])]
        added = 0
        for raw in paths:
            path = str(raw)
            if not path or path in existing:
                continue
            existing.append(path)
            added += 1
        self.state["stickers"] = existing
        self.save()
        self.refresh()
        return added

    def add_sticker_folder(self, folder: str) -> int:
        if not folder:
            return 0
        found = [str(path) for path in Path(folder).rglob("*") if path.suffix.lower() in {".png", ".gif"}]
        return self.add_sticker_files(found)

    def remove_stickers(self, paths: list[str]) -> None:
        keep = [str(path) for path in self.state.get("stickers", []) if str(path) not in set(paths)]
        self.state["stickers"] = keep
        self.save()
        self.refresh()

    def export_config(self) -> None:
        path = filedialog.asksaveasfilename(parent=self, title="导出加密备份", defaultextension=".db", filetypes=[("加密数据库", "*.db")])
        if path:
            self.save()
            export_backup(self.store, Path(path))
            self.add_log(f"已导出加密备份：{Path(path).name}")

    def add_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.state.setdefault("logs", []).append(f"[{timestamp}] {message}")
        self.state["logs"] = self.state["logs"][-100:]
        self.save()

    def show_logs(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("运行日志")
        dialog.geometry("760x460")
        dialog.transient(self)
        text = tk.Text(dialog, bg=paint("#111827"), fg=paint("#dbeafe"), relief="flat", padx=14, pady=14, font=("Consolas", 10))
        text.pack(fill="both", expand=True, padx=12, pady=12)
        text.insert("1.0", "\n".join(self.state.get("logs", [])) or "暂无运行日志")
        text.configure(state="disabled")

    def show_about(self) -> None:
        messagebox.showinfo(
            "关于",
            "抖音续火花 v{} · 全自动发送版\n"
            "本地运行；消息由你配置的 AI 或模板生成，经自动化浏览器发到抖音官方聊天页。\n"
            "首次对每位好友自动发送前会要求确认一次。\n\n"
            "AI 能力：\n"
            "到点自动写文案；编辑好友时可「AI 候选」一次出 3 条；\n"
            "表情库可让 AI 直接画一张表情；今日面板可生成「AI 本周小结」。\n\n"
            "快捷键：\n"
            "Ctrl+N 新增好友　Ctrl+B 批量启用　Ctrl+E 今日续火花\n"
            "Ctrl+F 搜索好友　F5 刷新状态　PageUp / PageDown 翻页　Home / End 到顶 / 到底\n"
            "Ctrl+1 好友管理　Ctrl+2 今日续火花面板　Ctrl+3 全局设置　Ctrl+4 运行日志\n"
            "鼠标滚轮在窗口任意位置都能滚动，按住卡片空白处可以拖动列表，双击卡片可折叠 / 展开。".format(__version__),
            parent=self,
        )

    def focus_friends(self) -> None:
        self.friend_scroll.canvas.yview_moveto(0)

    def tick(self) -> None:
        now = datetime.now()
        events = self.scheduler.due_events(now)
        if events:
            self.save()
            self.refresh()
        friends = {friend.friend_id: friend for friend in self.friends()}
        auto_on = bool(self.settings["auto"].get("enabled"))
        late_limit = int(self.settings["auto"].get("late_minutes", 120))
        for event in events:
            friend = friends.get(event.friend_id)
            if not friend:
                continue
            if auto_on:
                late_seconds = (now - event.due_at).total_seconds()
                if late_limit > 0 and late_seconds > late_limit * 60:
                    self.scheduler.set_event_status(
                        event.event_id, "auto_skipped", f"已超过 {late_limit} 分钟发送窗口，跳过本次"
                    )
                    self.add_log(f"跳过迟到任务：{friend.name} {event.due_at.strftime('%H:%M')}")
                    continue
                self.queue_auto(event, friend)
            else:
                ReminderToast(self, event, friend, self.event_status)
                self.add_log(f"已提醒 {friend.name}：{event.due_at.strftime('%H:%M')}，等待手动确认。")
        self._tick_job = self.after(60_000, self.tick)

    def event_status(self, event_id: str, status: str) -> None:
        self.scheduler.set_event_status(event_id, status)
        self.save()
        self.refresh()

    def close(self) -> None:
        if self._tick_job is not None:
            self.after_cancel(self._tick_job)
            self._tick_job = None
        if getattr(self, "_clock_job", None) is not None:
            self.after_cancel(self._clock_job)
            self._clock_job = None
        if getattr(self, "_poll_job", None) is not None:
            self.after_cancel(self._poll_job)
            self._poll_job = None
        for event in self.scheduler.events():
            if event.get("status") in {"pending", "auto_queued", "auto_sending"}:
                event["status"] = "needs_review"
        try:
            self.automation.stop()
        except Exception:
            pass
        self.save()
        self.store.close()
        self.destroy()
