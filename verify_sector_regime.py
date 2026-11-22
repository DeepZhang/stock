# -*- coding: utf-8 -*-
"""板块强度指标的分状态有效性验证（v2：自建板块指数，覆盖 2015-2026）

v1 的问题：直接用东方财富官方 BK 板块指数，但这些指数大多 2019 年后才发布，
2015-2018 只有 6 个板块有数据，做不了横截面排序。全样本结论其实只反映近几年。

v2 做法：
  1. 用本地个股前复权日线 + 23 板块成分快照，自建等权板块指数，覆盖 2015-01 起
  2. 在重叠期用官方 BK 指数校验自建指数（日收益相关性），证明构造可信
  3. 在完整样本上检验：按某指标给 23 个板块排序 → 取上下四分位 → 看未来 20 日
     相对 23 板块等权的超额收益
  4. 分市场状态（等权净值距 250 日高点）看指标方向是否翻转
  5. 用 NW t（lag=20）处理重叠窗口，并统计"独立时段"个数——
     深跌期看似几百天，实际只来自几次行情，不能当成几百个独立样本

已知偏差：成分快照是 2026-11-08 冻结的，退市股不在其中，历史统计存在幸存者偏差。
脚本会定量估计这个偏差的方向和量级。
"""
import json
import os
import sys

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
HIST_DIR = os.path.join(BASE, "data", "history")
MEMBERS = os.path.join(BASE, "data", "sector_members.json")
IDX_DIR = os.path.join(BASE, "data", "sector_index")
OUT_DIR = os.path.join(BASE, "results")
sys.path.insert(0, os.path.join(BASE, "modules"))
from price_clean import RET_LIMIT, load_clean, sector_returns  # noqa: E402

FWD = 20
NW_LAG = 20
MIN_OBS = 30
LIQ_FLOOR = 5e7

# 市值因子而非行业，横截面排序意义不同，单独标注
FACTOR_SECTORS = {"微盘股"}


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
    """读取并清洗前复权日线。

    用 price_clean 做两级清洗（成片损坏截断 / 孤立脏 bar 打洞），
    而非整只股票丢弃。见 modules/price_clean.py 的实测依据。
    """
    close, amount, missing, repaired = {}, {}, 0, 0
    for code in codes:
        p = os.path.join(HIST_DIR, f"{code}.parquet")
        d, qc = load_clean(p)
        if d is None:
            missing += 1
            continue
        if qc.get("truncated_bars"):
            repaired += 1
        idx = pd.DatetimeIndex(d["date"])
        close[code] = pd.Series(d["close"].values, index=idx)
        if "amount" in d.columns:
            amount[code] = pd.Series(d["amount"].values, index=idx)
    print(f"清洗：修复截断 {repaired} 只，不可用 {missing} 只")
    return pd.DataFrame(close).sort_index(), pd.DataFrame(amount).sort_index(), missing


def build_sector_indices(members, close, amount):
    """等权 + 中位两条合成指数。等权用日收益平均，避免价格量级差异。"""
    lvl_ew, lvl_med, liq_ok = {}, {}, {}
    for name, info in members.items():
        cols = [c for c in info["codes"] if c in close.columns]
        if len(cols) < 5:
            continue
        px = close[cols]
        amt = amount.reindex(columns=cols)
        # 逐日剔除越界收益（A股涨跌幅上限外必为脏数据）+ 流动性过滤
        ew, cov = sector_returns(px, amt, winsor=RET_LIMIT,
                                 liquidity_floor=LIQ_FLOOR, min_stocks=5)
        ret = px.pct_change(fill_method=None).where(ret_ok(px), np.nan)
        med = ret.median(axis=1, skipna=True)       # 中位日收益
        lvl_ew[name] = (1 + ew.fillna(0)).cumprod()
        lvl_med[name] = (1 + med.fillna(0)).cumprod()
        liq_ok[name] = cov
    return pd.DataFrame(lvl_ew), pd.DataFrame(lvl_med), pd.DataFrame(liq_ok)


def ret_ok(px, limit=RET_LIMIT):
    """有效收益掩码：价格非空且日收益在涨跌幅上限内。"""
    r = px.pct_change(fill_method=None)
    return px.notna() & r.notna() & (r.abs() <= limit)


