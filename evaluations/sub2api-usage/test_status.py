import importlib.util
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "skills" / "sub2api-usage" / "scripts" / "status.py"
SPEC = importlib.util.spec_from_file_location("sub2api_usage_status", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class Sub2ApiUsageEvaluationTest(unittest.TestCase):
    def test_admin_requests_run_without_worker_threads(self):
        requested_urls = []

        def fake_fetch(url, _timeout, _headers=None):
            self.assertIs(threading.current_thread(), threading.main_thread())
            requested_urls.append(url)
            if "/admin/users?" in url:
                return {"data": {"items": []}}
            if "/dashboard/trend?" in url:
                return {"data": {"trend": []}}
            return {"data": {"ranking": [], "total_tokens": 0, "total_requests": 0}}

        with patch.object(MODULE, "_fetch_json", side_effect=fake_fetch):
            MODULE.fetch_admin_report(
                "https://sub2api.example/api/v1",
                "test-key",
                8,
                now=datetime(2026, 8, 24, 20, 0, tzinfo=MODULE.SHANGHAI),
            )

        self.assertEqual(5, len(requested_urls))

    def test_admin_requests_support_isolated_utc_calendar(self):
        requested_urls = []

        def fake_fetch(url, _timeout, _headers=None):
            requested_urls.append(url)
            if "/admin/users?" in url:
                return {"data": {"items": []}}
            if "/dashboard/trend?" in url:
                return {"data": {"trend": []}}
            return {"data": {"ranking": [], "total_tokens": 0, "total_requests": 0}}

        with patch.object(MODULE, "_fetch_json", side_effect=fake_fetch):
            report = MODULE.fetch_admin_report(
                "https://sub2api.example/api/v1",
                "test-key",
                8,
                now=datetime(2026, 8, 24, 18, 0, tzinfo=ZoneInfo("UTC")),
                report_timezone=ZoneInfo("UTC"),
            )

        self.assertTrue(all("timezone=UTC" in url for url in requested_urls[1:]))
        self.assertEqual("UTC", report["report_timezone"])

    def test_report_uses_current_usernames_and_peak_hour(self):
        users = {"data": {"items": [
            {"id": 1, "username": "风"},
            {"id": 2, "username": "Allen"},
            {"id": 3, "username": "暂未使用"},
        ]}}
        today = {"data": {
            "total_tokens": 100000,
            "total_requests": 12,
            "ranking": [
                {"user_id": 1, "username": "旧用户名", "tokens": 70000, "requests": 8},
                {"user_id": 2, "username": "Allen", "tokens": 30000, "requests": 4},
            ],
        }}
        week = {"data": {"total_tokens": 200000, "ranking": [
            {"user_id": 1, "tokens": 120000},
            {"user_id": 2, "tokens": 80000},
        ]}}
        month = {"data": {"total_tokens": 500000, "ranking": [
            {"user_id": 1, "tokens": 280000},
            {"user_id": 2, "tokens": 220000},
        ]}}
        trend = {"data": {"trend": [
            {"date": "2026-08-24 20:00", "total_tokens": 40000, "requests": 5},
        ]}}
        report = MODULE.build_admin_report(users, today, week, month, trend)
        payload = {
            "ts": "2026-08-24T20:30:00+08:00",
            "today": {"ok": True, "tokens": 100000},
            "week": {"ok": True, "tokens": 200000},
            "month": {"ok": True, "tokens": 500000},
            "usage": {"ok": True, "requests": 12},
            "forecast": {},
            "week_pool": {"ok": True, "used_pct": 10, "remain_pct": 90},
        }
        text = MODULE.render_status(payload, report)
        self.assertIn("1. 风", text)
        self.assertIn("3. 暂未使用", text)
        self.assertIn("今日用量：70,000 Token", text)
        self.assertIn("占今日总量：70.0%", text)
        self.assertIn("今日用量高峰时段：20:00–20:59", text)
        self.assertNotIn("旧用户名", text)
        self.assertNotIn("今日用量已达到本月累计量", text)


if __name__ == "__main__":
    unittest.main()
