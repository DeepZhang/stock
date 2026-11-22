# -*- coding: utf-8 -*-
"""板块强势能否用于在「浅回撤区」选个股？

背景：daily_recommend 只在深跌区给推荐（个股超跌反弹），浅回撤/中度/高位
一律显示"不出手"。但浅回撤区占全部交易日的 46%，是覆盖率最大的状态。
若板块强势在该状态能筛个股，就能把推荐覆盖到这些日子。

本脚本做三件事：
  1. 用板块 dist_high60 给每只股票打"所属板块强度"（多板块取最强）
  2. 浅回撤区按强度分档，看未来 10/20 日超额是否单调
  3. 深跌区做同样检验作为对照（预期无效，验证与既有结论一致）

用法：python3 verify_sector_stock_shallow.py
输出：results/sector_stock_shallow.json
"""
import json
import os
import sys

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "modules"))
from price_clean import RET_LIMIT  # noqa: E402
from verify_sector_flow import load_all, build, newey_west_t, MIN_OBS  # noqa: E402

HORIZONS = (10, 20)
COST_RT = 0.30
PRICE_FLOOR = 5.0
AMT_FLOOR = 5e7
BUCKETS = 5


def main():
    print("读取数据…")
    members, close, amount = load_all()
    L, A, _ = build(members, close, amount)
    print(f"板块 {L.shape[1]} 个，股票 {close.shape[1]} 只，"
          f"{close.index[0].date()} ~ {close.index[-1].date()}")

    # 可交易池（与项目既有口径一致）
    ret = close.pct_change(fill_method=None)
    ok = (close >= PRICE_FLOOR) & (amount >= AMT_FLOOR) & ret.abs().le(RET_LIMIT)
    ew_stock = ret.where(ok).mean(axis=1, skipna=True)

    # 板块强度
    sec_str = L / L.rolling(60, min_periods=40).max() - 1

    # 每只股票取所属板块中最强的强度（多板块票按最强口径）
    M = pd.DataFrame(False, index=close.columns, columns=sec_str.columns)
    for name, info in members.items():
        if name not in sec_str.columns:
            continue
        cols = [c for c in info["codes"] if c in close.columns]
        if cols:
            M.loc[cols, name] = True
    stock_str = pd.DataFrame(np.nan, index=close.index, columns=close.columns)
    for name in sec_str.columns:
        sel = M[name].values
        if not sel.any():
            continue
        block = stock_str.loc[:, sel].values
        colv = sec_str[name].values[:, None]
        stock_str.loc[:, sel] = np.fmax(block, np.broadcast_to(colv, block.shape))
    print(f"板块强度覆盖股票 {int(stock_str.notna().any().sum())} 只，"
          f"有小盘股/概念重叠取最强")

    # 市场状态
    ew = L.pct_change(fill_method=None).mean(axis=1, skipna=True)
    mkt = (1 + ew.fillna(0)).cumprod()
    dd = mkt / mkt.rolling(250, min_periods=120).max() - 1
    regimes = {
        "浅回撤(-10~0%)": ((dd >= -0.10) & (dd < 0)).values,
        "深跌(dd<-20%)": (dd < -0.20).values,
        "中度(-20~-10%)": ((dd >= -0.20) & (dd < -0.10)).values,
    }

    lr = np.log1p(ret.where(ok))
    res = {"cost_round_trip_pct": COST_RT, "rows": []}

    for h in HORIZONS:
        fwd = np.expm1(lr.rolling(h).sum().shift(-h))
        fwd_ex = fwd.sub(ew_stock.rolling(h).sum().shift(-h) * 0 + fwd.mean(axis=1, skipna=True),
                         axis=0)
        for rname, rmask in regimes.items():
            mm = np.asarray(rmask, dtype=bool) & fwd_ex.notna().any(axis=1).values
            if mm.sum() < MIN_OBS:
                continue
            buckets = {i: [] for i in range(BUCKETS)}
            for i in np.where(mm)[0]:
                s = stock_str.iloc[i]
                f = fwd_ex.iloc[i]
                common = s.dropna().index.intersection(f.dropna().index)
                if len(common) < 100:
                    continue
                sv = s[common]
                try:
                    q = pd.qcut(sv.rank(method="first"), BUCKETS, labels=False)
                except ValueError:
                    continue
                for b in range(BUCKETS):
                    v = f[q[q == b].index].dropna()
                    if len(v) >= 5:
                        buckets[b].append(v.mean())
            if len(buckets[BUCKETS - 1]) < MIN_OBS:
                continue
            print(f"\n=== {h}日 | {rname} | 按所属板块强度分 {BUCKETS} 档"
                  f"（超额%，已扣成本 {COST_RT}%）")
            print(f"{'档位':<12}{'毛超额':>10}{'NW t':>8}{'净超额':>10}{'n':>7}")
            for b in range(BUCKETS):
                v = buckets[b]
                if len(v) < MIN_OBS:
                    continue
                arr = np.array(v) * 100
                t, n = newey_west_t(arr)
                label = ("最强" if b == BUCKETS - 1 else
                         "最弱" if b == 0 else f"第{b+1}档")
                print(f"{label:<12}{arr.mean():>9.3f}%"
                      f"{(t if np.isfinite(t) else 0):>8.2f}"
                      f"{arr.mean() - COST_RT:>9.3f}%{n:>7}")
                res["rows"].append({"horizon": h, "regime": rname, "bucket": label,
                                    "bucket_idx": b, "excess_pct": round(float(arr.mean()), 3),
                                    "nw_t": round(float(t), 2) if np.isfinite(t) else None,
                                    "n": int(n),
                                    "net_pct": round(float(arr.mean()) - COST_RT, 3)})
            # 最强 - 最弱
            hi, lo = np.array(buckets[BUCKETS - 1]) * 100, np.array(buckets[0]) * 100
            if len(hi) == len(lo) and len(hi) >= MIN_OBS:
                d = hi - lo
                td, nd = newey_west_t(d)
                print(f"{'最强-最弱':<12}{d.mean():>9.3f}%"
                      f"{(td if np.isfinite(td) else 0):>8.2f}")
                res["rows"].append({"horizon": h, "regime": rname,
                                    "bucket": "最强-最弱", "bucket_idx": 99,
                                    "excess_pct": round(float(d.mean()), 3),
                                    "nw_t": round(float(td), 2) if np.isfinite(td) else None,
                                    "n": int(nd), "net_pct": None})

    os.makedirs(os.path.join(BASE, "results"), exist_ok=True)
    out = os.path.join(BASE, "results", "sector_stock_shallow.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(f"\n已写入 {out}")


if __name__ == "__main__":
    main()
