from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from datetime import datetime, timedelta

from douyin_spark.auto import AutomationService, Job
from douyin_spark.models import Friend, ScheduleSlot
from douyin_spark.scheduler import Scheduler


class FakeSession:
    def __init__(self) -> None:
        self.texts: list[tuple[str, str, str]] = []
        self.images: list[tuple[str, str]] = []
        self.stopped = False

    @property
    def alive(self) -> bool:
        return not self.stopped

    def open_chat(self) -> str:
        return "ok"

    def send_text(self, nickname: str, message: str, emoji: str = "", **_kwargs):
        self.texts.append((nickname, message, emoji))
        return "sent", "已在消息列表确认"

    def send_image(self, nickname: str, path: str, **_kwargs):
        self.images.append((nickname, path))
        return "sent", "仅发送表情图片"

    def stop(self) -> None:
        self.stopped = True


def make_friend(mode: str, template: str = "你好", emoji: str = "sticker.png") -> Friend:
    return Friend(
        name="示例三分钟",
        consent=True,
        slots=[ScheduleSlot(at="09:00", template=template, emoji_path=emoji, mode=mode)],
    )


def state_for(friend: Friend) -> dict:
    return {"friends": [friend.to_dict()], "events": []}


def wait_results(service: AutomationService, timeout: float = 5.0) -> list:
    deadline = time.monotonic() + timeout
    items: list = []
    while time.monotonic() < deadline:
        items.extend(service.poll())
        if items:
            return items
        time.sleep(0.05)
    return items


class SlotModeModelTests(unittest.TestCase):
    def test_default_mode_is_both(self) -> None:
        slot = ScheduleSlot(at="09:00", template="hi", emoji_path="a.png")
        self.assertEqual(slot.mode, "both")
        self.assertTrue(slot.sends_text)
        self.assertTrue(slot.sends_emoji)

    def test_text_and_emoji_are_independent(self) -> None:
        text = ScheduleSlot(at="09:00", template="hi", mode="text")
        emoji = ScheduleSlot(at="09:00", emoji_path="a.png", mode="emoji")
        self.assertTrue(text.sends_text)
        self.assertFalse(text.sends_emoji)
        self.assertFalse(emoji.sends_text)
        self.assertTrue(emoji.sends_emoji)

    def test_legacy_rows_without_mode_are_migrated(self) -> None:
        with_emoji = ScheduleSlot.from_dict({"at": "09:00", "template": "hi", "emoji_path": "a.png"})
        without = ScheduleSlot.from_dict({"at": "09:00", "template": "hi"})
        self.assertEqual(with_emoji.mode, "both")
        self.assertEqual(without.mode, "text")

    def test_to_dict_roundtrip_keeps_mode(self) -> None:
        slot = ScheduleSlot(at="09:00", template="", emoji_path="a.png", mode="emoji")
        again = ScheduleSlot.from_dict(slot.to_dict())
        self.assertEqual(again.mode, "emoji")
        self.assertEqual(again.emoji_path, "a.png")
        self.assertEqual(again.template, "")


class SchedulerModeTests(unittest.TestCase):
    def test_text_only_slot_has_no_emoji(self) -> None:
        scheduler = Scheduler(state_for(make_friend("text")))
        now = datetime(2026, 11, 14, 9, 5)
        events = scheduler.due_events(now)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].mode, "text")
        self.assertTrue(events[0].message)
        self.assertEqual(events[0].emoji_path, "")

    def test_emoji_only_slot_has_no_message(self) -> None:
        scheduler = Scheduler(state_for(make_friend("emoji")))
        events = scheduler.due_events(datetime(2026, 11, 14, 9, 5))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].mode, "emoji")
        self.assertEqual(events[0].message, "")
        self.assertEqual(events[0].emoji_path, "sticker.png")

    def test_both_slot_sends_text_and_emoji(self) -> None:
        scheduler = Scheduler(state_for(make_friend("both")))
        events = scheduler.due_events(datetime(2026, 11, 14, 9, 5))
        self.assertEqual(events[0].mode, "both")
        self.assertTrue(events[0].message)
        self.assertEqual(events[0].emoji_path, "sticker.png")


class AutoModeDispatchTests(unittest.TestCase):
    def run_job(self, job: Job) -> tuple[AutomationService, FakeSession, list]:
        session = FakeSession()
        service = AutomationService(session_factory=lambda: session, generator=lambda *a, **k: "AI 文案")
        service.start()
        service.submit(job)
        results = wait_results(service)
        service.stop()
        return service, session, results

    def test_emoji_only_uses_image_pipeline(self) -> None:
        sticker = Path(tempfile.gettempdir()) / "slot-mode-sticker.png"
        sticker.write_bytes(b"\x89PNG\r\n\x1a\n")
        job = Job(
            kind="auto",
            event_id="e1",
            nickname="示例三分钟",
            message="",
            emoji=str(sticker),
            mode="emoji",
            approved=True,
        )
        _, session, results = self.run_job(job)
        self.assertEqual(results[0].status, "sent")
        self.assertEqual(results[0].mode, "emoji")
        self.assertEqual(len(session.images), 1)
        self.assertEqual(len(session.texts), 0)

    def test_unicode_emoji_only_goes_through_text(self) -> None:
        job = Job(kind="auto", event_id="e2", nickname="示例三分钟", emoji="🔥", mode="emoji", approved=True)
        _, session, results = self.run_job(job)
        self.assertEqual(results[0].status, "sent")
        self.assertEqual(len(session.images), 0)
        self.assertEqual(session.texts[0][1], "🔥")

    def test_text_only_ignores_emoji_argument(self) -> None:
        job = Job(
            kind="auto",
            event_id="e3",
            nickname="示例三分钟",
            message="在吗？",
            emoji="C:\\temp\\spark-test.png",
            mode="text",
            approved=True,
        )
        _, session, results = self.run_job(job)
        self.assertEqual(len(session.images), 0)
        self.assertEqual(session.texts[0][1], "在吗？")
        self.assertEqual(session.texts[0][2], "")


class NextDueSchedulingTests(unittest.TestCase):
    def test_next_due_respects_slot_mode_slots(self) -> None:
        friend = make_friend("emoji")
        scheduler = Scheduler(state_for(friend))
        now = datetime(2026, 11, 14, 8, 0)
        nxt = scheduler.next_due(friend, now)
        self.assertEqual(nxt, datetime(2026, 11, 14, 9, 0))
        self.assertLess(nxt - now, timedelta(hours=2))


if __name__ == "__main__":
    unittest.main()
