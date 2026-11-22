# -*- coding: utf-8 -*-
"""板块相对强弱：多持有期 + 分时段稳定性检验

verify_sector_regime.py 只测了 20 日持有期，得出"浅回撤区 dist_high60 有效
(t=3.11)"。但复盘是每天做的，用户关心的是"这个板块下周能不能买"，
不是"一个月后"。本脚本回答三个问题：

  1. 5 / 10 / 20 日持有期下，信号是否都成立？（若只有 20 日成立，实用性差）
  2. 分时段（逐年 + 前半/后半样本）是否稳定？还是靠某一段行情撑起来的？
  3. 扣除交易成本后还剩多少？

复用 verify_sector_regime.py 的指数构造与 NW t 口径，不另起一套标准。

用法：
    python3 verify_sector_horizon.py
输出：
    results/sector_horizon_test.json
"""
import json
import os
import sys

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
HIST_DIR = os.path.join(BASE, "data", "history")
MEMBERS = os.path.join(BASE, "data", "sector_members.json")
OUT_DIR = os.path.join(BASE, "results")
sys.path.insert(0, os.path.join(BASE, "modules"))
sys.path.insert(0, BASE)
from price_clean import RET_LIMIT, load_clean, sector_returns  # noqa: E402

HORIZONS = (5, 10, 20)
NW_LAG = 20
MIN_OBS = 30
LIQ_FLOOR = 5e7
QUANTILE = 0.25

# 单边成本（佣金+印花+滑点）约 0.15%，一个来回约 0.3%
COST_ROUND_TRIP = 0.003

# 只检验在 regime 脚本里表现最强的两个指标 + 基准动量
FEATURES = ("dist_high60", "dist_high250", "ret20")


def newey_west_t(x, lag=NW_LAG):
    x = np.asarray(pd.Series(x).dropna(), dtype=float)
    n = len(x)
    if n < MIN_OBS:
        return np.nan, n
    mu = x.mean()
    e = x - mu
    s = (e @ e) / n
    for l in range(1, min(lag, n - 1) + 1):
        s += 2 * (1 - l / (lag + 1)) * ((e[l:] @ e[:-l]) / n)
    if s <= 0:
        return np.nan, n
    return mu / np.sqrt(s / n), n


def episodes(flag):
    f = np.asarray(flag, dtype=bool)
    if not f.any():
        return []
    d = np.diff(f.astype(int))
    starts = list(np.where(d == 1)[0] + 1)
    ends = list(np.where(d == -1)[0] + 1)
    if f[0]:
        starts = [0] + starts
    if f[-1]:
        ends = ends + [len(f)]
    return list(zip(starts, ends, [e - s for s, e in zip(starts, ends)]))


def load_members():
    with open(MEMBERS, encoding="utf-8") as f:
        raw = json.load(f)["sectors"]
    out = {}
    for name, v in raw.items():
        codes = [m["code"] for m in v.get("members", []) if m.get("code")]
        if codes:
            out[name] = {"bk": v.get("bk"), "codes": codes}
    return out


def load_history(codes):
    close, amount, missing = {}, {}, 0
    for code in codes:
        d, _ = load_clean(os.path.join(HIST_DIR, f"{code}.parquet"))
        if d is None:
            missing += 1
            continue
        idx = pd.DatetimeIndex(d["date"])
        close[code] = pd.Series(d["close"].values, index=idx)
        if "amount" in d.columns:
            amount[code] = pd.Series(d["amount"].values, index=idx)
    return (pd.DataFrame(close).sort_index(),
            pd.DataFrame(amount).sort_index(), missing)


def build_indices(members, close, amount):
    lvl = {}
    for name, info in members.items():
        cols = [c for c in info["codes"] if c in close.columns]
        if len(cols) < 5:
            continue
        px = close[cols]
        amt = amount.reindex(columns=cols)
        ew, _ = sector_returns(px, amt, winsor=RET_LIMIT,
                               liquidity_floor=LIQ_FLOOR, min_stocks=5)
        lvl[name] = (1 + ew.fillna(0)).cumprod()
    return pd.DataFrame(lvl)


