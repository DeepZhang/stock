import datetime
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as site
import stock_data

ROOT = Path(__file__).resolve().parents[1]
FUND_DIR = ROOT / "data" / "funds"


def write_fund_file(date, times):
    FUND_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"times": times, "sectors": {"板块A": [1.0] * len(times), "板块B": [2.0] * len(times)}}
    (FUND_DIR / (date + "_fund.json")).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class FundflowFallbackTest(unittest.TestCase):
    def setUp(self):
        self._backups = []
        if FUND_DIR.exists():
            for path in FUND_DIR.glob("*_fund.json"):
                backup = path.with_suffix(".bak")
                path.rename(backup)
                self._backups.append(backup)

    def tearDown(self):
        for path in FUND_DIR.glob("*_fund.json"):
            path.unlink()
        for backup in self._backups:
            backup.rename(backup.with_suffix(".json"))

    def test_dates_only_lists_usable(self):
        write_fund_file("2026-11-05", ["09:30", "09:31", "09:32"])
        write_fund_file("2026-11-06", ["09:30"])
        write_fund_file("2026-11-07", [])
        with site.app.test_client() as client:
            resp = client.get("/api/dates")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {"dates": ["2026-11-05"]})

    def test_explicit_date_falls_back_to_previous_usable(self):
        write_fund_file("2026-11-05", ["09:30", "09:31"])
        with site.app.test_client() as client:
            resp = client.get("/api/data?date=2026-11-06")
        body = resp.get_json()
        self.assertEqual(body["date"], "2026-11-05")
        self.assertEqual(body["data"]["times"], ["09:30", "09:31"])

    def test_explicit_date_without_history_returns_empty_with_requested_date(self):
        with site.app.test_client() as client:
            resp = client.get("/api/data?date=2026-11-04")
        body = resp.get_json()
        self.assertEqual(body["date"], "2026-11-04")
        self.assertEqual(body["data"]["times"], [])
        self.assertEqual(body["data"]["sectors"], {})

    def test_single_point_file_is_not_usable(self):
        write_fund_file("2026-11-06", ["09:30"])
        path = FUND_DIR / "2026-11-06_fund.json"
        self.assertIsNone(site._load_fund_file(str(path)))
        self.assertEqual(site._usable_fund_dates(), [])
        self.assertIsNone(site._latest_usable_fund(before="2026-11-06"))

    def test_no_date_on_non_trading_day_returns_latest_usable(self):
        if datetime.date.today().weekday() < 5:
            self.skipTest("仅周末断言无日期回退")
        write_fund_file("2026-11-06", ["09:30", "09:31", "09:32"])
        data, date = site._read_latest_data()
        self.assertEqual(date, "2026-11-06")
        self.assertEqual(len(data["times"]), 3)

    def test_no_date_without_any_file_does_not_fetch_on_non_trading_day(self):
        if datetime.date.today().weekday() < 5:
            self.skipTest("仅周末断言")
        with mock.patch.object(stock_data, "fetch_all_sectors_fund", side_effect=AssertionError("周末不应拉取")):
            data, date = site._read_latest_data()
        self.assertEqual(data, {"times": [], "sectors": {}})
        self.assertEqual(date, datetime.date.today().strftime("%Y-%m-%d"))


if __name__ == "__main__":
    unittest.main()
