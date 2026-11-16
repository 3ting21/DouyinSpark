from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime
import tkinter as tk
from tkinter import ttk
import unittest

from douyin_spark.storage import EncryptedStore
from douyin_spark.models import Friend, ScheduleSlot
from douyin_spark.ui import FriendCard, FriendDialog, MainWindow


class FriendSaveTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "data.db"
        self.store = EncryptedStore(self.db_path)
        try:
            self.window = MainWindow(self.store)
        except Exception:
            self.store.close()
            self.tempdir.cleanup()
            raise

    def tearDown(self):
        if self.window is not None:
            self.window.update_idletasks()
            self.window.close()
        self.tempdir.cleanup()

    def test_save_button_visible_at_minimum_height(self):
        dialog = FriendDialog(self.window, None, self.window.replace_friend, [])
        dialog.geometry("900x600")
        dialog.update()
        button = next(
            child
            for frame in dialog.winfo_children()
            for child in frame.winfo_children()
            if isinstance(child, ttk.Button) and child.cget("text") == "保存好友"
        )
        self.assertGreater(button.winfo_height(), 1)
        self.assertLessEqual(
            button.winfo_rooty() + button.winfo_height(),
            dialog.winfo_rooty() + dialog.winfo_height(),
        )
        dialog.destroy()

    def test_compact_layout_keeps_controls_visible_and_scrolls(self):
        self.window.geometry("860x600")
        self.window.update()
        self.assertEqual(int(self.window.friend_count.grid_info()["row"]), 1)
        self.assertEqual(int(self.window.add_button.grid_info()["row"]), 0)
        self.assertGreater(self.window.friend_count.winfo_width(), 100)
        self.assertGreater(self.window.all_enabled_button.winfo_width(), 60)
        self.assertGreater(self.window.right_panel.canvas.winfo_height(), 0)
        region = self.window.right_panel.canvas.bbox(self.window.right_panel.window)
        self.assertGreater(region[3] - region[1], self.window.right_panel.canvas.winfo_height())

        friend = Friend(
            friend_id="responsive",
            name="一位昵称比较长的好友名称用于窄屏布局检查",
            consent=False,
            slots=[ScheduleSlot(slot_id="slot-{}".format(index), at="{:02d}:00".format(8 + index)) for index in range(4)],
        )
        self.window.replace_friend(friend)
        self.window.update()
        card = next(child for child in self.window.friend_scroll.inner.winfo_children() if isinstance(child, FriendCard))
        buttons = [child for frame in card.winfo_children()[0].winfo_children() if isinstance(frame, tk.Frame) for child in frame.winfo_children() if isinstance(child, ttk.Button)]
        self.assertEqual(len(buttons), 2)
        self.assertTrue(all(button.winfo_width() > 40 for button in buttons))
        self.assertTrue(all(button.winfo_rootx() + button.winfo_width() <= card.winfo_rootx() + card.winfo_width() for button in buttons))

        self.window.geometry("1450x850")
        self.window.update()
        self.assertEqual(int(self.window.friend_count.grid_info()["row"]), 0)
        self.assertGreater(self.window.friend_count.winfo_width(), 100)

    def test_dialog_time_row_fits_minimum_width(self):
        dialog = FriendDialog(self.window, None, self.window.replace_friend, [])
        dialog.geometry("560x510")
        dialog.update()
        row = dialog.rows[0]["frame"]
        self.assertLessEqual(row.winfo_width(), dialog.body_canvas.winfo_width())
        for frame in row.winfo_children():
            for child in frame.winfo_children():
                if isinstance(child, ttk.Button):
                    self.assertLessEqual(child.winfo_rootx() + child.winfo_width(), dialog.winfo_rootx() + dialog.winfo_width())
        dialog.destroy()

    def test_add_edit_and_reopen_without_confirming_reminders(self):
        dialog = FriendDialog(self.window, None, self.window.replace_friend, [])
        dialog.name.set("小明")
        dialog.add_slot()
        dialog.rows[0]["message"].set("早安，{nickname}")
        dialog.rows[1]["hour"].set(18)
        dialog.rows[1]["minute"].set(30)
        dialog.rows[1]["message"].set("晚上好")
        dialog.save()

        self.assertEqual(dialog.winfo_exists(), 0)
        friend = self.window.friends()[0]
        self.assertFalse(friend.consent)
        self.assertEqual([slot.at for slot in friend.slots], ["09:00", "18:30"])

        edit = FriendDialog(self.window, friend, self.window.replace_friend, [])
        edit.name.set("小明（修改）")
        edit.save()
        self.assertEqual(edit.winfo_exists(), 0)

        self.window.update_idletasks()
        self.window.close()
        self.window = None
        reopened_store = EncryptedStore(self.db_path)
        self.window = MainWindow(reopened_store)
        self.window.update_idletasks()
        reopened_friend = self.window.friends()[0]
        self.assertEqual(reopened_friend.friend_id, friend.friend_id)
        self.assertEqual(reopened_friend.name, "小明（修改）")
        self.assertEqual([slot.at for slot in reopened_friend.slots], ["09:00", "18:30"])
        self.assertEqual([slot.template for slot in reopened_friend.slots], ["早安，{nickname}", "晚上好"])
        self.assertFalse(reopened_friend.consent)

    def test_douyin_id_is_normalized_and_persisted(self):
        dialog = FriendDialog(self.window, None, self.window.replace_friend, [])
        dialog.name.set("示例小林")
        dialog.douyin.set("@lin_xiaolin")
        dialog.save()

        friend = self.window.friends()[0]
        self.assertEqual(friend.douyin_id, "lin_xiaolin")

        self.window.update_idletasks()
        self.window.close()
        self.window = None
        reopened = EncryptedStore(self.db_path)
        self.window = MainWindow(reopened)
        self.window.update_idletasks()
        self.assertEqual(self.window.friends()[0].douyin_id, "lin_xiaolin")

    def test_either_nickname_or_douyin_id_is_enough(self):
        dialog = FriendDialog(self.window, None, self.window.replace_friend, [])
        dialog.douyin.set("only_id_user")
        dialog.consent.set(True)
        dialog.save()

        friend = self.window.friends()[0]
        self.assertEqual(friend.name, "")
        self.assertEqual(friend.douyin_id, "only_id_user")

        scheduler = self.window.scheduler
        self.assertIsNone(scheduler.next_due(friend, datetime(2026, 11, 15, 8, 0)))
        card = next(child for child in self.window.friend_scroll.inner.winfo_children() if isinstance(child, FriendCard))
        self.assertIn("抖音号 only_id_user", card.name_label.cget("text"))
        texts = [w.cget("text") for f in card.content.winfo_children() for w in f.winfo_children() if isinstance(w, tk.Label)]
        self.assertTrue(any("待补昵称" in text for text in texts))