def load_official(bk):
    p = os.path.join(IDX_DIR, f"{bk}.json")
    if not os.path.exists(p):
        return None
    s = open(p, encoding="utf-8").read().strip()
    if not s or s == "[]":
        return None
    rows = []
    for line in json.loads(s):
        parts = line.split(",")
        if len(parts) < 3:
            continue
        try:
            rows.append((parts[0], float(parts[2])))
        except ValueError:
            continue
    if len(rows) < 250:
        return None
    ser = pd.Series(dict(rows))
    ser.index = pd.to_datetime(ser.index)
    return ser.sort_index()


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
    return [(s, e, e - s) for s, e in zip(starts, ends)]


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    members = load_members()
    all_codes = sorted({c for v in members.values() for c in v["codes"]})
    print(f"板块 {len(members)} 个，成分合计 {len(all_codes)} 只")

    close, amount, missing = load_history(all_codes)
    print(f"读取本地日线：命中 {close.shape[1]} 只，缺历史 {missing} 只 "
          f"({missing / max(1, len(all_codes)) * 100:.1f}%)")

    lvl, lvl_med, liq = build_sector_indices(members, close, amount)
    lvl = lvl.loc[lvl.index >= "2015-01-01"]
    lvl_med = lvl_med.reindex(lvl.index)
    liq = liq.reindex(lvl.index)
    print(f"自建板块指数：{lvl.shape[1]} 板块 × {lvl.shape[0]} 交易日 "
          f"({lvl.index[0]:%Y-%m-%d} ~ {lvl.index[-1]:%Y-%m-%d})")

    # ---- 与官方指数校验 ----
    print("\n[校验] 自建等权指数 vs 官方 BK 指数（日收益相关性，重叠期）")
    ver = {"corr": {}, "median_corr": None}
    cors = []
    for name, info in members.items():
        if name not in lvl.columns:
            continue
        off = load_official(info["bk"]) if info.get("bk") else None
        if off is None:
            print(f"  {name:<10} 官方指数不可用")
            continue
        a = lvl[name].pct_change()
        b = off.pct_change()
        j = pd.concat([a, b], axis=1, keys=["mine", "off"]).dropna()
        if len(j) < 250:
            print(f"  {name:<10} 重叠期不足（{len(j)} 天）")
            continue
        c = j["mine"].corr(j["off"])
        cors.append(c)
        ver["corr"][name] = round(float(c), 3)
        print(f"  {name:<10} r={c:.3f}  重叠 {len(j):>5} 天  "
              f"{j.index[0]:%Y-%m-%d}~{j.index[-1]:%Y-%m-%d}")
    if cors:
        ver["median_corr"] = round(float(np.median(cors)), 3)
        print(f"  中位相关性 = {np.median(cors):.3f}")

    # ---- 特征 ----
    ret = lvl.pct_change()
    ew_mkt = ret.mean(axis=1, skipna=True)
    mkt_lvl = (1 + ew_mkt.fillna(0)).cumprod()
    dd = mkt_lvl / mkt_lvl.rolling(250, min_periods=120).max() - 1

    feats = {
        "dist_high250": lvl / lvl.rolling(250, min_periods=120).max() - 1,
        "dist_high60": lvl / lvl.rolling(60, min_periods=40).max() - 1,
        "ret20": lvl / lvl.shift(20) - 1,
        "ret60": lvl / lvl.shift(60) - 1,
        "ret5": lvl / lvl.shift(5) - 1,
        "mom_vol_adj": (lvl / lvl.shift(60) - 1) / (ret.rolling(60).std() * np.sqrt(60)),
        "low_vol": -ret.rolling(20).std(),
    }
    feats["rel_ew60"] = feats["ret60"].sub(feats["ret60"].mean(axis=1), axis=0)

    lr = np.log1p(ret)
    fwd_ret = np.expm1(lr.rolling(FWD).sum().shift(-FWD))
    fwd_ex = fwd_ret.sub(fwd_ret.mean(axis=1, skipna=True), axis=0)

    valid = fwd_ex.notna().any(axis=1) & dd.notna()
    regimes = {
        "深跌(dd<-20%)": dd < -0.20,
        "中度(-20~-10%)": (dd >= -0.20) & (dd < -0.10),
        "浅回撤(-10~0%)": (dd >= -0.10) & (dd < 0),
        "高位(dd>=0)": dd >= 0,
    }

    print(f"\n各状态交易日数与独立时段（有效样本 {int(valid.sum())} 天，"
          f"每行含 {lvl.shape[1]} 个板块）")
    results = {
        "sample": {"sectors": int(lvl.shape[1]), "days": int(lvl.shape[0]),
                   "start": str(lvl.index[0].date()), "end": str(lvl.index[-1].date()),
                   "fwd_window": FWD, "nw_lag": NW_LAG,
                   "missing_history_pct": round(missing / max(1, len(all_codes)) * 100, 2)},
        "index_validation": ver, "regimes": {}, "tests": [],
    }
    for rname, m in regimes.items():
        mm = (m & valid).values
        eps = episodes(mm)
        results["regimes"][rname] = {
            "days": int(mm.sum()), "episodes": len(eps),
            "episode_lengths": [int(e[2]) for e in eps],
            "episode_start": [str(lvl.index[e[0]].date()) for e in eps],
        }
        print(f"  {rname:<16}{int(mm.sum()):>5} 天 | 连续时段 {len(eps):>2} 个 | "
              f"时段起点 {[str(lvl.index[e[0]].date()) for e in eps][:8]}")

    print(f"\n{'指标':<14}{'状态':<16}{'前25%超额':>11}{'NW t':>7}{'后25%超额':>11}{'NW t':>7}"
          f"{'多空差':>9}{'时段':>5}{'正时段':>9}")
    print("-" * 100)

    for fname in ["dist_high250", "dist_high60", "ret60", "ret20", "ret5",
                  "mom_vol_adj", "low_vol", "rel_ew60"]:
        score = feats[fname]
        for rname, m in regimes.items():
            mm = (m & valid).values
            if mm.sum() < MIN_OBS:
                continue
            tops, bots, spreads = [], [], []
            for i in np.where(mm)[0]:
                row = score.iloc[i]
                if row.notna().sum() < 12:
                    continue
                order = row.dropna().sort_values(ascending=False)
                k = max(2, int(round(len(order) * 0.25)))
                f = fwd_ex.iloc[i]
                tv = f[order.index[:k]].dropna()
                bv = f[order.index[-k:]].dropna()
                if len(tv) < 2 or len(bv) < 2:
                    continue
                tops.append(tv.mean()); bots.append(bv.mean())
                spreads.append(tv.mean() - bv.mean())
            if len(tops) < MIN_OBS:
                continue
            tops = np.array(tops) * 100; bots = np.array(bots) * 100
            spreads = np.array(spreads) * 100
            t_top, n = newey_west_t(tops)
            t_bot, _ = newey_west_t(bots)
            eps = episodes(mm)
            pos = tot = 0
            for s, e, _ in eps:
                seg = spreads[s:e]
                if len(seg) >= 10:
                    tot += 1
                    pos += int(np.mean(seg) > 0)
            results["tests"].append({
                "feature": fname, "regime": rname, "n": int(n),
                "top25_excess_pct": round(float(tops.mean()), 3),
                "top25_nw_t": round(float(t_top), 2) if np.isfinite(t_top) else None,
                "bot25_excess_pct": round(float(bots.mean()), 3),
                "bot25_nw_t": round(float(t_bot), 2) if np.isfinite(t_bot) else None,
                "spread_pct": round(float(spreads.mean()), 3),
                "episodes": len(eps), "positive_episodes": f"{pos}/{tot}" if tot else None,
            })
            print(f"{fname:<14}{rname:<16}{tops.mean():>10.3f}%"
                  f"{(t_top if np.isfinite(t_top) else 0):>7.2f}"
                  f"{bots.mean():>10.3f}%{(t_bot if np.isfinite(t_bot) else 0):>7.2f}"
                  f"{spreads.mean():>8.3f}%{len(eps):>5}{f'{pos}/{tot}':>9}")

    out = os.path.join(OUT_DIR, "sector_regime_test.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(f"\n已保存 {out}")


if __name__ == "__main__":
    main()