def build_features(lvl):
    return {
        "dist_high250": lvl / lvl.rolling(250, min_periods=120).max() - 1,
        "dist_high60": lvl / lvl.rolling(60, min_periods=40).max() - 1,
        "ret20": lvl / lvl.shift(20) - 1,
    }


def market_regime(lvl):
    """市场状态：等权净值距 250 日高点回撤。"""
    ret = lvl.pct_change(fill_method=None)
    ew = ret.mean(axis=1, skipna=True)
    mkt = (1 + ew.fillna(0)).cumprod()
    dd = mkt / mkt.rolling(250, min_periods=120).max() - 1
    return {
        "深跌(dd<-20%)": dd < -0.20,
        "中度(-20~-10%)": (dd >= -0.20) & (dd < -0.10),
        "浅回撤(-10~0%)": (dd >= -0.10) & (dd < 0),
        "高位(dd>=0)": dd >= 0,
    }, dd


def test_one(score, fwd_ex, mask, min_indep=None):
    """按 score 横截面排序取上下四分位，返回超额与 NW t。"""
    mm = np.asarray(mask, dtype=bool)
    mm &= fwd_ex.notna().any(axis=1).values
    if mm.sum() < MIN_OBS:
        return None
    tops, bots, spreads = [], [], []
    for i in np.where(mm)[0]:
        row = score.iloc[i]
        if row.notna().sum() < 12:
            continue
        order = row.dropna().sort_values(ascending=False)
        k = max(2, int(round(len(order) * QUANTILE)))
        f = fwd_ex.iloc[i]
        tv = f[order.index[:k]].dropna()
        bv = f[order.index[-k:]].dropna()
        if len(tv) < 2 or len(bv) < 2:
            continue
        tops.append(tv.mean())
        bots.append(bv.mean())
        spreads.append(tv.mean() - bv.mean())
    if len(tops) < MIN_OBS:
        return None
    tops = np.array(tops) * 100
    spreads = np.array(spreads) * 100
    t_top, n = newey_west_t(tops)
    t_spr, _ = newey_west_t(spreads)

    # 各连续时段的胜率——判断是否是"某一段行情撑起来的"
    eps = episodes(mm)
    pos = tot = 0
    idxs = np.where(mm)[0]
    for s, e, _ in eps:
        seg = [jd for jd, gi in enumerate(idxs) if s <= gi < e]
        if len(seg) >= 10:
            tot += 1
            pos += int(np.mean(spreads[seg]) > 0)
    return {
        "n": int(n),
        "top_excess_pct": round(float(tops.mean()), 3),
        "top_t": round(float(t_top), 2) if np.isfinite(t_top) else None,
        "spread_pct": round(float(spreads.mean()), 3),
        "spread_t": round(float(t_spr), 2) if np.isfinite(t_spr) else None,
        "episodes": len(eps),
        "positive_episodes": f"{pos}/{tot}" if tot else None,
    }


