# -*- coding: utf-8 -*-
"""深跌区「个股池内资金」增量的稳健性检验

verify_deep_drawdown_incr.py 发现：深跌区、个股超跌池内，按所属板块资金
高低配对差分，10 日 Δ=+0.753%(t=2.59)、20 日 Δ=+1.820%(t=3.03)，显著。
这是本次唯一通过配对差分检验的候选信号，必须做稳健性检验才能上线：

  1. 分时段（前/后半、逐年）是否稳定，还是靠某一段
  2. 换切分阈值（中位/30/70分位）是否还成立
  3. 控制"资金高的板块恰好也是强势板块"这一混杂
  4. 换持有期 5/10/20 的方向一致性
  5. 独立时段正负计数

用法：python3 verify_flow_robust.py
输出：results/flow_robust.json
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
from verify_sector_flow import load_all, build, newey_west_t, episodes, MIN_OBS  # noqa: E402

HORIZONS = (5, 10, 20)
COST_RT = 0.30
PRICE_FLOOR = 5.0
AMT_FLOOR = 5e7
POOL_Q = 0.10
TOPN = 20
CUTS = (0.30, 0.50, 0.70)


def build_panel():
    members, close, amount = load_all()
    L, A_, F = build(members, close, amount)
    ret = close.pct_change(fill_method=None)
    ok = (close >= PRICE_FLOOR) & (amount >= AMT_FLOOR) & ret.abs().le(RET_LIMIT)
    dist60 = close / close.rolling(60, min_periods=40).max() - 1
    volr = amount / amount.rolling(20, min_periods=10).mean()
    sec_str = L / L.rolling(60, min_periods=40).max() - 1

    M = pd.DataFrame(False, index=close.columns, columns=sec_str.columns)
    for name, info in members.items():
        if name in sec_str.columns:
            cols = [c for c in info["codes"] if c in close.columns]
            if cols:
                M.loc[cols, name] = True
    stock_flow = pd.DataFrame(np.nan, index=close.index, columns=close.columns)
    stock_str = pd.DataFrame(np.nan, index=close.index, columns=close.columns)
    for name in sec_str.columns:
        sel = M[name].values
        if not sel.any():
            continue
        ns = int(sel.sum())
        fv = (F[name].values[:, None] if name in F.columns
              else np.full((len(close), 1), np.nan))
        stock_flow.loc[:, sel] = np.fmax(
            stock_flow.loc[:, sel].values, np.broadcast_to(fv, (len(close), ns)))
        stock_str.loc[:, sel] = np.fmax(
            stock_str.loc[:, sel].values,
            np.broadcast_to(sec_str[name].values[:, None], (len(close), ns)))

    ew = L.pct_change(fill_method=None).mean(axis=1, skipna=True)
    mkt = (1 + ew.fillna(0)).cumprod()
    dd = mkt / mkt.rolling(250, min_periods=120).max() - 1
    return close, amount, ok, ret, dist60, volr, stock_flow, stock_str, dd, ew


def day_deltas(i, dist60, volr, fwd_ex, ok, stock_flow, stock_str, cut):
    d, v, f = dist60.iloc[i], volr.iloc[i], fwd_ex.iloc[i]
    day_ok = ok.iloc[i]
    common = (d.dropna().index.intersection(v.dropna().index)
              .intersection(f.dropna().index).intersection(day_ok[day_ok].index))
    if len(common) < 200:
        return None
    dv = d[common]
    k = max(5, int(len(common) * POOL_Q))
    pool = dv.sort_values().index[:k]
    vr = v[pool].dropna().sort_values(ascending=False)
    if len(vr) < 5:
        return None
    picks = vr.index[:TOPN]
    fv = f[picks].dropna()
    if len(fv) < 6:
        return None
    out = {"base": fv.mean()}
    fl = stock_flow.iloc[i][picks].dropna()
    if len(fl) >= 6:
        hi_thr = fl.quantile(cut)
        lo_thr = fl.quantile(1 - cut)
        hi = f[fv.index.intersection(fl[fl >= hi_thr].index)].dropna()
        lo = f[fv.index.intersection(fl[fl <= lo_thr].index)].dropna()
        if len(hi) >= 2 and len(lo) >= 2:
            out["flow"] = hi.mean() - lo.mean()
    # 控制强度：在资金高组内，强度高低再差一次
    st = stock_str.iloc[i][picks].dropna()
    if len(fl) >= 6 and len(st) >= 6:
        hi_idx = fl[fl >= fl.quantile(cut)].index
        fi = fv.index.intersection(hi_idx).intersection(st.dropna().index)
        if len(fi) >= 4:
            sv = st[fi]
            med = sv.median()
            a = f[sv[sv >= med].index].dropna()
            b = f[sv[sv < med].index].dropna()
            if len(a) >= 2 and len(b) >= 2:
                out["flow_ctrl_str"] = a.mean() - b.mean()
    return out


def main():
    print("构建面板…")
    close, amount, ok, ret, dist60, volr, sf, ss, dd, ew = build_panel()
    deep = (dd < -0.20).values
    res = {"cost_round_trip_pct": COST_RT, "rows": []}

    for h in HORIZONS:
        fwd = np.expm1(np.log1p(ret.where(ok)).rolling(h).sum().shift(-h))
        bench = np.expm1(np.log1p(ret.where(ok).mean(axis=1, skipna=True)).rolling(h).sum().shift(-h))
        fwd_ex = fwd.sub(bench, axis=0)
        mm = np.asarray(deep, dtype=bool) & fwd_ex.notna().any(axis=1).values
        idxs = np.where(mm)[0]

        for cut in CUTS:
            flow_d, ctrl_d, base_d, days = [], [], [], []
            for i in idxs:
                r = day_deltas(i, dist60, volr, fwd_ex, ok, sf, ss, cut)
                if not r:
                    continue
                base_d.append(r["base"])
                days.append(i)
                if "flow" in r:
                    flow_d.append(r["flow"])
                if "flow_ctrl_str" in r:
                    ctrl_d.append(r["flow_ctrl_str"])
            if len(flow_d) < MIN_OBS:
                continue
            fd = np.array(flow_d) * 100
            tf, nf = newey_west_t(fd)
            print(f"\n--- {h}日 cut={cut:.2f} | 深跌区 ---")
            print(f"  资金高低 Δ        : {fd.mean():+.3f}% (t={tf:.2f}, n={nf})")
            row = {"horizon": h, "cut": cut, "metric": "flow_delta",
                   "pct": round(float(fd.mean()), 3),
                   "nw_t": round(float(tf), 2), "n": int(nf)}
            # 独立时段正负
            dmask = np.zeros(len(close), dtype=bool)
            dmask[np.array(days[:len(flow_d)])] = True
            pos = tot = 0
            for s, e, _ in episodes(dmask & np.asarray(deep)):
                seg = [j for j, gi in enumerate(days[:len(flow_d)]) if s <= gi < e]
                if len(seg) >= 10:
                    tot += 1
                    pos += int(np.mean(fd[seg]) > 0)
            row["positive_episodes"] = f"{pos}/{tot}" if tot else None
            print(f"  独立时段为正      : {pos}/{tot}")
            res["rows"].append(row)

            if len(ctrl_d) >= MIN_OBS:
                cd = np.array(ctrl_d) * 100
                tc, nc = newey_west_t(cd)
                print(f"  控制强度后 Δ      : {cd.mean():+.3f}% (t={tc:.2f}, n={nc})")
                res["rows"].append({"horizon": h, "cut": cut,
                                    "metric": "flow_ctrl_strength",
                                    "pct": round(float(cd.mean()), 3),
                                    "nw_t": round(float(tc), 2), "n": int(nc)})

        # 分时段（用 cut=0.5）
        for label, m2 in (("前半", close.index < close.index[len(close)//2]),
                          ("后半", close.index >= close.index[len(close)//2])):
            flow_d = []
            for i in idxs:
                if not m2[i]:
                    continue
                r = day_deltas(i, dist60, volr, fwd_ex, ok, sf, ss, 0.5)
                if r and "flow" in r:
                    flow_d.append(r["flow"])
            if len(flow_d) < 20:
                continue
            fd = np.array(flow_d) * 100
            tf, nf = newey_west_t(fd)
            print(f"  {label}样本 资金Δ  : {fd.mean():+.3f}% (t={tf:.2f}, n={nf})")
            res["rows"].append({"horizon": h, "cut": 0.5,
                                "metric": f"flow_delta_{label}",
                                "pct": round(float(fd.mean()), 3),
                                "nw_t": round(float(tf), 2), "n": int(nf)})

    os.makedirs(os.path.join(BASE, "results"), exist_ok=True)
    out = os.path.join(BASE, "results", "flow_robust.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(f"\n已写入 {out}")


if __name__ == "__main__":
    main()
