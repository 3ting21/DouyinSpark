from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from .models import Friend, ScheduleSlot, next_slot_time, render_template

AUTO_STATUSES = {
    "auto_queued",
    "auto_sending",
    "auto_sent",
    "auto_failed",
    "auto_skipped",
    "auto_needs_confirm",
}
BASE_STATUSES = {
    "reminded",
    "pending",
    "sent",
    "needs_review",
    "skipped",
    "skipped_limit",
}
COUNTED_STATUSES = AUTO_STATUSES | BASE_STATUSES
ALLOWED_STATUSES = AUTO_STATUSES | {"reminded", "pending", "sent", "needs_review", "skipped"}


def _sendable(friend: Friend) -> bool:
    """A friend needs a resolvable nickname: auto-send locates threads by name only."""
    return bool(friend.name.strip()) and friend.enabled and friend.consent


@dataclass
class DueEvent:
    event_id: str
    friend_id: str
    slot_id: str
    due_at: datetime
    message: str
    emoji_path: str
    mode: str = "both"


class Scheduler:
    """Daily schedule evaluator for local reminders and user-confirmed drafts."""

    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state

    @property
    def friends(self) -> list[Friend]:
        return [Friend.from_dict(raw) for raw in self.state.get("friends", [])]

    def events(self) -> list[dict[str, Any]]:
        return self.state.setdefault("events", [])

    def _friend(self, friend_id: str) -> Friend:
        for friend in self.friends:
            if friend.friend_id == friend_id:
                return friend
        raise KeyError(friend_id)

    def _count_day(self, friend_id: str, day: date) -> int:
        day_text = day.isoformat()
        return sum(
            1
            for event in self.events()
            if event.get("friend_id") == friend_id
            and event.get("date") == day_text
            and event.get("status") in COUNTED_STATUSES
        )

    def _already_fired(self, friend_id: str, slot_id: str, day: date) -> bool:
        return any(
            event.get("friend_id") == friend_id
            and event.get("slot_id") == slot_id
            and event.get("date") == day.isoformat()
            for event in self.events()
        )

    def next_due(self, friend: Friend, now: datetime) -> datetime | None:
        if not _sendable(friend):
            return None
        candidates = []
        for slot in friend.slots:
            if not slot.enabled:
                continue
            try:
                candidates.append(next_slot_time(slot, now))
            except ValueError:
                continue
        return min(candidates) if candidates else None

    def due_events(self, now: datetime) -> list[DueEvent]:
        due: list[DueEvent] = []
        for friend in self.friends:
            if not _sendable(friend):
                continue
            for slot in friend.slots:
                if not slot.enabled:
                    continue
                try:
                    slot_time = slot.parsed_time()
                except ValueError:
                    continue
                if slot.day >= 0 and slot.day != now.weekday():
                    continue
                due_at = datetime.combine(now.date(), slot_time)
                if due_at > now or self._already_fired(friend.friend_id, slot.slot_id, now.date()):
                    continue
                if self._count_day(friend.friend_id, now.date()) >= friend.daily_limit:
                    self.events().append(
                        {
                            "event_id": f"limit-{friend.friend_id}-{slot.slot_id}-{now.date().isoformat()}",
                            "friend_id": friend.friend_id,
                            "slot_id": slot.slot_id,
                            "date": now.date().isoformat(),
                            "due_at": due_at.isoformat(),
                            "status": "skipped_limit",
                        }
                    )
                    continue
                message = render_template(slot.template, friend, due_at) if slot.sends_text else ""
                event_id = f"{friend.friend_id}-{slot.slot_id}-{now.date().isoformat()}"
                self.events().append(
                    {
                        "event_id": event_id,
                        "friend_id": friend.friend_id,
                        "slot_id": slot.slot_id,
                        "date": now.date().isoformat(),
                        "due_at": due_at.isoformat(),
                        "status": "reminded",
                    }
                )
                due.append(
                    DueEvent(
                        event_id=event_id,
                        friend_id=friend.friend_id,
                        slot_id=slot.slot_id,
                        due_at=due_at,
                        message=message,
                        emoji_path=slot.emoji_path if slot.sends_emoji else "",
                        mode=slot.mode,
                    )
                )
        return due

    def prepare_round(self, now: datetime) -> list[DueEvent]:
        """Prepare one user-confirmed draft per eligible friend right now."""
        prepared: list[DueEvent] = []
        for friend in self.friends:
            if not _sendable(friend):
                continue
            if self._count_day(friend.friend_id, now.date()) >= friend.daily_limit:
                continue
            candidates = [slot for slot in friend.slots if slot.enabled]
            if not candidates:
                continue
            slot = next((item for item in candidates if item.day == now.weekday()), candidates[0])
            event_id = f"manual-{friend.friend_id}-{now.strftime('%Y%m%d%H%M%S%f')}"
            self.events().append(
                {
                    "event_id": event_id,
                    "friend_id": friend.friend_id,
                    "slot_id": slot.slot_id,
                    "date": now.date().isoformat(),
                    "due_at": now.isoformat(),
                    "status": "reminded",
                    "source": "manual_round",
                }
            )
            prepared.append(
                DueEvent(
                    event_id=event_id,
                    friend_id=friend.friend_id,
                    slot_id=slot.slot_id,
                    due_at=now,
                    message=render_template(slot.template, friend, now) if slot.sends_text else "",
                    emoji_path=slot.emoji_path if slot.sends_emoji else "",
                    mode=slot.mode,
                )
            )
        return prepared

    def set_event_status(self, event_id: str, status: str, detail: str = "") -> None:
        if status not in ALLOWED_STATUSES:
            raise ValueError(status)
        for event in self.events():
            if event.get("event_id") == event_id:
                event["status"] = status
                if detail:
                    event["detail"] = detail
                return
        raise KeyError(event_id)

    def claim_auto(self, event_id: str) -> bool:
        """Atomically move a due event into the auto-send pipeline once."""
        for event in self.events():
            if event.get("event_id") == event_id and event.get("status") in {"reminded", "pending"}:
                event["status"] = "auto_queued"
                return True
        return False
