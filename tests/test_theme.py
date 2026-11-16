"""Palette invariants: every theme must define every token the UI paints with."""

import unittest

from douyin_spark import theme
from douyin_spark import ui


def _luminance(value: str) -> float:
    channels = [int(value[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


class ThemePaletteTests(unittest.TestCase):
    def setUp(self) -> None:
        theme.apply("light")

    def tearDown(self) -> None:
        theme.apply("light")

    def test_all_themes_are_complete(self) -> None:
        for key in theme.ORDER:
            colors = theme.THEMES[key]["colors"]
            for token in ui._THEME_KEYS:
                self.assertIn(token, colors, f"{key} is missing {token}")
                self.assertRegex(str(colors[token]), r"^#[0-9a-f]{6}$")

    def test_every_palette_has_on_accent_and_on_nav(self) -> None:
        for key in theme.ORDER:
            colors = theme.THEMES[key]["colors"]
            self.assertRegex(colors["ON_ACCENT"], r"^#[0-9a-f]{6}$")
            self.assertRegex(colors["ON_NAV"], r"^#[0-9a-f]{6}$")

    def test_dark_theme_inverts_a_light_surface(self) -> None:
        theme.apply("carbon")
        painted = theme.paint("#f9fbfe")
        self.assertNotEqual(painted, "#f9fbfe")
        self.assertLess(_luminance(painted), 0.25)

    def test_dark_theme_keeps_white_for_foregrounds(self) -> None:
        theme.apply("carbon")
        self.assertEqual(theme.paint("#ffffff"), "#ffffff")

    def test_dark_theme_lifts_dark_text(self) -> None:
        theme.apply("royal")
        painted = theme.paint("#141e2b")
        self.assertGreater(_luminance(painted), 0.5)

    def test_nav_text_literals_map_to_nav_token(self) -> None:
        theme.apply("midnight")
        self.assertEqual(theme.paint("#b7c6d8"), theme.THEMES["midnight"]["colors"]["NAV_TEXT"])
        self.assertEqual(theme.paint("#8fa4bd"), theme.THEMES["midnight"]["colors"]["NAV_TEXT"])

    def test_light_theme_is_identity(self) -> None:
        for value in ("#f3f6fa", "#b7c6d8", "#cf4527"):
            self.assertEqual(theme.paint(value), value)

    def test_use_theme_rebinds_module_constants(self) -> None:
        name = ui.use_theme("carbon")
        self.assertEqual(name, "carbon")
        self.assertEqual(ui.BG, theme.THEMES["carbon"]["colors"]["BG"])
        self.assertEqual(ui.ON_ACCENT, theme.THEMES["carbon"]["colors"]["ON_ACCENT"])
        ui.use_theme("light")
        self.assertEqual(ui.BG, "#f3f6fa")

    def test_available_lists_all_themes_in_order(self) -> None:
        keys = [item[0] for item in theme.available()]
        self.assertEqual(keys, list(theme.ORDER))
        self.assertEqual(len(keys), 5)


if __name__ == "__main__":
    unittest.main()
