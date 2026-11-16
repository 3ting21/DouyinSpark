from datetime import datetime
import unittest

from douyin_spark.models import Friend, ScheduleSlot
from douyin_spark.scheduler import Scheduler


class OfficialUrlTests(unittest.TestCase):
    def test_chat_page_is_preserved(self):
        from douyin_spark.urls import DOUYIN_CHAT_URL, official_douyin_url

        self.assertEqual(official_douyin_url(DOUYIN_CHAT_URL), DOUYIN_CHAT_URL)
        self.assertEqual(official_douyin_url("https://www.douyin.com/message"), DOUYIN_CHAT_URL)

    def test_non_douyin_domain_is_rejected(self):
        from douyin_spark.urls import DOUYIN_HOME_URL, official_douyin_url

        self.assertEqual(official_douyin_url("https://notdouyin.com/chat"), DOUYIN_HOME_URL)


class SchedulerTests(unittest.TestCase):
    def make_scheduler(self, slots, limit=3):
        friend = Friend(friend_id="f1", name="小明", consent=True, daily_limit=limit, slots=slots)
        return Scheduler({"friends": [friend.to_dict()], "events": []})

    def test_multiple_slots_same_day_are_due_once_each(self):
        scheduler = self.make_scheduler([
            ScheduleSlot(slot_id="a", day=0, at="09:00", template="早上好，{nickname}"),
            ScheduleSlot(slot_id="b", day=0, at="15:00", template="下午好，{nickname}"),
        ])
        now = datetime(2026, 10, 5, 16, 0)  # Monday
        due = scheduler.due_events(now)
        self.assertEqual([event.slot_id for event in due], ["a", "b"])
        self.assertEqual(due[0].message, "早上好，小明")
        self.assertEqual(scheduler.due_events(now), [])

    def test_daily_limit_is_per_friend(self):
        scheduler = self.make_scheduler([
            ScheduleSlot(slot_id="a", day=0, at="09:00"),
            ScheduleSlot(slot_id="b", day=0, at="12:00"),
            ScheduleSlot(slot_id="c", day=0, at="15:00"),
        ], limit=2)
        due = scheduler.due_events(datetime(2026, 10, 5, 18, 0))
        self.assertEqual(len(due), 2)
        self.assertEqual(sum(1 for event in scheduler.events() if event["status"] == "skipped_limit"), 1)

    def test_different_days_are_independent(self):
        scheduler = self.make_scheduler([
            ScheduleSlot(slot_id="mon", day=0, at="09:00"),
            ScheduleSlot(slot_id="tue", day=1, at="09:00"),
        ])
        self.assertEqual(len(scheduler.due_events(datetime(2026, 10, 5, 10, 0))), 1)
        self.assertEqual(len(scheduler.due_events(datetime(2026, 10, 6, 10, 0))), 1)

    def test_manual_round_prepares_one_draft_per_friend(self):
        scheduler = self.make_scheduler([
            ScheduleSlot(slot_id="a", day=0, at="09:00", template="你好，{nickname}"),
            ScheduleSlot(slot_id="b", day=0, at="15:00", template="下午好，{nickname}"),
        ])
        prepared = scheduler.prepare_round(datetime(2026, 10, 5, 12, 0))
        self.assertEqual(len(prepared), 1)
        self.assertEqual(prepared[0].message, "你好，小明")
        self.assertEqual(scheduler.events()[0]["source"], "manual_round")

    def test_daily_slot_does_not_depend_on_weekday(self):
        scheduler = self.make_scheduler([
            ScheduleSlot(slot_id="daily", day=-1, at="09:00", template="早安，{nickname}"),
        ])
        self.assertEqual(len(scheduler.due_events(datetime(2026, 10, 9, 10, 0))), 1)

    def test_invalid_template_does_not_consume_the_reminder(self):
        scheduler = self.make_scheduler([
            ScheduleSlot(slot_id="broken", day=-1, at="09:00", template="早安，{not_a_field}"),
        ])
        with self.assertRaisesRegex(ValueError, "模板变量不支持"):
            scheduler.due_events(datetime(2026, 10, 9, 10, 0))
        self.assertEqual(scheduler.events(), [])


if __name__ == "__main__":
    unittest.main()
