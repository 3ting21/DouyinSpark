from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import Friend, ScheduleSlot


def load_reference_config(config_path: Path) -> list[Friend]:
    """Convert the reference package's offline config into local reminder records."""
    config_path = Path(config_path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    sticker_map: dict[str, str] = {}
    stickers_path = config_path.with_name("stickers.json")
    if stickers_path.exists():
        sticker_data = json.loads(stickers_path.read_text(encoding="utf-8"))
        for item in sticker_data.get("items", []):
            sticker_id = str(item.get("id", ""))
            thumb = str(item.get("thumb", ""))
            if sticker_id and thumb:
                sticker_map[sticker_id] = str(config_path.parent / thumb)

    friends: list[Friend] = []
    for item in raw.get("friends", []):
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        messages = [str(value).strip() for value in item.get("messages", []) if str(value).strip()]
        messages = messages or ["你好，{nickname}"]
        sticker_ids = [str(value) for value in item.get("stickers", [])]
        sticker_path = next((sticker_map[value] for value in sticker_ids if value in sticker_map), "")
        slots: list[ScheduleSlot] = []
        for index, at in enumerate(item.get("times", [])):
            at = str(at)
            template = messages[index % len(messages)]
            slots.append(ScheduleSlot(day=-1, at=at, template=template, emoji_path=sticker_path))
        if slots:
            friends.append(
                Friend(
                    name=name,
                    enabled=bool(item.get("enabled", True)),
                    consent=False,
                    daily_limit=max(3, len(slots)),
                    slots=slots,
                )
            )
    return friends
