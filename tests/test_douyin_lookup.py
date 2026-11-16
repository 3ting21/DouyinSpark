from __future__ import annotations

import time
import unittest

from douyin_spark.auto import AutomationService, Job
from douyin_spark.models import Friend
from douyin_spark.urls import DOUYIN_HOME_URL, douyin_search_url, normalize_douyin_id

from douyin_spark.browser import ChatUnavailable


class NormalizeTests(unittest.TestCase):
    def test_accepts_bare_at_and_search_link(self):
        self.assertEqual(normalize_douyin_id("abyc123"), "abyc123")
        self.assertEqual(normalize_douyin_id("@abyc123"), "abyc123")
        self.assertEqual(normalize_douyin_id("https://www.douyin.com/search/abyc123"), "abyc123")

    def test_rejects_profile_link_and_blank(self):
        self.assertEqual(normalize_douyin_id("https://www.douyin.com/user/MS4wLjABAAAA"), "")
        self.assertEqual(normalize_douyin_id("https://example.com/abyc123"), "")
        self.assertEqual(normalize_douyin_id("   "), "")

    def test_search_url_targets_user_results(self):
        self.assertEqual(douyin_search_url("abyc123"), "https://www.douyin.com/search/abyc123?type=user")
        self.assertEqual(douyin_search_url(""), DOUYIN_HOME_URL)


class FriendRoundTripTests(unittest.TestCase):
    def test_douyin_id_survives_serialization(self):
        friend = Friend(name="示例三分钟", douyin_id="abyc123")
        again = Friend.from_dict(friend.to_dict())
        self.assertEqual(again.douyin_id, "abyc123")
        self.assertEqual(Friend.from_dict({"name": "老数据"}).douyin_id, "")


class FakeSession:
    def __init__(self, candidates):
        self.candidates = candidates
        self.keywords = []

    def lookup_user(self, keyword):
        self.keywords.append(keyword)
        if keyword == "boom":
            raise ChatUnavailable("打开抖音搜索页失败：mock")
        return self.candidates

    def stop(self):
        return None


class LookupJobTests(unittest.TestCase):
    def run_job(self, keyword, candidates):
        session = FakeSession(candidates)
        service = AutomationService(session_factory=lambda: session)
        service.start()
        service.submit(Job(kind="lookup", keyword=keyword))
        deadline = time.time() + 5
        results = []
        while time.time() < deadline and not results:
            results = service.poll()
            if not results:
                time.sleep(0.05)
        service.stop()
        return results[0], session

    def test_returns_candidates_from_session(self):
        result, session = self.run_job("abyc123", [{"name": "示例三分钟", "url": ""}])
        self.assertEqual(result.kind, "lookup")
        self.assertEqual(result.status, "lookup_ok")
        self.assertEqual(result.report["candidates"][0]["name"], "示例三分钟")
        self.assertEqual(session.keywords, ["abyc123"])

    def test_empty_result_still_succeeds_with_warning(self):
        result, _session = self.run_job("abyc123", [])
        self.assertEqual(result.status, "lookup_warn")
        self.assertIn("浏览器", result.detail)

    def test_open_failure_is_reported(self):
        result, _session = self.run_job("boom", [])
        self.assertEqual(result.status, "lookup_error")
        self.assertIn("mock", result.detail)


if __name__ == "__main__":
    unittest.main()
