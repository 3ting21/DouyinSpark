"""Theme palette for the desktop window.

Every colour in the UI goes through :func:`paint` so a theme switch only needs
to rebuild the widgets once; nothing is hard-coded at paint time.
"""

from __future__ import annotations

import colorsys

THEMES: dict[str, dict[str, object]] = {
    "light": {
        "label": "官方白 · 浅色",
        "hint": "现在的默认外观：冷白底 + 深藏青侧栏",
        "polarity": "light",
        "colors": {
            "BG": "#f3f6fa",
            "SURFACE": "#ffffff",
            "PANEL_SOFT": "#fbfcfe",
            "SOFT": "#eff3f8",
            "LINE": "#dce3ed",
            "INK": "#141e2b",
            "MUTED": "#5c6878",
            "NAV": "#0d1f33",
            "NAV_HOVER": "#16293f",
            "NAV_SELECTED": "#1a3451",
            "NAV_TEXT": "#93a4ba",
            "ACCENT": "#1b4a75",
            "ACCENT_HOVER": "#15395c",
            "SUCCESS": "#186046",
            "WARNING": "#8a5c17",
            "DANGER": "#9c2c3a",
            "INFO": "#2a5a88",
            "WARM_BG": "#fff8f2",
            "WARM_LINE": "#f0dccd",
            "WARM_INK": "#342a24",
            "WARM_MUTED": "#8a7466",
            "CORAL": "#cf4527",
            "CORAL_DARK": "#a8341b",
            "CORAL_SOFT": "#fdeee7",
            "ON_ACCENT": "#ffffff",
            "ON_NAV": "#ffffff",
            "ON_NAV": "#ffffff",
            "ON_NAV": "#ffffff",
        },
    },
    "midnight": {
        "label": "深空蓝 · 深色",
        "hint": "近黑蓝底 + 亮蓝强调，冷峻、对比强",
        "polarity": "dark",
        "colors": {
            "BG": "#070b12",
            "SURFACE": "#0f1724",
            "PANEL_SOFT": "#111c2c",
            "SOFT": "#172335",
            "LINE": "#22334a",
            "INK": "#e9f1fb",
            "MUTED": "#8ea5c0",
            "NAV": "#04070d",
            "NAV_HOVER": "#101a29",
            "NAV_SELECTED": "#172740",
            "NAV_TEXT": "#7f95b0",
            "ACCENT": "#3d7dff",
            "ACCENT_HOVER": "#2b63d6",
            "SUCCESS": "#38d9a0",
            "WARNING": "#f0b84c",
            "DANGER": "#ff6b81",
            "INFO": "#6cb6ff",
            "WARM_BG": "#121a26",
            "WARM_LINE": "#2a3a50",
            "WARM_INK": "#eaf0f8",
            "WARM_MUTED": "#9fb0c4",
            "CORAL": "#ff6a3d",
            "CORAL_DARK": "#e2532a",
            "CORAL_SOFT": "#2a1916",
            "ON_ACCENT": "#ffffff",
            "ON_NAV": "#ffffff",
            "ON_NAV": "#ffffff",
            "ON_NAV": "#ffffff",
        },
    },
    "carbon": {
        "label": "石墨黑 · 深色",
        "hint": "纯黑灰底 + 荧光青强调，最硬的高对比",
        "polarity": "dark",
        "colors": {
            "BG": "#08080a",
            "SURFACE": "#131316",
            "PANEL_SOFT": "#16161a",
            "SOFT": "#1d1d22",
            "LINE": "#2b2b33",
            "INK": "#f2f3f5",
            "MUTED": "#a0a1ad",
            "NAV": "#020203",
            "NAV_HOVER": "#141418",
            "NAV_SELECTED": "#1e1e25",
            "NAV_TEXT": "#8f909c",
            "ACCENT": "#22d3ee",
            "ACCENT_HOVER": "#0fb6d1",
            "SUCCESS": "#4ade80",
            "WARNING": "#fbbf24",
            "DANGER": "#f87171",
            "INFO": "#67e8f9",
            "WARM_BG": "#141416",
            "WARM_LINE": "#2c2c33",
            "WARM_INK": "#f4f4f6",
            "WARM_MUTED": "#a8a8b3",
            "CORAL": "#22d3ee",
            "CORAL_DARK": "#12a8bf",
            "CORAL_SOFT": "#10262a",
            "ON_ACCENT": "#04161a",
            "ON_NAV": "#ffffff",
        },
    },
    "royal": {
        "label": "曜石鎏金 · 深色",
        "hint": "黑曜底 + 香槟金强调，正式、贵气",
        "polarity": "dark",
        "colors": {
            "BG": "#0b0906",
            "SURFACE": "#15110b",
            "PANEL_SOFT": "#19140d",
            "SOFT": "#211a11",
            "LINE": "#382d1c",
            "INK": "#f7efdf",
            "MUTED": "#b6a486",
            "NAV": "#070502",
            "NAV_HOVER": "#1b150d",
            "NAV_SELECTED": "#2a2013",
            "NAV_TEXT": "#a9987a",
            "ACCENT": "#d4af37",
            "ACCENT_HOVER": "#b8932a",
            "SUCCESS": "#c9b25a",
            "WARNING": "#e0a03a",
            "DANGER": "#e2726a",
            "INFO": "#cfc09a",
            "WARM_BG": "#191309",
            "WARM_LINE": "#40311a",
            "WARM_INK": "#f8f0df",
            "WARM_MUTED": "#c0ac87",
            "CORAL": "#d4af37",
            "CORAL_DARK": "#a9862a",
            "CORAL_SOFT": "#2a1f0f",
            "ON_ACCENT": "#171106",
            "ON_NAV": "#f7efdf",
        },
    },
    "sand": {
        "label": "暖砂纸 · 浅色",
        "hint": "米砂底 + 深棕强调，柔和但不廉价",
        "polarity": "light",
        "colors": {
            "BG": "#f4efe4",
            "SURFACE": "#fffdf8",
            "PANEL_SOFT": "#fbf7ee",
            "SOFT": "#f2ebdd",
            "LINE": "#dfd4c0",
            "INK": "#241d14",
            "MUTED": "#6f6353",
            "NAV": "#2a2118",
            "NAV_HOVER": "#3a2e21",
            "NAV_SELECTED": "#463726",
            "NAV_TEXT": "#b3a58f",
            "ACCENT": "#8a5a2b",
            "ACCENT_HOVER": "#6f471f",
            "SUCCESS": "#2f6b47",
            "WARNING": "#8a5c17",
            "DANGER": "#9c3a2c",
            "INFO": "#5b6f8a",
            "WARM_BG": "#fff7ec",
            "WARM_LINE": "#ecd9c1",
            "WARM_INK": "#33261a",
            "WARM_MUTED": "#8d7862",
            "CORAL": "#c25a2c",
            "CORAL_DARK": "#9c451f",
            "CORAL_SOFT": "#fbe9dd",
            "ON_ACCENT": "#ffffff",
            "ON_NAV": "#ffffff",
            "ON_NAV": "#ffffff",
            "ON_NAV": "#ffffff",
        },
    },
}

