from __future__ import annotations

import json
import unittest
import urllib.error
from datetime import datetime
from io import BytesIO

from douyin_spark.aiclient import (
    AIError,
    apply_provider,
    chat_text,
    generate_image,
    generate_message,
    generate_variants,
    probe_chat,
    sanitize,
    settings_with_defaults,
    sticker_prompt,
    suggest_quote,
)


class FakeResponse(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def opener_for(payload: dict, captured: list | None = None):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def opener(request, timeout=None):
        if captured is not None:
            captured.append(request)
        return FakeResponse(body)

    return opener


class SanitizeTests(unittest.TestCase):
    def test_strips_quotes_and_collapses_lines(self):
        self.assertEqual(sanitize('“今天也要开心”', 32), "今天也要开心")
        self.assertEqual(sanitize("第一条\n第二条", 32), "第一条 第二条")

    def test_trims_to_max_chars(self):
        self.assertEqual(len(sanitize("啊" * 40, 10)), 10)

    def test_rejects_empty_and_unrendered_template(self):
        with self.assertRaises(AIError):
            sanitize("   ", 32)
        with self.assertRaises(AIError):
            sanitize("你好{nickname}", 32)

    def test_strips_common_prefix(self):
        self.assertEqual(sanitize("消息：在干嘛呢", 32), "在干嘛呢")


class GenerateTests(unittest.TestCase):
    def settings(self, **overrides):
        base = settings_with_defaults(
            {
                "enabled": True,
                "api_key": "test-key",
                "base_url": "https://example.com/v1",
                "model": "demo-model",
                "max_chars": 20,
            }
        )
        base.update(overrides)
        return base

    def test_returns_model_text_and_sends_prompt(self):
        captured: list = []
        opener = opener_for({"choices": [{"message": {"content": "晚上好呀"}}]}, captured)
        text = generate_message(
            self.settings(),
            "小明",
            "模板文案",
            when=datetime(2026, 11, 14, 20, 0),
            recent=["吃了吗", "在忙吗"],
            opener=opener,
        )
        self.assertEqual(text, "晚上好呀")
        request = captured[0]
        self.assertEqual(request.full_url, "https://example.com/v1/chat/completions")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["model"], "demo-model")
        self.assertTrue(body["messages"][1]["content"].find("小明") >= 0)
        self.assertIn("吃了吗", body["messages"][1]["content"])
        self.assertEqual(request.headers["Authorization"], "Bearer test-key")

    def test_missing_key_fails_before_network(self):
        with self.assertRaisesRegex(AIError, "API Key"):
            generate_message(self.settings(api_key=""), "小明", "模板", when=datetime.now())

    def test_unexpected_payload_raises(self):
        opener = opener_for({"error": "bad"})
        with self.assertRaisesRegex(AIError, "格式"):
            generate_message(self.settings(), "小明", "模板", when=datetime.now(), opener=opener)

    def test_provider_switch_updates_endpoint(self):
        merged = apply_provider({"api_key": "k"}, "deepseek")
        self.assertEqual(merged["base_url"], "https://api.deepseek.com/v1")
        self.assertEqual(merged["model"], "deepseek-chat")
        self.assertEqual(merged["api_key"], "k")

    def test_provider_switch_sets_image_model(self):
        merged = apply_provider({"api_key": "k"}, "zhipu")
        self.assertEqual(merged["image_model"], "cogview-3-flash")


class VariantTests(unittest.TestCase):
    def settings(self, **overrides):
        base = settings_with_defaults(
            {
                "api_key": "test-key",
                "base_url": "https://example.com/v1",
                "model": "demo-model",
                "max_chars": 20,
            }
        )
        base.update(overrides)
        return base

    def test_returns_three_distinct_lines(self):
        replies = iter(["早上好", "中午好", "晚上好"])

        def opener(request, timeout=None):
            return FakeResponse(json.dumps({"choices": [{"message": {"content": next(replies)}}]}).encode("utf-8"))

        texts = generate_variants(self.settings(), "小明", "模板", when=datetime(2026, 11, 15, 9, 0), count=3, opener=opener)
        self.assertEqual(texts, ["早上好", "中午好", "晚上好"])

    def test_chat_text_returns_raw_reply(self):
        opener = opener_for({"choices": [{"message": {"content": "本周 3 位都续上了。"}}]})
        text = chat_text(self.settings(), "系统提示", "用户输入", opener=opener)
        self.assertEqual(text, "本周 3 位都续上了。")


