#!/usr/bin/env python3
"""每日板块龙头 Top 3 扫描：只读当日日线缓存，按保留过滤器取当日强势票，写入 data/sector_leaders_picks.json；摘要只生成不发送。信号口径与回测报告一致：5 日涨幅>=5%、量比<=3、换手率 1%~20%，ST/退市按当日名称剔除。"""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[0:0] = [str(ROOT), str(ROOT / "modules"), str(ROOT / "stock-analysis")]

from backtest_sector_leaders import (  # noqa: E402
    BASE_RET5, FILTERS, START, VAL_START, add_features, summarise,
)
from modules.push_summary import generate_sector_leaders_summary  # noqa: E402
from pull_history import atomic_json  # noqa: E402
from scripts.run_shortterm import latest_trading_date, refresh_history  # noqa: E402

KEPT_FILTERS = ("vol_ratio_le_3", "turnover_1_20")
RESULTS_FILE = ROOT / "stock-analysis/sector_leaders_results.json"
MEMBERS_FILE = ROOT / "stock-analysis/sector_members.json"
OUTPUT = ROOT / "data/sector_leaders_picks.json"

def is_strong(row, name):
    """当日强势：5 日涨幅>=5% 且保留过滤器（量比<=3、换手率 1%~20%）都通过；ST/退市按当日名称剔除。"""
    if "ST" in (name or "") or "退" in (name or ""):
        return False
    frame = pd.DataFrame([row])
    try:
        if not bool(frame["ret5"].iloc[0] >= BASE_RET5):
            return False
        return all(bool(FILTERS[k][1](frame).fillna(False).iloc[0]) for k in KEPT_FILTERS)
    except (KeyError, TypeError, ValueError):
        return False

def history_stats(d, as_of):
    """单只票组合信号历史统计：窗口 [2024-01-01, as_of]，与回测 combo_mask 同口径，不依赖冻结 END。"""
    end = pd.Timestamp(as_of)
    mask = (d["pos"] >= 20) & (d["has_fwd5"]) & (d["ret5"] >= BASE_RET5)
    mask &= (d["date"] >= START) & (d["date"] <= end)
    mask &= (d[["open", "high", "low", "close", "volume"]] > 0).all(axis=1)
    mask = mask.fillna(False)
    for key in KEPT_FILTERS:
        mask &= FILTERS[key][1](d).fillna(False)
    stats = summarise(d.loc[mask, "ret_fwd5"])
    dates = d.loc[mask, "date"]
    stats["last_signal"] = dates.max().strftime("%Y-%m-%d") if len(dates) else None
    stats["signals_2026"] = int((dates >= VAL_START).sum())
    return stats

def load_members(members_path=None):
    data = json.loads(Path(members_path or MEMBERS_FILE).read_text(encoding="utf-8"))
    return data["sectors"]


