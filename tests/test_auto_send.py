from __future__ import annotations

import time
import unittest
from datetime import datetime

from douyin_spark.aiclient import AIError
from douyin_spark.auto import AutomationService, Job
from douyin_spark.models import Friend, ScheduleSlot
from douyin_spark.scheduler import Scheduler


class FakeSession:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []
        self.stopped = False
        self.fail_next: str | None = None

    @property
    def alive(self) -> bool:
        return not self.stopped

    def open_chat(self) -> str:
        return "ok"

    def probe(self) -> dict:
        return {"login": True, "editor": True, "detail": "ok"}

    def send_text(self, nickname: str, message: str, emoji: str = "", **_kwargs):
        if self.fail_next:
            raise self.fail_next
        self.sent.append((nickname, message, emoji))
        return "sent", "已在消息列表确认"

    def stop(self) -> None:
        self.stopped = True


def wait_results(service: AutomationService, timeout: float = 5.0) -> list:
    deadline = time.monotonic() + timeout
    items: list = []
    while time.monotonic() < deadline:
        items.extend(service.poll())
        if items:
            return items
        time.sleep(0.05)
    return items


def ai_ok(cfg, nickname, template, *, when, recent=None) -> str:
    return f"AI 给{nickname}的消息"


def ai_fail(cfg, nickname, template, *, when, recent=None) -> str:
    raise AIError("模拟失败")


class AutoServiceTests(unittest.TestCase):
    def make_service(self, generator=ai_ok) -> tuple[AutomationService, FakeSession]:
        session = FakeSession()
        service = AutomationService(session_factory=lambda: session, generator=generator, idle_seconds=0)
        service.start()
        return service, session

    def test_approved_job_generates_with_ai_and_sends(self):
        service, session = self.make_service()
        service.submit(
            Job(
                kind="auto",
                event_id="e1",
                nickname="小明",
                fallback="模板文案",
                ai={"enabled": True, "api_key": "k", "max_chars": 32},
                approved=True,
            )
        )
        results = wait_results(service)
        service.stop()
        self.assertEqual(results[0].status, "sent")
        self.assertTrue(results[0].ai_used)
        self.assertEqual(results[0].message, "AI 给小明的消息")
        self.assertEqual(session.sent, [("小明", "AI 给小明的消息", "")])

    def test_unapproved_job_asks_for_confirmation_without_sending(self):
        service, session = self.make_service()
        service.submit(
            Job(kind="auto", event_id="e2", nickname="小红", fallback="模板", ai={"enabled": True}, approved=False)
        )
        results = wait_results(service)
        service.stop()
        self.assertEqual(results[0].status, "needs_confirm")
        self.assertEqual(session.sent, [])

    def test_ai_failure_falls_back_to_template(self):
        service, session = self.make_service(generator=ai_fail)
        service.submit(
            Job(kind="auto", event_id="e3", nickname="小刚", fallback="晚上好", ai={"enabled": True, "api_key": "k"}, approved=True)
        )
        results = wait_results(service)
        service.stop()
        self.assertEqual(results[0].status, "sent")
        self.assertFalse(results[0].ai_used)
        self.assertEqual(session.sent, [("小刚", "晚上好", "")])

    def test_disabled_ai_uses_template_without_calling_model(self):
        service, session = self.make_service(generator=ai_fail)
        service.submit(
            Job(kind="auto", event_id="e4", nickname="小美", fallback="在忙什么", ai={"enabled": False}, approved=True)
        )
        results = wait_results(service)
        service.stop()
        self.assertEqual(results[0].status, "sent")
        self.assertEqual(results[0].message, "在忙什么")

    def test_cancel_pending_drops_queued_jobs(self):
        service, session = self.make_service()
        for index in range(3):
            service.submit(Job(kind="auto", event_id=f"c{index}", nickname="小明", fallback="hi", approved=True))
        service.cancel_pending()
        time.sleep(0.6)
        results = service.poll()
        service.stop()
        self.assertLessEqual(len([item for item in results if item.status == "sent"]), 1)
        self.assertLessEqual(len(session.sent), 1)


class AutoSchedulerTests(unittest.TestCase):
    def make_scheduler(self):
        friend = Friend(
            friend_id="f1",
            name="小明",
            consent=True,
            slots=[ScheduleSlot(slot_id="a", at="09:00", template="早安，{nickname}")],
        )
        return Scheduler({"friends": [friend.to_dict()], "events": []})

    def test_claim_auto_is_single_use(self):
        scheduler = self.make_scheduler()
        events = scheduler.due_events(datetime(2026, 11, 14, 10, 0))
        self.assertEqual(len(events), 1)
        self.assertTrue(scheduler.claim_auto(events[0].event_id))
        self.assertFalse(scheduler.claim_auto(events[0].event_id))
        self.assertEqual(scheduler.events()[0]["status"], "auto_queued")

    def test_auto_statuses_are_settable_and_counted(self):
        scheduler = self.make_scheduler()
        events = scheduler.due_events(datetime(2026, 11, 14, 10, 0))
        scheduler.set_event_status(events[0].event_id, "auto_sent", "ok")
        self.assertEqual(scheduler.events()[0]["status"], "auto_sent")
        self.assertEqual(scheduler._count_day("f1", datetime(2026, 11, 14).date()), 1)

    def test_unknown_status_is_rejected(self):
        scheduler = self.make_scheduler()
        with self.assertRaises(ValueError):
            scheduler.set_event_status("nope", "whatever")

    def test_auto_approved_flag_round_trips(self):
        friend = Friend(name="小明", auto_approved=True, recent_sent=["a", "b"])
        restored = Friend.from_dict(friend.to_dict())
        self.assertTrue(restored.auto_approved)
        self.assertEqual(restored.recent_sent, ["a", "b"])


if __name__ == "__main__":
    unittest.main()
