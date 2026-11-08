import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
 
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "modules"))
 
import app as site
from unified import build_unified, shortterm_rates
 
 
LEADERS = {
    "as_of": "2026-11-06",
    "source": "daily",
    "backtest": {"combo": {"full": {"n": 1000, "wins": 480, "win_rate": 0.48}}},
    "sectors": {
        "半导体": {
            "combo": {"full": {"n": 100, "wins": 52, "win_rate": 0.52}},
            "top3": [
                {"code": "600001", "name": "甲", "win_rate": 0.55, "n": 100, "wins": 55, "last_signal": "2026-11-05"},
                {"code": "600002", "name": "乙", "win_rate": 0.60, "n": 50, "wins": 30, "last_signal": "2026-11-04"},
                {"code": "600003", "name": "丙", "win_rate": 0.50, "n": 200, "wins": 100, "last_signal": "2026-11-03"},
            ],
        },
        "电力": {
            "combo": {"full": {"n": 100, "wins": 43, "win_rate": 0.43}},
            "top3": [
                {"code": "600010", "name": "电甲", "win_rate": 0.70, "n": 40, "wins": 28, "last_signal": "2026-11-05"},
            ],
        },
    },
}
 
MEMBERS = {
    "sectors": {
        "半导体": {"members": [{"code": "600001"}, {"code": "600002"}, {"code": "600004"}, {"code": "600005"}]},
        "电力": {"members": [{"code": "600010"}]},
    },
}
 
SHORTTERM = {
    "as_of": "2026-11-06",
    "limit_up": [
        {"code": "600001", "name": "甲", "signal_type": "limit_up", "date": "2026-11-06"},
        {"code": "600004", "name": "丁", "signal_type": "limit_up", "date": "2026-11-06"},
    ],
    "oversold": [
        {"code": "600002", "name": "乙", "signal_type": "oversold", "date": "2026-11-06"},
        {"code": "600005", "name": "戊", "signal_type": "oversold", "date": "2026-11-06"},
    ],
}
 
SCREEN = {
    "list": [
        {"code": "600001", "name": "甲", "sector": "半导体", "date": "2026-11-06"},
        {"code": "600006", "name": "己", "sector": "半导体", "date": "2026-11-06"},
        {"code": "600010", "name": "电甲", "sector": "电力", "date": "2026-11-06"},
    ],
}
 
 
def build_fixture():
    return build_unified(shortterm=copy.deepcopy(SHORTTERM),
                         leaders=copy.deepcopy(LEADERS),
                         screen=copy.deepcopy(SCREEN),
                         members=copy.deepcopy(MEMBERS))
 
 
