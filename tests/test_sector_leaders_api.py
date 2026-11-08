"""板块龙头只读缓存 API 测试：缺失每日产物时回退回测 Top 3，不联网、不扫描。"""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import app as site

ROOT = Path(__file__).resolve().parent.parent
BACKTEST = ROOT / "stock-analysis" / "sector_leaders_results.json"
FIXTURE = {
    "as_of": "2026-11-06",
    "generated_at": "2026-11-08T11:30:00+00:00",
    "source": "daily",
    "sectors": {
        "玻璃基板": {
            "combo": {},
            "candidates": 1,
            "top3": [
                {
                    "code": "600584",
                    "name": "长电科技",
                    "signal": "5日涨幅 12.34%",
                    "ret_5d": 0.1234,
                    "vol_ratio": 1.5,
                    "turnover": 0.05,
                    "close": 30.5,
                    "win_rate": 0.5797,
                    "n": 138,
                    "wins": 80,
                    "mean_ret": 0.0287,
                    "payoff": 1.48,
                    "last_signal": "2026-09-22",
                    "signals_2026": 55,
                    "excluded_live": False,
                }
            ],
        }
    },
    "counts": {"sectors": 1, "picks": 1, "strong": 1},
    "summary": "摘要",
    "research_only": True,
    "backtest": {"combo": {}, "source": "stock-analysis/sector_leaders_backtest.md"},
}


class SectorLeadersApiTest(unittest.TestCase):
    def setUp(self):
        self.client = site.app.test_client()

    def test_missing_daily_falls_back_to_backtest_without_network(self):
        missing = str(ROOT / "data" / "sector_leaders_picks.json.missing")
        with mock.patch.object(site, "SECTOR_LEADERS_RESULT", missing):
            with mock.patch("requests.get", side_effect=AssertionError("network forbidden")):
                with mock.patch("requests.post", side_effect=AssertionError("network forbidden")):
                    start = time.monotonic()
                    resp = self.client.get("/api/sector-leaders")
                    elapsed = time.monotonic() - start
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("Cache-Control"), "no-store")
        self.assertLess(elapsed, 5)
        data = resp.get_json()
        self.assertEqual(data["source"], "backtest")
        self.assertEqual(data["counts"], {"sectors": 23, "picks": 69})
        self.assertTrue(data["research_only"])
        raw = json.loads(BACKTEST.read_text(encoding="utf-8"))
        for name, sec in raw["sectors"].items():
            picks = data["sectors"][name]["top3"]
            self.assertLessEqual(len(picks), 3)
            for pick, top in zip(picks, sec["top3"]):
                self.assertEqual(pick["code"], top["code"])
                self.assertEqual(pick["win_rate"], top["full"]["win_rate"])
                self.assertEqual(pick["n"], top["full"]["n"])
                self.assertEqual(pick["last_signal"], top["last_signal"])
        first = data["sectors"]["半导体"]["top3"][0]
        self.assertEqual(first["code"], "688807")
        self.assertEqual(first["win_rate"], 0.64)
        self.assertEqual(first["n"], 50)

    def test_present_daily_result_served_as_is(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sector_leaders_picks.json"
            path.write_text(json.dumps(FIXTURE, ensure_ascii=False), encoding="utf-8")
            with mock.patch.object(site, "SECTOR_LEADERS_RESULT", str(path)):
                resp = self.client.get("/api/sector-leaders")
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertEqual(data["source"], "daily")
            self.assertEqual(data["as_of"], "2026-11-06")
            self.assertEqual(data["sectors"], FIXTURE["sectors"])
            self.assertEqual(data["summary"], "摘要")
            path.write_text("{}", encoding="utf-8")
            with mock.patch.object(site, "SECTOR_LEADERS_RESULT", str(path)):
                resp = self.client.get("/api/sector-leaders")
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertEqual(data["source"], "none")
            self.assertEqual(data["sectors"], {})

    def test_screen_and_prediction_contracts_unchanged(self):
        self.assertEqual(self.client.get("/api/prediction").status_code, 404)
        self.assertEqual(self.client.get("/api/screen").status_code, 200)


if __name__ == "__main__":
    unittest.main()