class ImageTests(unittest.TestCase):
    def settings(self, **overrides):
        base = settings_with_defaults(
            {
                "api_key": "test-key",
                "base_url": "https://example.com/v1",
                "image_model": "cogview-3-flash",
            }
        )
        base.update(overrides)
        return base

    def test_prompt_always_carries_style_rules(self):
        prompt = sticker_prompt("戴墨镜的柴犬")
        self.assertIn("戴墨镜的柴犬", prompt)
        self.assertIn("不要出现任何文字", prompt)

    def test_downloads_url_result(self):
        def opener(request, timeout=None):
            if request.full_url.endswith("/images/generations"):
                return FakeResponse(json.dumps({"data": [{"url": "https://cdn.example.com/a.png"}]}).encode("utf-8"))
            return FakeResponse(b"PNGDATA")

        data = generate_image(self.settings(), sticker_prompt("小猫"), opener=opener)
        self.assertEqual(data, b"PNGDATA")

    def test_accepts_b64_result(self):
        import base64

        opener = opener_for({"data": [{"b64_json": base64.b64encode(b"PNGDATA").decode()}]})
        self.assertEqual(generate_image(self.settings(), sticker_prompt("小猫"), opener=opener), b"PNGDATA")

    def test_missing_image_model_is_reported(self):
        with self.assertRaisesRegex(AIError, "图像模型"):
            generate_image(self.settings(model="x", image_model="", provider="openai_compatible"),
                           sticker_prompt("小猫"), opener=opener_for({}))

    def test_url_in_image_field_falls_back_to_preset(self):
        import base64

        captured: list = []
        opener = opener_for({"data": [{"b64_json": base64.b64encode(b"PNGDATA").decode()}]}, captured)
        settings = self.settings(
            provider="zhipu",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            image_model="https://open.bigmodel.cn/api/paas/v4",
        )
        generate_image(settings, sticker_prompt("柴犬"), opener=opener)
        body = json.loads(captured[0].data.decode("utf-8"))
        self.assertEqual(body["model"], "cogview-3-flash")

    def test_url_in_image_field_without_preset_is_actionable(self):
        settings = self.settings(image_model="https://example.com/v1", provider="openai_compatible")
        with self.assertRaisesRegex(AIError, "模型名"):
            generate_image(settings, sticker_prompt("柴犬"), opener=opener_for({}))


class ProbeChatTests(unittest.TestCase):
    def settings(self, **overrides):
        base = {
            "api_key": "k",
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "model": "glm-4-flash",
        }
        base.update(overrides)
        return settings_with_defaults(base)

    def test_reports_status_and_latency(self):
        result = probe_chat(self.settings(), opener=opener_for({"choices": [{"message": {"content": "pong"}}]}))
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], 200)
        self.assertGreaterEqual(result["elapsed_ms"], 0)
        self.assertIn("glm-4-flash", result["model"])

    def test_http_error_is_reported(self):
        def opener(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", None, BytesIO(b"bad key"))

        result = probe_chat(self.settings(), opener=opener)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], 401)
        self.assertIn("401", result["detail"])
        self.assertIn("Key", result["hint"])

    def test_connection_error_mentions_proxy_or_provider(self):
        def opener(request, timeout=None):
            raise urllib.error.URLError(TimeoutError("timed out"))

        result = probe_chat(self.settings(), opener=opener)
        self.assertFalse(result["ok"])
        self.assertIn("超时", result["detail"])
        self.assertIn("智谱", result["hint"])

    def test_missing_key_and_bad_url(self):
        self.assertFalse(probe_chat(self.settings(api_key=""))["ok"])
        bad = probe_chat(self.settings(base_url="api.openai.com"))
        self.assertFalse(bad["ok"])
        self.assertIn("地址", bad["detail"])


class SuggestQuoteTimeoutTests(unittest.TestCase):
    def test_quote_waits_long_enough_for_slow_relay(self):
        seen: list[float] = []

        def opener(request, timeout=None):
            seen.append(timeout)
            return FakeResponse(
                json.dumps({"choices": [{"message": {"content": "别等了，先干起来。\n佚名"}}]}).encode()
            )

        suggest_quote(
            {"api_key": "k", "base_url": "https://relay.test/v1", "model": "gpt-5.6-sol"},
            opener=opener,
        )
        self.assertGreaterEqual(seen[0], 45)


if __name__ == "__main__":
    unittest.main()