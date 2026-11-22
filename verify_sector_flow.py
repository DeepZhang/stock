# -*- coding: utf-8 -*-
"""板块「资金流入」是否有增量价值？

用户的核心诉求是"哪些板块强势、有资金进入、适合买"。前两步已验：
板块强弱信号在浅回撤区有效（10 日 t=4.5），但毛超额仅 0.4%，
扣成本后所剩无几。本脚本回答第三步：在此之上，"资金流入"能否提高准确率？

资金口径（板块内成分股成交额合计，仅有当日快照可回测）：
  flow_ratio = 近5日均额 / 近20日均额 - 1
这是 daily_recommend.sector_fund_flow 的线上口径，故用同一口径检验。

两种检验：
  A. 单独看资金：按 flow 排序取上下四分位，未来 10/20 日超额
  B. 强弱 × 资金 2x2：在强势板块内部，高资金是否优于低资金
     （这才是"强势且有资金进入"的真实收益，也是页面要展示的表）

用法：python3 verify_sector_flow.py
输出：results/sector_flow_test.json
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
from price_clean import RET_LIMIT, load_clean, sector_returns  # noqa: E402

HORIZONS = (10, 20)
NW_LAG = 20
MIN_OBS = 30
LIQ_FLOOR = 5e7
MIN_XS = 8          # 横截面最少板块数
COST_RT = 0.30      # 往返成本 %


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


def load_all():
    with open(MEMBERS, encoding="utf-8") as f:
        raw = json.load(f)["sectors"]
    members = {n: {"bk": v.get("bk"),
                   "codes": [m["code"] for m in v.get("members", []) if m.get("code")]}
               for n, v in raw.items()}
    members = {n: v for n, v in members.items() if v["codes"]}
    codes = sorted({c for v in members.values() for c in v["codes"]})

    close, amount = {}, {}
    for c in codes:
        d, _ = load_clean(os.path.join(HIST_DIR, f"{c}.parquet"))
        if d is None:
            continue
        idx = pd.DatetimeIndex(d["date"])
        close[c] = pd.Series(d["close"].values, index=idx)
        if "amount" in d.columns:
            amount[c] = pd.Series(d["amount"].values, index=idx)
    return members, pd.DataFrame(close).sort_index(), pd.DataFrame(amount).sort_index()


def build(members, close, amount):
    """等权板块指数 + 板块成交额合计（资金口径）。"""
    lvl, amt_s, flow, broke = {}, {}, {}, {}
    for name, info in members.items():
        cols = [c for c in info["codes"] if c in close.columns]
        if len(cols) < 5:
            continue
        px = close[cols]
        am = amount.reindex(columns=cols)
        ew, _ = sector_returns(px, am, winsor=RET_LIMIT,
                               liquidity_floor=LIQ_FLOOR, min_stocks=5)
        lvl[name] = (1 + ew.fillna(0)).cumprod()

        # 板块资金 = 成分股成交额合计（按当日有效成分）
        a = am.where(px.notna() & px.pct_change(fill_method=None).abs().le(RET_LIMIT))
        s = a.sum(axis=1, min_count=3)
        amt_s[name] = s
        m5 = s.rolling(5, min_periods=3).mean()
        m20 = s.rolling(20, min_periods=10).mean()
        flow[name] = (m5 / m20 - 1) * 100          # 百分比

    L = pd.DataFrame(lvl)
    A = pd.DataFrame(amt_s)
    F = pd.DataFrame(flow)
    return L, A, F


def quadrant_table(score, flow, fwd_ex, mask):
    """强弱 × 资金 2x2：每日中位数切分，记录各象限平均超额。"""
    mm = np.asarray(mask, dtype=bool) & fwd_ex.notna().any(axis=1).values
    if mm.sum() < MIN_OBS:
        return None, None
    quads = {"强+资金高": [], "强+资金低": [], "弱+资金高": [], "弱+资金低": []}
    flow_only = {"资金高": [], "资金低": []}
    for i in np.where(mm)[0]:
        s, f, fe = score.iloc[i], flow.iloc[i], fwd_ex.iloc[i]
        common = s.dropna().index.intersection(f.dropna().index).intersection(fe.dropna().index)
        if len(common) < MIN_XS:
            continue
        sv, fv = s[common], f[common]
        smed, fmed = sv.median(), fv.median()
        hi_s = sv >= smed
        hi_f = fv >= fmed
        for key, sel in (("强+资金高", hi_s & hi_f), ("强+资金低", hi_s & ~hi_f),
                         ("弱+资金高", ~hi_s & hi_f), ("弱+资金低", ~hi_s & ~hi_f)):
            v = fe[common][sel].dropna()
            if len(v) >= 2:
                quads[key].append(v.mean())
        for key, sel in (("资金高", hi_f), ("资金低", ~hi_f)):
            v = fe[common][sel].dropna()
            if len(v) >= 2:
                flow_only[key].append(v.mean())

    def summ(d):
        out = {}
        for k, v in d.items():
            if len(v) < MIN_OBS:
                continue
            arr = np.array(v) * 100
            t, n = newey_west_t(arr)
            out[k] = {"excess_pct": round(float(arr.mean()), 3),
                      "nw_t": round(float(t), 2) if np.isfinite(t) else None,
                      "n": int(n)}
        return out

    return summ(quads), summ(flow_only)


def main():
    print("读取数据…")
    members, close, amount = load_all()
    L, A, F = build(members, close, amount)
    print(f"板块 {L.shape[1]} 个，{L.index[0].date()} ~ {L.index[-1].date()}，"
          f"股票 {close.shape[1]} 只")

    ret = L.pct_change(fill_method=None)
    lr = np.log1p(ret)
    feats = {
        "dist_high60": L / L.rolling(60, min_periods=40).max() - 1,
        "dist_high250": L / L.rolling(250, min_periods=120).max() - 1,
    }
    ew = ret.mean(axis=1, skipna=True)
    mkt = (1 + ew.fillna(0)).cumprod()
    dd = mkt / mkt.rolling(250, min_periods=120).max() - 1
    regimes = {
        "深跌(dd<-20%)": (dd < -0.20).values,
        "中度(-20~-10%)": ((dd >= -0.20) & (dd < -0.10)).values,
        "浅回撤(-10~0%)": ((dd >= -0.10) & (dd < 0)).values,
        "高位(dd>=0)": (dd >= 0).values,
    }

    res = {"sample": {"sectors": int(L.shape[1]), "days": int(L.shape[0]),
                      "start": str(L.index[0].date()), "end": str(L.index[-1].date()),
                      "cost_round_trip_pct": COST_RT}, "quadrants": [], "flow_only": []}

    for h in HORIZONS:
        fwd = np.expm1(lr.rolling(h).sum().shift(-h))
        fwd_ex = fwd.sub(fwd.mean(axis=1, skipna=True), axis=0)
        for fname, score in feats.items():
            for rname, m in regimes.items():
                q, fo = quadrant_table(score, F, fwd_ex, m)
                if not q:
                    continue
                print(f"\n=== {h}日 | {fname} | {rname} （超额%，已扣成本 {COST_RT}%）")
                print(f"{'象限':<12}{'毛超额':>10}{'NW t':>8}{'净超额':>10}{'n':>7}")
                for k in ("强+资金高", "强+资金低", "弱+资金高", "弱+资金低"):
                    if k not in q:
                        continue
                    v = q[k]
                    net = v["excess_pct"] - COST_RT
                    print(f"{k:<12}{v['excess_pct']:>9.3f}%{(v['nw_t'] or 0):>8.2f}"
                          f"{net:>9.3f}%{v['n']:>7}")
                    res["quadrants"].append({"horizon": h, "feature": fname,
                                             "regime": rname, "quadrant": k,
                                             "excess_pct": v["excess_pct"],
                                             "nw_t": v["nw_t"], "n": v["n"],
                                             "net_pct": round(net, 3)})
                if fo:
                    print(f"  {'-- 仅资金 --':<20}", end="")
                    for k in ("资金高", "资金低"):
                        if k in fo:
                            print(f"  {k} {fo[k]['excess_pct']:+.3f}%"
                                  f"(t={fo[k]['nw_t']})", end="")
                            res["flow_only"].append({"horizon": h, "feature": fname,
                                                     "regime": rname, "group": k,
                                                     "excess_pct": fo[k]["excess_pct"],
                                                     "nw_t": fo[k]["nw_t"], "n": fo[k]["n"]})
                    print()

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, "sector_flow_test.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(f"\n已写入 {out}")


if __name__ == "__main__":
    main()
