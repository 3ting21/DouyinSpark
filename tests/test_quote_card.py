from __future__ import annotations

import json
import unittest
from datetime import date
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from douyin_spark.aiclient import AIError, settings_with_defaults, suggest_quote
from douyin_spark.quotes import QUOTE_POOL, format_quote, next_quote, quote_for
from douyin_spark.storage import EncryptedStore
from douyin_spark.ui import MainWindow


class FakeResponse(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def chat_opener(content: str):
    body = json.dumps({"choices": [{"message": {"content": content}}]}, ensure_ascii=False).encode("utf-8")

    def opener(request, timeout=None):
        return FakeResponse(body)

    return opener


class LocalQuoteTests(unittest.TestCase):
    def test_quote_is_stable_per_day(self):
        day = date(2026, 11, 15)
        self.assertEqual(quote_for(day), quote_for(day))
        self.assertIn(quote_for(day), QUOTE_POOL)

    def test_next_quote_cycles_through_pool(self):
        start = QUOTE_POOL[0][0]
        seen = {start}
        text = start
        for _ in range(len(QUOTE_POOL) - 1):
            text, _author = next_quote(text)
            self.assertNotIn(text, seen)
            seen.add(text)
        text, _author = next_quote(text)
        self.assertEqual(text, start)

    def test_unknown_quote_restarts_pool(self):
        self.assertEqual(next_quote("不存在的句子"), QUOTE_POOL[0])

    def test_format_quote_for_clipboard(self):
        self.assertEqual(format_quote("你好", "佚名"), "“你好” —— 佚名")
        self.assertEqual(format_quote("   ", "佚名"), "")


class AiQuoteTests(unittest.TestCase):
    def settings(self):
        return settings_with_defaults({"api_key": "k", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"})

    def test_parses_two_line_reply(self):
        text, author = suggest_quote(self.settings(), opener=chat_opener("把今天过好。\n佚名"))
        self.assertEqual(text, "把今天过好。")
        self.assertEqual(author, "佚名")

    def test_strips_numbering_and_quotes(self):
        text, _author = suggest_quote(self.settings(), opener=chat_opener("1. “先完成，再完美。”"))
        self.assertEqual(text, "先完成，再完美。")

    def test_empty_reply_is_error(self):
        with self.assertRaises(AIError):
            suggest_quote(self.settings(), opener=chat_opener("   "))


class QuoteCardTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = TemporaryDirectory()
        self.db = Path(self.tempdir.name) / "data.db"
        self.store = EncryptedStore(self.db)
        self.window = MainWindow(self.store)
        self.window.update_idletasks()

    def tearDown(self):
        if self.window is not None:
            self.window.update_idletasks()
            self.window.close()
        self.tempdir.cleanup()

    def test_card_renders_today_quote_and_clock(self):
        self.assertTrue(self.window.quote_text_label.cget("text").strip())
        self.assertRegex(self.window.quote_clock_label.cget("text"), r"\d{2}:\d{2}:\d{2}")
        self.assertIn("年", self.window.quote_date_label.cget("text"))
        self.assertIn(str(date.today().year), self.window.quote_date_label.cget("text"))
        self.assertTrue(self.window.quote_hi_label.cget("text").strip())
        self.assertIn("盯你第", self.window.quote_source_label.cget("text"))
        self.assertTrue(self.window.state["quote"]["date"])
        self.assertTrue(self.window.state["quote"]["first_date"])

    def test_switch_button_changes_quote_and_persists(self):
        before = self.window.quote_text_label.cget("text")
        self.window.next_quote()
        after = self.window.quote_text_label.cget("text")
        self.assertNotEqual(before, after)
        self.assertEqual(self.window.state["quote"]["text"], after)

        self.window.update_idletasks()
        self.window.close()
        self.window = None
        reopened = EncryptedStore(self.db)
        self.window = MainWindow(reopened)
        self.window.update_idletasks()
        self.assertEqual(self.window.state["quote"]["text"], after)

    def test_ai_quote_marks_source(self):
        self.window._apply_ai_quote("别怕慢，只怕站。", "佚名")
        self.assertEqual(self.window.state["quote"]["source"], "ai")
        self.assertIn("AI 写的", self.window.quote_source_label.cget("text"))
        self.assertIn("盯你第", self.window.quote_source_label.cget("text"))

    def test_ai_quote_without_key_does_not_change_text(self):
        from douyin_spark import ui

        before = self.window.quote_text_label.cget("text")
        original = ui.messagebox.showinfo
        ui.messagebox.showinfo = lambda *args, **kwargs: None
        try:
            self.window.ai_quote()
        finally:
            ui.messagebox.showinfo = original
        self.assertEqual(self.window.quote_text_label.cget("text"), before)

    def test_failure_dialog_explains_how_to_probe(self):
        from douyin_spark import ui

        captured = {}
        original = ui.messagebox.showwarning
        ui.messagebox.showwarning = lambda title, message, **kwargs: captured.update(
            title=title, message=message
        )
        try:
            self.window._ai_quote_failed("AI 接口请求超时")
        finally:
            ui.messagebox.showwarning = original
        self.assertIn("测试接口连通性", captured["message"])
        self.assertIn("api.openai.com", captured["message"])
        self.assertIn("智谱", captured["message"])


if __name__ == "__main__":
    unittest.main()
