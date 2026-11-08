"""每日板块龙头脚本测试：自测与日历、历史统计与回测一致、离线 fixture 生成、错误分支不写结果。"""
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import run_sector_leaders as task  # noqa: E402

def bars(dates, closes):
    return pd.DataFrame({"date": dates, "open": closes, "high": closes, "low": closes, "close": closes, "volume": [1000.0] * len(closes), "amount": [c * 1000.0 for c in closes], "turnover": [5.0] * len(closes)})

def members_fixture(tmp):
    members = {"sectors": {
        "板块甲": {"bk": "BK0001", "total": 4, "members": [
            {"code": "000001", "name": "测试一"}, {"code": "000002", "name": "测试二"},
            {"code": "000003", "name": "ST测试三"}, {"code": "000004", "name": "测试四"}]},
        "板块乙": {"bk": "BK0002", "total": 1, "members": [{"code": "000001", "name": "测试一"}]},
    }}
    path = Path(tmp) / "members.json"
    path.write_text(json.dumps(members, ensure_ascii=False), encoding="utf-8")
    return path

def history_fixture(tmp):
    hist = Path(tmp) / "history"
    hist.mkdir()
    dates = pd.read_parquet(ROOT / "data/history/600000.parquet", columns=["date"])["date"].tail(30)
    universe = [
        {"code": "000001", "name": "测试一"}, {"code": "000002", "name": "测试二"},
        {"code": "000003", "name": "ST测试三"}, {"code": "000004", "name": "测试四"},
    ]
    (hist / "universe.json").write_text(json.dumps({"stocks": universe}, ensure_ascii=False), encoding="utf-8")
    up = [10.0 * (1.02 ** i) for i in range(30)]
    flat = [10.0] * 30
    for code, closes in (("000001", up), ("000002", up), ("000003", up), ("000004", flat)):
        bars(dates, closes).to_parquet(hist / f"{code}.parquet")
    return hist, dates

class SectorLeadersDailyTest(unittest.TestCase):
    def test_self_test_and_calendar(self):
        task.self_test()
        self.assertEqual(task.latest_trading_date(dt.date(2026, 11, 8)), "2026-11-06")

    def test_kept_filters_match_backtest(self):
        results = json.loads((ROOT / "stock-analysis/sector_leaders_results.json").read_text(encoding="utf-8"))
        self.assertEqual(list(task.KEPT_FILTERS), results["kept_filters"])

    def test_backtest_snapshot_numbers(self):
        snap = task.backtest_snapshot()
        self.assertEqual(snap["source"], "stock-analysis/sector_leaders_backtest.md")
        combo = snap["combo"]["full"]
        self.assertEqual(combo["n"], 166876)
        self.assertEqual(combo["wins"], 81393)
        self.assertAlmostEqual(combo["win_rate"], 81393 / 166876)
        self.assertEqual(len(snap["sectors"]), 23)
        self.assertEqual(len(snap["top3"]), 23)
        report = (ROOT / "stock-analysis/sector_leaders_backtest.md").read_text(encoding="utf-8")
        self.assertIn("48.77%", report)
        self.assertIn("53%~66%", report)

    def test_history_stats_matches_backtest(self):
        results = json.loads((ROOT / "stock-analysis/sector_leaders_results.json").read_text(encoding="utf-8"))
        entry = results["sectors"]["半导体"]["top3"][0]
        self.assertEqual(entry["code"], "688807")
        d = task.add_features(pd.read_parquet(ROOT / "data/history/688807.parquet"))
        stats = task.history_stats(d, "2026-11-06")
        for key in ("n", "wins"):
            self.assertEqual(stats[key], entry["full"][key])
        for key in ("win_rate", "mean_ret", "payoff"):
            self.assertAlmostEqual(stats[key], entry["full"][key], places=9)
        self.assertEqual(stats["last_signal"], entry["last_signal"])
        self.assertEqual(stats["signals_2026"], entry["signals_2026"])

    def test_generate_offline_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            hist, dates = history_fixture(tmp)
            members_path = members_fixture(tmp)
            out = Path(tmp) / "out.json"
            result = task.generate(as_of="2026-11-06", history_dir=hist, output=out,
                                   offline=True, members_path=members_path)
            self.assertEqual(result["as_of"], "2026-11-06")
            self.assertEqual(result["source"], "daily")
            self.assertEqual(result["counts"]["strong"], 2)
            self.assertEqual(result["counts"]["picks"], 3)
            self.assertEqual(result["counts"]["sectors"], 2)
            top_a = result["sectors"]["板块甲"]["top3"]
            self.assertEqual([p["code"] for p in top_a], ["000001", "000002"])
            self.assertEqual([p["code"] for p in result["sectors"]["板块乙"]["top3"]], ["000001"])
            pick = top_a[0]
            self.assertEqual(pick["signal"], "5日涨幅 10.41%")
            self.assertEqual(pick["n"], 5)
            self.assertEqual(pick["wins"], 5)
            self.assertEqual(pick["win_rate"], 1.0)
            self.assertAlmostEqual(pick["mean_ret"], 1.02 ** 5 - 1)
            self.assertEqual(pick["last_signal"], str(dates.iloc[24])[:10])
            self.assertEqual(pick["signals_2026"], 5)
            self.assertFalse(pick["excluded_live"])
            self.assertIn("48.77%", result["summary"])
            self.assertIn("仅研究", result["summary"])
            self.assertTrue(out.exists())

    def test_generate_rejects_future_and_holiday(self):
        with self.assertRaises(ValueError):
            task.generate(as_of="2026-11-09", offline=True)
        with self.assertRaises(ValueError):
            task.generate(as_of="2026-11-07", offline=True)

    def test_generate_missing_cache_keeps_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            hist = Path(tmp) / "history"
            hist.mkdir()
            (hist / "universe.json").write_text(
                json.dumps({"stocks": [{"code": "000001", "name": "测试一"}]}, ensure_ascii=False),
                encoding="utf-8")
            members_path = members_fixture(tmp)
            out = Path(tmp) / "out.json"
            out.write_text("{\"sentinel\": true}", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "结果未发布"):
                task.generate(as_of="2026-11-06", history_dir=hist, output=out,
                              offline=True, members_path=members_path)
            self.assertEqual(out.read_text(encoding="utf-8"), "{\"sentinel\": true}")


    def test_scan_uses_top_1500_only(self):
        """标的池与回测一致：只取 universe 前 1500 只，1501 只起即使缺缓存也不致命。"""
        with tempfile.TemporaryDirectory() as tmp:
            hist, dates = history_fixture(tmp)
            members_path = members_fixture(tmp)
            flat = hist / "000004.parquet"
            extra = []
            for i in range(1496):
                code = "9%05d" % (i + 1)
                (hist / (code + ".parquet")).symlink_to(flat)
                extra.append({"code": code, "name": "补位"})
            data = json.loads((hist / "universe.json").read_text(encoding="utf-8"))
            data["stocks"] = data["stocks"] + extra + [{"code": "999999", "name": "池外缺缓存"}]
            (hist / "universe.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            result = task.scan_sector_leaders("2026-11-06", hist, members_path)
            self.assertEqual(result["missing_codes"], [])
            self.assertEqual(result["counts"]["strong"], 2)


if __name__ == "__main__":
    unittest.main()