ORDER = ("light", "sand", "midnight", "carbon", "royal")

_current = "light"
_palette: dict[str, str] = {}
_cache: dict[str, str] = {}

# Values that always mean "the light on top" rather than "the page colour".
_KEEP = {"#ffffff": "#ffffff"}


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _hex_to_rgb(value: str) -> tuple[float, float, float]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def _rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(int(_clamp(channel) * 255) for channel in rgb))


def _mix(first: str, second: str, amount: float) -> str:
    a, b = _hex_to_rgb(first), _hex_to_rgb(second)
    return _rgb_to_hex(tuple(a[i] * (1 - amount) + b[i] * amount for i in range(3)))


def _invert(value: str, palette: dict[str, str]) -> str:
    """Map a light-theme colour onto the dark palette by its role."""
    if value in _PALETTE_OVERRIDES:
        return _PALETTE_OVERRIDES[value](palette)
    hue, light, saturation = colorsys.rgb_to_hls(*_hex_to_rgb(value))
    surface, bg, line = palette["SURFACE"], palette["BG"], palette["LINE"]
    ink, muted, accent = palette["INK"], palette["MUTED"], palette["ACCENT"]
    if light >= 0.93:
        # Background tiers: keep them distinct but all near the surface.
        return _mix(surface, bg, min(0.9, (1.0 - light) * 6))
    if light >= 0.78:
        return line
    if light >= 0.55:
        if saturation >= 0.22:
            return _mix(accent, surface, 0.55 if light > 0.72 else 0.35)
        return muted
    if light >= 0.34:
        if saturation >= 0.35:
            # A dark accent turns into a bright, readable version of itself.
            bright = colorsys.hls_to_rgb(hue, 0.62, min(0.85, saturation + 0.18))
            return _rgb_to_hex(bright)
        return _mix(ink, muted, 0.55)
    if saturation >= 0.35:
        bright = colorsys.hls_to_rgb(hue, 0.66, min(0.9, saturation + 0.12))
        return _rgb_to_hex(bright)
    return ink


