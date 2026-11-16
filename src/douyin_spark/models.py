from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import uuid4


WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def new_id() -> str:
    return uuid4().hex


@dataclass
class ScheduleSlot:
    slot_id: str = field(default_factory=new_id)
    # -1 means a daily slot; non-negative values are retained for legacy weekly data.
    day: int = -1
    at: str = "09:00"
    template: str = "你好，{nickname}"
    emoji_path: str = ""
    enabled: bool = True
    # text | emoji | both — either part can be sent on its own.
    mode: str = "both"

    def parsed_time(self) -> time:
        return datetime.strptime(self.at, "%H:%M").time()

    @property
    def sends_text(self) -> bool:
        return self.mode in ("text", "both")

    @property
    def sends_emoji(self) -> bool:
        return self.mode in ("emoji", "both") and bool(self.emoji_path)

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "day": self.day,
            "at": self.at,
            "template": self.template,
            "emoji_path": self.emoji_path,
            "enabled": self.enabled,
            "mode": self.mode,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScheduleSlot":
        emoji_path = str(data.get("emoji_path", ""))
        mode = str(data.get("mode") or ("both" if emoji_path else "text"))
        if mode not in ("text", "emoji", "both"):
            mode = "both" if emoji_path else "text"
        return cls(
            slot_id=str(data.get("slot_id") or new_id()),
            day=int(data.get("day", -1)),
            at=str(data.get("at", "09:00")),
            template=str(data.get("template", "你好，{nickname}")),
            emoji_path=emoji_path,
            enabled=bool(data.get("enabled", True)),
            mode=mode,
        )


@dataclass
class Friend:
    friend_id: str = field(default_factory=new_id)
    name: str = ""
    douyin_id: str = ""
    conversation_url: str = ""
    consent: bool = False
    enabled: bool = True
    daily_limit: int = 3
    auto_approved: bool = False
    recent_sent: list[str] = field(default_factory=list)
    slots: list[ScheduleSlot] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "friend_id": self.friend_id,
            "name": self.name,
            "douyin_id": self.douyin_id,
            "conversation_url": self.conversation_url,
            "consent": self.consent,
            "enabled": self.enabled,
            "daily_limit": self.daily_limit,
            "auto_approved": self.auto_approved,
            "recent_sent": self.recent_sent[-8:],
            "slots": [slot.to_dict() for slot in self.slots],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Friend":
        return cls(
            friend_id=str(data.get("friend_id") or new_id()),
            name=str(data.get("name", "")),
            douyin_id=str(data.get("douyin_id", "")),
            conversation_url=str(data.get("conversation_url", "")),
            consent=bool(data.get("consent", False)),
            enabled=bool(data.get("enabled", True)),
            daily_limit=max(1, int(data.get("daily_limit", 3))),
            auto_approved=bool(data.get("auto_approved", False)),
            recent_sent=[str(item) for item in data.get("recent_sent", [])][-8:],
            slots=[ScheduleSlot.from_dict(slot) for slot in data.get("slots", [])],
        )


def render_template(template: str, friend: Friend, when: datetime) -> str:
    values = {
        "nickname": friend.name,
        "date": when.strftime("%Y-%m-%d"),
        "weekday": WEEKDAYS[when.weekday()],
    }
    try:
        return template.format(**values)
    except KeyError as exc:
        raise ValueError(f"模板变量不支持: {{{exc.args[0]}}}") from exc


def next_slot_time(slot: ScheduleSlot, start: datetime) -> datetime:
    """Return the next occurrence of a daily or legacy weekly slot."""
    target_time = slot.parsed_time()
    if slot.day < 0:
        candidate = datetime.combine(start.date(), target_time)
        return candidate if candidate >= start else candidate + timedelta(days=1)
    for offset in range(8):
        candidate_date = (start + timedelta(days=offset)).date()
        if candidate_date.weekday() != slot.day:
            continue
        candidate = datetime.combine(candidate_date, target_time)
        if candidate >= start:
            return candidate
    raise RuntimeError("could not calculate next slot")