def scan_sector_leaders(as_of, history_dir, members_path=None):
    """扫描当日强势票并按板块取 Top 3；缺失缓存进 missing_codes，不写结果。"""
    cache = Path(history_dir)
    # 标的池与回测一致：成交额前 1500 只（universe.json 按成交额排序，取前 1500；其余票不在池内）。
    universe = json.loads((cache / "universe.json").read_text(encoding="utf-8"))["stocks"][:1500]
    members = load_members(members_path)
    code_sectors = {}
    for sec, info in members.items():
        for member in info.get("members", []):
            code_sectors.setdefault(member["code"], []).append(sec)
    strong = []
    missing_codes, unavailable_codes = [], []
    for stock in universe:
        code = stock["code"]
        name = stock.get("name", "")
        path = cache / f"{code}.parquet"
        if not path.exists():
            missing_codes.append(code)
            continue
        d = add_features(pd.read_parquet(path))
        if len(d) < 21:
            unavailable_codes.append(code)
            continue
        last = d.iloc[-1]
        if str(last["date"])[:10] != as_of:
            unavailable_codes.append(code)
            continue
        if not is_strong(last, name):
            continue
        stats = history_stats(d, as_of)
        strong.append({
            "code": code, "name": name,
            "signal": f"5日涨幅 {float(last["ret5"]):.2%}",
            "ret_5d": float(last["ret5"]), "vol_ratio": float(last["vol_ratio"]),
            "turnover": float(last["turnover"]), "close": float(last["close"]),
            "win_rate": stats["win_rate"], "n": stats["n"], "wins": stats["wins"],
            "mean_ret": stats["mean_ret"], "payoff": stats["payoff"],
            "last_signal": stats["last_signal"], "signals_2026": stats["signals_2026"],
            "excluded_live": code.startswith(("30", "688")),
            "sectors": sorted(code_sectors.get(code, [])),
        })
    sectors = {}
    for sec in members:
        picks = [p for p in strong if sec in p["sectors"]]
        picks.sort(key=lambda p: (-p["ret_5d"], p["code"]))
        top3 = [{k: v for k, v in p.items() if k != "sectors"} for p in picks[:3]]
        sectors[sec] = {"combo": None, "top3": top3, "candidates": len(picks)}
    return {
        "as_of": as_of, "source": "daily", "sectors": sectors,
        "counts": {"sectors": len(members), "picks": sum(len(s["top3"]) for s in sectors.values()), "strong": len(strong)},
        "missing_codes": missing_codes, "unavailable_codes": unavailable_codes,
    }

def backtest_snapshot():
    """仅冻结报告已有的统计，随每日结果冻结，HTTP 请求不读取回测源文件。"""
    results = json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
    return {
        "combo": results["combo"],
        "sectors": {sec: info["combo"] for sec, info in results["sectors"].items()},
        "top3": {sec: info["top3"] for sec, info in results["sectors"].items()},
        "source": "stock-analysis/sector_leaders_backtest.md",
    }

def generate(as_of=None, history_dir=None, output=None, offline=False, members_path=None):
    as_of = as_of or latest_trading_date()
    day = dt.date.fromisoformat(as_of)
    if as_of > dt.date.today().isoformat():
        raise ValueError("不允许未来日期")
    if latest_trading_date(day) != as_of:
        raise ValueError("指定日期休市，请使用最近交易日")
    cache = Path(history_dir or ROOT / "data/history")
    refresh = {"updated": 0, "network": False, "mode": "offline"} if offline else refresh_history(as_of, cache)
    result = scan_sector_leaders(as_of, cache, members_path)
    if result["missing_codes"]:
        raise RuntimeError(f"缺少{len(result["missing_codes"])}只缓存，结果未发布")
    snapshot = backtest_snapshot()
    for sec, info in result["sectors"].items():
        info["combo"] = snapshot["sectors"].get(sec)
    result.update(generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                  research_only=True, refresh=refresh, backtest=snapshot)
    result["summary"] = generate_sector_leaders_summary(result)
    atomic_json(output or OUTPUT, result)
    print(result["summary"])
    return result

def self_test():
    """运行当日强势规则的边界用例：不联网、不读历史。"""
    row = {"ret5": 0.06, "vol_ratio": 2.0, "turnover": 5.0}
    assert is_strong(row, "普通股")
    assert is_strong({"ret5": 0.05, "vol_ratio": 3.0, "turnover": 1.0}, "普通股")
    assert is_strong({"ret5": 0.05, "vol_ratio": 3.0, "turnover": 20.0}, "普通股")
    for name, changes in (("ST普通股", {}), ("退市股", {}),
                          ("普通股", {"vol_ratio": 3.5}), ("普通股", {"turnover": 25.0}),
                          ("普通股", {"turnover": 0.5}), ("普通股", {"ret5": 0.04}),
                          ("普通股", {"ret5": None})):
        assert not is_strong({**row, **changes}, name)
    assert latest_trading_date(dt.date(2026, 11, 8)) == "2026-11-06"
    print("自测通过：ST/退市、量比>3、换手率越界、5日涨幅不足、周末日期")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="信号交易日 YYYY-MM-DD；默认最近交易日")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    else:
        generate(args.date or latest_trading_date(), offline=args.offline)
