from __future__ import annotations

import tkinter as tk
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from douyin_spark.models import Friend, ScheduleSlot
from douyin_spark.storage import EncryptedStore
from douyin_spark.ui import (
    EnablePickerDialog,
    FriendDialog,
    MainWindow,
    Scrollable,
    friend_matches,
    spark_history,
    spark_rows,
    streak_days,
)


def friend_at(name: str, at: str, *, enabled: bool = True, mode: str = "text") -> Friend:
    return Friend(
        name=name,
        consent=True,
        enabled=enabled,
        slots=[ScheduleSlot(at=at, template="hi", mode=mode)],
    )


def event(friend: Friend, slot: ScheduleSlot, date: str, status: str, detail: str = "") -> dict:
    return {
        "event_id": f"{friend.friend_id}-{slot.slot_id}-{date}",
        "friend_id": friend.friend_id,
        "slot_id": slot.slot_id,
        "date": date,
        "due_at": f"{date}T{slot.at}:00",
        "status": status,
        "detail": detail,
    }


class SparkRowsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 11, 15, 20, 0)
        self.today = self.now.date().isoformat()

    def test_sent_slot_marks_friend_as_kept(self) -> None:
        friend = friend_at("甲", "09:00")
        rows = spark_rows([friend], [event(friend, friend.slots[0], self.today, "auto_sent")], self.now)
        self.assertEqual(rows[0]["key"], "sent")
        self.assertEqual(rows[0]["sent"], 1)
        self.assertEqual(rows[0]["total"], 1)

    def test_past_unsent_slot_marks_friend_as_missing(self) -> None:
        friend = friend_at("乙", "09:00")
        rows = spark_rows([friend], [], self.now)
        self.assertEqual(rows[0]["key"], "missed")

    def test_future_slot_is_pending_not_missing(self) -> None:
        friend = friend_at("丙", "23:00")
        rows = spark_rows([friend], [], self.now)
        self.assertEqual(rows[0]["key"], "pending")

    def test_queued_event_is_pending(self) -> None:
        friend = friend_at("丁", "09:00")
        rows = spark_rows([friend], [event(friend, friend.slots[0], self.today, "auto_queued")], self.now)
        self.assertEqual(rows[0]["key"], "pending")

    def test_failed_event_is_flagged(self) -> None:
        friend = friend_at("戊", "09:00")
        rows = spark_rows([friend], [event(friend, friend.slots[0], self.today, "auto_failed")], self.now)
        self.assertEqual(rows[0]["key"], "failed")

    def test_disabled_friend_is_off(self) -> None:
        friend = friend_at("己", "09:00", enabled=False)
        rows = spark_rows([friend], [], self.now)
        self.assertEqual(rows[0]["key"], "off")

    def test_friend_without_slots_is_none(self) -> None:
        friend = Friend(name="庚", consent=True)
        rows = spark_rows([friend], [], self.now)
        self.assertEqual(rows[0]["key"], "none")
        self.assertEqual(rows[0]["total"], 0)

    def test_two_slots_one_sent_counts_as_kept(self) -> None:
        friend = friend_at("辛", "09:00")
        friend.slots.append(ScheduleSlot(at="23:00", template="hi", mode="text"))
        events = [event(friend, friend.slots[0], self.today, "auto_sent")]
        rows = spark_rows([friend], events, self.now)
        self.assertEqual(rows[0]["key"], "sent")
        self.assertEqual(rows[0]["sent"], 1)
        self.assertEqual(rows[0]["total"], 2)
        self.assertEqual(rows[0]["slots"][friend.slots[1].slot_id], "pending")


class EnableControlsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = TemporaryDirectory()
        self.store = EncryptedStore(Path(self.tempdir.name) / "data.db")
        self.window = MainWindow(self.store)

    def tearDown(self) -> None:
        if self.window is not None:
            self.window.update_idletasks()
            self.window.close()
        self.tempdir.cleanup()

    def add_friends(self) -> list[Friend]:
        friends = [friend_at("甲", "09:00"), friend_at("乙", "12:00")]
        for friend in friends:
            self.window.replace_friend(friend)
        return friends

    def test_dialog_picks_single_friend(self) -> None:
        friends = self.add_friends()
        applied: dict[str, bool] = {}
        dialog = EnablePickerDialog(self.window, friends, lambda mapping: applied.update(mapping))
        dialog.update_idletasks()
        dialog._set_all(False)
        dialog.vars[friends[0].friend_id].set(True)
        dialog._apply()
        self.assertEqual(applied[friends[0].friend_id], True)
        self.assertEqual(applied[friends[1].friend_id], False)

    def test_batch_apply_updates_friend_state(self) -> None:
        friends = self.add_friends()
        self.window.apply_enable_map({friends[0].friend_id: False, friends[1].friend_id: True})
        stored = {friend.friend_id: friend for friend in self.window.friends()}
        self.assertFalse(stored[friends[0].friend_id].enabled)
        self.assertTrue(stored[friends[1].friend_id].enabled)

    def test_card_checkbox_matches_friend_state(self) -> None:
        friends = self.add_friends()
        self.window.refresh()
        cards = [child for child in self.window.friend_scroll.inner.winfo_children()]
        self.assertTrue(all(card.enabled_var.get() for card in cards))
        self.window.set_friend_enabled(friends[0], False)
        stored = next(friend for friend in self.window.friends() if friend.friend_id == friends[0].friend_id)
        self.assertFalse(stored.enabled)

    def test_card_checkbox_click_toggles_and_persists(self) -> None:
        friends = self.add_friends()
        self.window.refresh()
        self.window.update()
        card = next(child for child in self.window.friend_scroll.inner.winfo_children())
        card.enable_chk.invoke()
        self.window.update()
        stored = next(friend for friend in self.window.friends() if friend.friend_id == friends[0].friend_id)
        self.assertFalse(stored.enabled)
        self.window.refresh()
        self.window.update()
        row = next(child for child in self.window.friend_scroll.inner.winfo_children())
        self.assertFalse(row.enabled_var.get())


class StreakAndHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.today = date(2026, 11, 15)
        self.friend = friend_at("甲", "09:00")

    def sent(self, days_ago: int, status: str = "auto_sent") -> dict:
        day = (self.today - timedelta(days=days_ago)).isoformat()
        return event(self.friend, self.friend.slots[0], day, status)

    def test_streak_counts_consecutive_sent_days(self) -> None:
        events = [self.sent(0), self.sent(1), self.sent(2)]
        self.assertEqual(streak_days(self.friend, events, self.today), 3)

    def test_streak_stops_at_a_gap(self) -> None:
        events = [self.sent(0), self.sent(1), self.sent(3)]
        self.assertEqual(streak_days(self.friend, events, self.today), 2)

    def test_streak_counts_until_yesterday_when_today_unsent(self) -> None:
        events = [self.sent(1), self.sent(2)]
        self.assertEqual(streak_days(self.friend, events, self.today), 2)

    def test_streak_is_zero_without_sent_events(self) -> None:
        events = [self.sent(0, "auto_failed")]
        self.assertEqual(streak_days(self.friend, events, self.today), 0)

    def test_history_marks_sent_and_missed_days(self) -> None:
        events = [self.sent(0), self.sent(2, "auto_skipped")]
        history = spark_history(self.friend, events, self.today, 4)
        self.assertEqual(len(history), 4)
        self.assertEqual(history[-1]["date"], self.today.isoformat())
        self.assertEqual(history[-1]["sent"], 1)
        self.assertEqual(history[-2]["plan"], 0)
        self.assertEqual(history[-3]["plan"], 1)
        self.assertEqual(history[-3]["sent"], 0)


class FriendSearchTests(unittest.TestCase):
    def test_matches_name_template_and_time(self) -> None:
        friend = Friend(name="示例三分钟", consent=True, slots=[ScheduleSlot(at="09:00", template="早安，今天也要好好的")])
        self.assertTrue(friend_matches(friend, "三分钟"))
        self.assertTrue(friend_matches(friend, "早安"))
        self.assertTrue(friend_matches(friend, "09:00"))
        self.assertTrue(friend_matches(friend, "  "))

    def test_does_not_match_unrelated_text(self) -> None:
        friend = friend_at("示例好友乙", "12:00")
        self.assertFalse(friend_matches(friend, "示例好友丙"))


class ScrollWheelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.geometry("300x160")
        self.area = Scrollable(self.root, bg="#ffffff")
        self.area.pack(fill="both", expand=True)
        inner = self.area.inner
        for _ in range(40):
            tk.Label(inner, text="内容", bg="#ffffff", height=2).pack(fill="x")
        self.root.update()

    def tearDown(self) -> None:
        self.root.destroy()

    def test_scroll_units_moves_the_view(self) -> None:
        self.assertAlmostEqual(self.area.canvas.yview()[0], 0.0, places=3)
        self.area.scroll_units(3)
        self.root.update()
        self.assertGreater(self.area.canvas.yview()[0], 0.0)

    def test_scroll_page_and_edges(self) -> None:
        self.area.scroll_page(1)
        self.root.update()
        middle = self.area.canvas.yview()[0]
        self.assertGreater(middle, 0.0)
        self.area.scroll_to(0.0)
        self.root.update()
        self.assertEqual(self.area.canvas.yview()[0], 0.0)
        self.area.scroll_to(1.0)
        self.root.update()
        self.assertEqual(self.area.canvas.yview()[1], 1.0)


class WheelSurvivesDialogTests(unittest.TestCase):
    """打开并关闭编辑好友弹窗后，主窗口的全局滚轮绑定必须还在。"""

    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.geometry("320x180")
        self.area = Scrollable(self.root, bg="#ffffff")
        self.area.pack(fill="both", expand=True)
        for _ in range(30):
            tk.Label(self.area.inner, text="内容", bg="#ffffff", height=2).pack(fill="x")
        self.root.update()

    def tearDown(self) -> None:
        self.root.destroy()

    def test_global_wheel_binding_survives_dialog_close(self) -> None:
        self.assertTrue(self.root.bind_all("<MouseWheel>"))
        dialog = FriendDialog(self.root, None, lambda friend: None, [])
        self.root.update()
        dialog.destroy()
        self.root.update()
        self.assertTrue(self.root.bind_all("<MouseWheel>"))
        self.area.scroll_units(3)
        self.root.update()
        self.assertGreater(self.area.canvas.yview()[0], 0.0)


if __name__ == "__main__":
    unittest.main()