def main():
    print("读取成分与日线…")
    members = load_members()
    all_codes = sorted({c for v in members.values() for c in v["codes"]})
    close, amount, missing = load_history(all_codes)
    print(f"股票 {close.shape[1]} 只，交易日 {close.shape[0]}，缺历史 {missing} 只")

    lvl = build_indices(members, close, amount)
    print(f"合成板块指数 {lvl.shape[1]} 个，"
          f"{lvl.index[0].date()} ~ {lvl.index[-1].date()}")

    ret = lvl.pct_change(fill_method=None)
    feats = build_features(lvl)
    regimes, dd = market_regime(lvl)

    lr = np.log1p(ret)
    results = {
        "sample": {
            "sectors": int(lvl.shape[1]), "days": int(lvl.shape[0]),
            "start": str(lvl.index[0].date()), "end": str(lvl.index[-1].date()),
            "horizons": list(HORIZONS), "nw_lag": NW_LAG,
            "cost_round_trip_pct": COST_ROUND_TRIP * 100,
            "missing_history_pct": round(missing / max(1, len(all_codes)) * 100, 2),
        },
        "annual": [], "by_horizon": [], "split_half": [],
    }

    # ---- 1. 多持有期 × 市场状态 ----
    print(f"\n{'持有期':>6}{'指标':<14}{'状态':<16}{'n':>6}{'前25%超额':>11}"
          f"{'t':>7}{'多空差':>9}{'t':>7}{'正时段':>9}")
    print("-" * 100)
    for h in HORIZONS:
        fwd_ret = np.expm1(lr.rolling(h).sum().shift(-h))
        fwd_ex = fwd_ret.sub(fwd_ret.mean(axis=1, skipna=True), axis=0)
        for fname in FEATURES:
            score = feats[fname]
            for rname, m in regimes.items():
                r = test_one(score, fwd_ex, m.values)
                if r is None:
                    continue
                r.update({"horizon": h, "feature": fname, "regime": rname})
                results["by_horizon"].append(r)
                print(f"{h:>6}{fname:<14}{rname:<16}{r['n']:>6}"
                      f"{r['top_excess_pct']:>10.3f}%"
                      f"{(r['top_t'] or 0):>7.2f}"
                      f"{r['spread_pct']:>8.3f}%{(r['spread_t'] or 0):>7.2f}"
                      f"{(r['positive_episodes'] or '-'):>9}")

    # ---- 2. 分时段稳定性（浅回撤区，dist_high60）----
    print("\n分时段稳定性：浅回撤区 × dist_high60")
    print(f"{'持有期':>6}{'时段':<12}{'n':>6}{'前25%超额':>11}{'t':>7}{'多空差':>9}{'正时段':>9}")
    print("-" * 90)
    shallow = regimes["浅回撤(-10~0%)"].values
    for h in HORIZONS:
        fwd_ret = np.expm1(lr.rolling(h).sum().shift(-h))
        fwd_ex = fwd_ret.sub(fwd_ret.mean(axis=1, skipna=True), axis=0)
        score = feats["dist_high60"]
        years = sorted({d.year for d in lvl.index})
        for y in years:
            ymask = shallow & (lvl.index.year == y)
            r = test_one(score, fwd_ex, ymask)
            if r is None:
                continue
            r.update({"horizon": h, "period": str(y)})
            results["annual"].append(r)
            print(f"{h:>6}{str(y):<12}{r['n']:>6}{r['top_excess_pct']:>10.3f}%"
                  f"{(r['top_t'] or 0):>7.2f}{r['spread_pct']:>8.3f}%"
                  f"{(r['positive_episodes'] or '-'):>9}")

    # ---- 3. 前后半样本切分 ----
    mid = lvl.index[len(lvl.index) // 2]
    for h in HORIZONS:
        fwd_ret = np.expm1(lr.rolling(h).sum().shift(-h))
        fwd_ex = fwd_ret.sub(fwd_ret.mean(axis=1, skipna=True), axis=0)
        score = feats["dist_high60"]
        for label, m2 in (("前半", lvl.index < mid), ("后半", lvl.index >= mid)):
            r = test_one(score, fwd_ex, shallow & m2)
            if r is None:
                continue
            r.update({"horizon": h, "period": f"{label}(<{mid.date()})"
                      if label == "前半" else f"{label}(>={mid.date()})"})
            results["split_half"].append(r)
            print(f"{h:>6}{r['period']:<20}{r['n']:>6}"
                  f"{r['top_excess_pct']:>10.3f}%{(r['top_t'] or 0):>7.2f}"
                  f"{r['spread_pct']:>8.3f}%{(r['positive_episodes'] or '-'):>9}")

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, "sector_horizon_test.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(f"\n已写入 {out}")


if __name__ == "__main__":
    main()
