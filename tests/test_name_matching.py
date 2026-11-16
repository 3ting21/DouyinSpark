from __future__ import annotations

import unittest

from douyin_spark.browser import (
    ChatSession,
    ChatUnavailable,
    name_match_score,
    normalize_name,
    pick_name_matches,
)


class NormalizeTests(unittest.TestCase):
    def test_drops_emoji_ellipsis_and_spaces(self):
        self.assertEqual(normalize_name("TestUser🌸"), "testuser")
        self.assertEqual(normalize_name("TestUser…"), "testuser")
        self.assertEqual(normalize_name("示 例 三分钟　✨"), "示例三分钟")

    def test_folds_fullwidth_and_case(self):
        self.assertEqual(normalize_name("ＴｅｓｔＵｓｅｒ"), "testuser")


class ScoreTests(unittest.TestCase):
    def test_identical_after_folding(self):
        self.assertEqual(name_match_score("TestUser", "TestUser 🌸"), 1.0)

    def test_prefix_with_tag(self):
        self.assertGreaterEqual(name_match_score("示例三分钟", "示例三分钟（互关）"), 0.95)

    def test_contains_is_strong(self):
        self.assertGreaterEqual(name_match_score("小林", "示例小林"), 0.90)

    def test_truncated_candidate_still_matches(self):
        self.assertGreaterEqual(name_match_score("TestUser", "TestUs…"), 0.75)

    def test_unrelated_is_zero(self):
        self.assertEqual(name_match_score("示例小林", "示例三分钟"), 0.0)

    def test_short_single_char_is_not_loose(self):
        self.assertLess(name_match_score("林", "示例小林"), 0.90)


class PickTests(unittest.TestCase):
    def blocks(self):
        return [
            {"text": "TestUser🌸", "x": 20, "y": 100, "w": 120, "h": 20},
            {"text": "TestUser·备用", "x": 20, "y": 101, "w": 120, "h": 20},
            {"text": "TestUser…", "x": 20, "y": 240, "w": 90, "h": 20},
            {"text": "示例三分钟", "x": 20, "y": 320, "w": 100, "h": 20},
        ]

    def test_one_row_wins_and_rows_are_deduped(self):
        hits = pick_name_matches("TestUser", self.blocks())
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0]["y"], 100)
        self.assertGreaterEqual(hits[0]["score"], 0.99)

    def test_ambiguous_lookalikes_are_refused(self):
        hits = pick_name_matches("TestUser", self.blocks())
        with self.assertRaises(ChatUnavailable):
            ChatSession._pick_thread(object(), hits, "TestUser")

    def test_clear_winner_is_accepted(self):
        hits = pick_name_matches("示例三分钟", self.blocks())
        chosen = ChatSession._pick_thread(object(), hits, "示例三分钟")
        self.assertEqual(chosen["text"], "示例三分钟")


if __name__ == "__main__":
    unittest.main()