class UnifiedBuildTest(unittest.TestCase):
    def setUp(self):
        self.result = build_fixture()
    def _sector(self, name):
        for sec in self.result["sectors"]:
            if sec["sector"] == name:
                return sec
        self.fail("missing sector: " + name)
 
    def test_merge_dedup_signals(self):
        row = [p for p in self._sector("半导体")["picks"] if p["code"] == "600001"][0]
        self.assertEqual([s["signal"] for s in row["signals"]],
                         ["板块龙头", "涨停次日", "旧突破选股器"])
        self.assertAlmostEqual(row["best_win_rate"], 0.6303)
        self.assertEqual(row["best_n"], 10762)
        self.assertEqual(row["best_signal"], "涨停次日")
        leader = row["signals"][0]
        self.assertEqual((leader["win_rate"], leader["n"], leader["holding"]), (0.55, 100, "5天"))
 
    def test_sort_best_rate_then_n(self):
        codes = [p["code"] for p in self._sector("半导体")["picks"]]
        self.assertEqual(codes, ["600002", "600005", "600001"])
        first = self._sector("半导体")["picks"][0]
        self.assertAlmostEqual(first["best_win_rate"], 0.6903)
        self.assertEqual(first["best_signal"], "超跌反弹")
 
    def test_top3_limit_and_candidate_count(self):
        semi = self._sector("半导体")
        self.assertEqual(semi["candidate_count"], 6)
        self.assertEqual(len(semi["picks"]), 3)
        self.assertEqual(self.result["counts"], {"sectors": 23, "recommended_sectors": 1,
                                                   "skipped_sectors": 22, "candidates": 7, "picks": 3})
 
    def test_threshold_skips_below_50(self):
        skipped = {x["sector"]: x for x in self.result["skipped"]}
        self.assertIn("电力", skipped)
        self.assertAlmostEqual(skipped["电力"]["combo_win_rate"], 0.43)
        self.assertEqual(skipped["电力"]["reason"], "组合胜率低于 50%")
        self.assertNotIn("600010", [p["code"] for s in self.result["sectors"] for p in s["picks"]])
 
    def test_threshold_boundary_exact_50(self):
        leaders = copy.deepcopy(LEADERS)
        leaders["sectors"]["半导体"]["combo"]["full"]["win_rate"] = 0.5
        got = build_unified(leaders=leaders)
        self.assertEqual([s["sector"] for s in got["sectors"]], ["半导体"])
        leaders["sectors"]["半导体"]["combo"]["full"]["win_rate"] = 0.4999
        got = build_unified(leaders=leaders)
        self.assertEqual(got["sectors"], [])
 
    def test_screen_only_row_has_no_win_rate(self):
        got = build_unified(shortterm=copy.deepcopy(SHORTTERM),
                          leaders=copy.deepcopy(LEADERS),
                          screen=copy.deepcopy(SCREEN),
                          members=copy.deepcopy(MEMBERS), top_n=10)
        row = [p for s in got["sectors"] for p in s["picks"] if p["code"] == "600006"][0]
        self.assertIsNone(row["best_win_rate"])
        self.assertIsNone(row["best_signal"])
        self.assertEqual([s["signal"] for s in row["signals"]], ["旧突破选股器"])
        self.assertIsNone(row["signals"][0]["win_rate"])
 
    def test_missing_sources_contribute_empty(self):
        got = build_unified()
        self.assertEqual(got["sectors"], [])
        self.assertEqual(len(got["skipped"]), 23)
        self.assertEqual(got["counts"]["picks"], 0)
        self.assertEqual(got["as_of"], "")
        self.assertAlmostEqual(got["combo_win_rate"], 0.4877)
 
    def test_leaders_only_still_works(self):
        got = build_unified(leaders=copy.deepcopy(LEADERS))
        self.assertEqual([s["sector"] for s in got["sectors"]], ["半导体"])
        codes = [p["code"] for p in got["sectors"][0]["picks"]]
        self.assertEqual(codes, ["600002", "600001", "600003"])
        self.assertAlmostEqual(got["combo_win_rate"], 0.48)
 
    def test_shortterm_rates_prefers_full_period(self):
        rows = [{"signal": "涨停次日", "period": "2024-01-01~2026-11-06", "n": 10762,
                 "wins": 6783, "win_rate": 63.0273},
                {"signal": "涨停次日", "period": "2026年计入", "n": 4808,
                 "wins": 2982, "win_rate": 62.0216},
                {"signal": "超跌双信号", "period": "2024-01-01~2026-11-06", "n": 3213,
                 "wins": 2218, "win_rate": 69.0321}]
        rates = shortterm_rates({"backtest": {"rows": rows}})
        self.assertAlmostEqual(rates["limit_up"][0], 0.630273, places=5)
        self.assertEqual(rates["limit_up"][1], 10762)
        self.assertAlmostEqual(rates["oversold"][0], 0.690321, places=5)
        self.assertEqual(rates["oversold"][1], 3213)
 
 
class UnifiedApiTest(unittest.TestCase):
    def setUp(self):
        self.client = site.app.test_client()
 
    def test_api_unified_with_fixtures(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = {}
            for name, data in (("shortterm.json", SHORTTERM), ("leaders.json", LEADERS),
                               ("screen.json", SCREEN), ("members.json", MEMBERS)):
                path = Path(tmp) / name
                path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                paths[name] = str(path)
            with mock.patch.object(site, "SHORTTERM_RESULT", paths["shortterm.json"]), \
                 mock.patch.object(site, "SECTOR_LEADERS_RESULT", paths["leaders.json"]), \
                 mock.patch.object(site, "SCREEN_CACHE", paths["screen.json"]), \
                 mock.patch.object(site, "SECTOR_MEMBERS_FILE", paths["members.json"]):
                resp = self.client.get("/api/unified")
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp.headers.get("Cache-Control"), "no-store")
                data = resp.get_json()
                self.assertEqual(data["as_of"], "2026-11-06")
                self.assertAlmostEqual(data["combo_win_rate"], 0.48)
                self.assertEqual(data["counts"]["recommended_sectors"], 1)
                self.assertEqual(data["counts"]["skipped_sectors"], 22)
                self.assertEqual(data["sectors"][0]["sector"], "半导体")
                codes = [p["code"] for p in data["sectors"][0]["picks"]]
                self.assertEqual(codes, ["600002", "600005", "600001"])
 
    def test_api_unified_missing_files_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / "missing.json")
            with mock.patch.object(site, "SHORTTERM_RESULT", missing), \
                 mock.patch.object(site, "SECTOR_LEADERS_RESULT", missing), \
                 mock.patch.object(site, "SCREEN_CACHE", missing), \
                 mock.patch.object(site, "SECTOR_MEMBERS_FILE", missing), \
                 mock.patch.object(site, "SECTOR_LEADERS_BACKTEST", missing):
                resp = self.client.get("/api/unified")
                self.assertEqual(resp.status_code, 200)
                data = resp.get_json()
                self.assertEqual(data["sectors"], [])
                self.assertEqual(data["counts"]["skipped_sectors"], 23)
                self.assertEqual(data["counts"]["picks"], 0)
 
    def test_unified_page_renders(self):
        resp = self.client.get("/unified")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("统一榜单", body)
        self.assertIn("未推荐板块", body)
 
 
if __name__ == "__main__":
    unittest.main()