_PALETTE_OVERRIDES = {
    "#fffdfb": lambda p: p["SURFACE"],
    "#fff6f5": lambda p: _mix(p["DANGER"], p["SURFACE"], 0.12),
    "#faf1f2": lambda p: _mix(p["DANGER"], p["SURFACE"], 0.10),
    "#fbeeef": lambda p: _mix(p["DANGER"], p["SURFACE"], 0.12),
    "#ecd3d8": lambda p: _mix(p["DANGER"], p["SURFACE"], 0.30),
    "#eef7f2": lambda p: _mix(p["SUCCESS"], p["SURFACE"], 0.12),
    "#a9d9bd": lambda p: _mix(p["SUCCESS"], p["SURFACE"], 0.35),
    "#fbf4e8": lambda p: _mix(p["WARNING"], p["SURFACE"], 0.12),
    "#eadfc0": lambda p: _mix(p["WARNING"], p["SURFACE"], 0.28),
    "#e3edfb": lambda p: _mix(p["ACCENT"], p["SURFACE"], 0.12),
    "#dbeafe": lambda p: _mix(p["ACCENT"], p["SURFACE"], 0.18),
    "#fdeee7": lambda p: _mix(p["CORAL"], p["SURFACE"], 0.12),
    "#fff8f2": lambda p: p["WARM_BG"],
    "#f0dccd": lambda p: p["WARM_LINE"],
    "#8a7466": lambda p: p["WARM_MUTED"],
    "#a8917f": lambda p: _mix(p["WARM_MUTED"], p["SURFACE"], 0.25),
    "#93a4ba": lambda p: p["NAV_TEXT"],
    "#b7c6d8": lambda p: p["NAV_TEXT"],
    "#8fa4bd": lambda p: p["NAV_TEXT"],
    "#8fa0b6": lambda p: p["MUTED"],
    "#8b98ab": lambda p: p["MUTED"],
    "#44546a": lambda p: p["MUTED"],
    "#2a3f58": lambda p: p["INK"],
    "#111827": lambda p: p["INK"],
    "#1c3149": lambda p: p["LINE"],
    "#dce3ed": lambda p: p["LINE"],
    "#f9fbfe": lambda p: p["PANEL_SOFT"],
    "#f8fbff": lambda p: p["SURFACE"],
    "#f5f8fd": lambda p: p["SURFACE"],
    "#f4f7fc": lambda p: p["SOFT"],
    "#f1f4f8": lambda p: p["SOFT"],
    "#eef1f5": lambda p: p["LINE"],
    "#fbfcfe": lambda p: p["PANEL_SOFT"],
}


def available() -> list[tuple[str, str, str]]:
    """(key, label, hint) in display order."""
    return [
        (key, str(THEMES[key]["label"]), str(THEMES[key]["hint"]))
        for key in ORDER
        if key in THEMES
    ]


def apply(name: str) -> dict[str, str]:
    """Switch the active palette and return its named colours."""
    global _current, _palette
    if name not in THEMES:
        name = "light"
    _current = name
    _cache.clear()
    _palette = dict(THEMES[name]["colors"])  # type: ignore[arg-type]
    return _palette


def current() -> str:
    return _current


def polarity() -> str:
    return str(THEMES[_current]["polarity"])


def paint(value: str) -> str:
    """Resolve one literal colour against the active theme."""
    value = value.lower()
    if value in _KEEP:
        return _KEEP[value]
    if value in _cache:
        return _cache[value]
    if polarity() == "light":
        result = value
    else:
        result = _invert(value, _palette)
    _cache[value] = result
    return result
