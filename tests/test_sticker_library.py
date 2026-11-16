from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from douyin_spark.storage import EncryptedStore
from douyin_spark.ui import FriendDialog, MainWindow, StickerDialog, add_sticker_path, save_generated_sticker


def make_png(path: Path, size: int = 12) -> None:
    image = tk.PhotoImage(width=size, height=size)
    image.put("#1f4e79", to=(0, 0, size, size))
    image.write(str(path), format="png")


class StickerLibraryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = TemporaryDirectory()
        base = Path(self.tempdir.name)
        self.store = EncryptedStore(base / "data.db")
        self.window = MainWindow(self.store)
        self.window.update_idletasks()
        self.png = base / "spark.png"
        make_png(self.png)

    def tearDown(self):
        if self.window is not None:
            self.window.update_idletasks()
            self.window.close()
        self.tempdir.cleanup()

    def test_right_panel_drops_dead_buttons_and_moves_library_entry(self):
        def texts_of(widget, classes):
            found = []
            for child in widget.winfo_children():
                try:
                    if isinstance(child, tk.Widget) and child.winfo_class() in classes:
                        found.append(child.cget("text"))
                except tk.TclError:
                    pass
                found.extend(texts_of(child, classes))
            return found

        panel = " | ".join(texts_of(self.window, ("TButton", "Button")))
        self.assertNotIn("导入参考配置", panel)
        self.assertNotIn("同步本地表情包", panel)
        self.assertNotIn("表情库管理", panel)
        self.assertIn("今天，听我的", " | ".join(texts_of(self.window.right_panel, ("Label",))))

        dialog = FriendDialog(self.window, None, self.window.replace_friend, [], open_library=self.window.show_sticker_library)
        dialog.update_idletasks()
        self.assertIn("管理表情库", " | ".join(texts_of(dialog, ("TButton", "Button"))))
        dialog.destroy()

    def test_add_file_updates_state_and_label(self):
        added = self.window.add_sticker_files([str(self.png)])
        self.assertEqual(added, 1)
        self.assertEqual(self.window.sticker_paths(), [str(self.png)])
        self.assertEqual(len(self.window.state["stickers"]), 1)
        self.assertEqual(self.window.add_sticker_files([str(self.png)]), 0)

    def test_dialog_lists_and_removes_stickers(self):
        self.window.add_sticker_files([str(self.png)])
        dialog = StickerDialog(self.window, self.window.state, self.window._stickers_changed, self.window.settings)
        dialog.update_idletasks()
        dialog.reload()
        dialog.update_idletasks()
        self.assertEqual(len(dialog.cards), 1)
        dialog.selected.add(str(self.png))
        dialog.state["stickers"] = []
        dialog.selected.clear()
        dialog.reload()
        self.assertEqual(dialog.cards, {})
        dialog.destroy()

    def test_folders_are_scanned_for_pictures(self):
        folder = Path(self.tempdir.name) / "emoji"
        folder.mkdir()
        make_png(folder / "a.png")
        (folder / "b.gif").write_bytes(b"not really a gif")
        added = self.window.add_sticker_folder(str(folder))
        self.assertEqual(added, 2)

    def test_generated_bytes_are_saved_and_validated(self):
        target = save_generated_sticker(self.png.read_bytes())
        self.assertTrue(target.exists())
        self.assertTrue(target.name.startswith("ai-"))
        target.unlink()

    def test_invalid_bytes_are_rejected(self):
        with self.assertRaises(ValueError):
            save_generated_sticker(b"\x89PNG broken")


class StickerHelperTests(unittest.TestCase):
    def test_add_sticker_path_rejects_duplicates(self):
        class Dummy:
            def __init__(self, state):
                self.state = state

            def sticker_paths(self):
                return [str(path) for path in self.state.get("stickers", [])]

        with TemporaryDirectory() as tmp:
            png = Path(tmp) / "x.png"
            root = tk.Tk()
            root.withdraw()
            try:
                make_png(png)
                dummy = Dummy({"stickers": []})
                self.assertTrue(add_sticker_path(dummy, str(png)))
                self.assertFalse(add_sticker_path(dummy, str(png)))
            finally:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
