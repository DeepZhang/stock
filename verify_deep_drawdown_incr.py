# -*- coding: utf-8 -*-
"""深跌区「个股信号 + 板块资金」增量配对检验

verify_stock_with_sector.py 发现 D（∩板块资金高）在深跌区 10 日
+1.779%(t=3.98)，高于 A 的 +1.157%(t=2.02)，看似资金有增量 +0.622%。
但这是两个独立子样本的均值差，不是配对差分——B/C 的对比也说明
t 值高低受样本天数影响。本脚本做严格的配对差分：

  对每个交易日，先取 A 的推荐集合，内部按所属板块资金高低分两组，
  delta = mean(资金高组) - mean(资金低组)，对 delta 序列做 NW t。

同时检查 B（板块强）是否真的劣于 A：逐日配对看 B 是否跑输 A。

用法：python3 verify_deep_drawdown_incr.py
输出：results/deep_drawdown_incr.json
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

HORIZONS = (5, 10, 20)
COST_RT = 0.30
PRICE_FLOOR = 5.0
AMT_FLOOR = 5e7
POOL_Q = 0.10
TOPN = 20


def main():
    print("读取数据…")
    members, close, amount = load_all()
    L, A_, F = build(members, close, amount)

    ret = close.pct_change(fill_method=None)
    ok = (close >= PRICE_FLOOR) & (amount >= AMT_FLOOR) & ret.abs().le(RET_LIMIT)
    ew_bench = ret.where(ok).mean(axis=1, skipna=True)
    dist60 = close / close.rolling(60, min_periods=40).max() - 1
    volr = amount / amount.rolling(20, min_periods=10).mean()

    sec_str = L / L.rolling(60, min_periods=40).max() - 1
    M = pd.DataFrame(False, index=close.columns, columns=sec_str.columns)
    for name, info in members.items():
        if name in sec_str.columns:
            cols = [c for c in info["codes"] if c in close.columns]
            if cols:
                M.loc[cols, name] = True
    stock_str = pd.DataFrame(np.nan, index=close.index, columns=close.columns)
    stock_flow = pd.DataFrame(np.nan, index=close.index, columns=close.columns)
    for name in sec_str.columns:
        sel = M[name].values
        if not sel.any():
            continue
        n_sel = int(sel.sum())
        stock_str.loc[:, sel] = np.fmax(
            stock_str.loc[:, sel].values,
            np.broadcast_to(sec_str[name].values[:, None], (len(close), n_sel)))
        fv = (F[name].values[:, None] if name in F.columns
              else np.full((len(close), 1), np.nan))
        stock_flow.loc[:, sel] = np.fmax(
            stock_flow.loc[:, sel].values,
            np.broadcast_to(fv, (len(close), n_sel)))

    ew = L.pct_change(fill_method=None).mean(axis=1, skipna=True)
    mkt = (1 + ew.fillna(0)).cumprod()
    dd = mkt / mkt.rolling(250, min_periods=120).max() - 1
    deep = (dd < -0.20).values

    res = {"cost_round_trip_pct": COST_RT, "rows": []}
    for h in HORIZONS:
        fwd = np.expm1(np.log1p(ret.where(ok)).rolling(h).sum().shift(-h))
        bench = np.expm1(np.log1p(ew_bench).rolling(h).sum().shift(-h))
        fwd_ex = fwd.sub(bench, axis=0)
        mm = np.asarray(deep, dtype=bool) & fwd_ex.notna().any(axis=1).values

        d_flow, d_str, base_ret = [], [], []
        for i in np.where(mm)[0]:
            d, v, f = dist60.iloc[i], volr.iloc[i], fwd_ex.iloc[i]
            day_ok = ok.iloc[i]
            common = (d.dropna().index.intersection(v.dropna().index)
                      .intersection(f.dropna().index).intersection(day_ok[day_ok].index))
            if len(common) < 200:
                continue
            dv = d[common]
            k = max(5, int(len(common) * POOL_Q))
            pool = dv.sort_values().index[:k]
            vr = v[pool].dropna().sort_values(ascending=False)
            if len(vr) < 5:
                continue
            picks = vr.index[:TOPN]
            fv = f[picks].dropna()
            if len(fv) < 3:
                continue
            base_ret.append(fv.mean())

            fl = stock_flow.iloc[i][picks].dropna()
            if len(fl) >= 6:
                med = fl.median()
                hi = f[fv.index.intersection(fl[fl >= med].index)].dropna()
                lo = f[fv.index.intersection(fl[fl < med].index)].dropna()
                if len(hi) >= 2 and len(lo) >= 2:
                    d_flow.append(hi.mean() - lo.mean())

            st = stock_str.iloc[i][picks].dropna()
            if len(st) >= 6:
                med = st.median()
                hi2 = f[fv.index.intersection(st[st >= med].index)].dropna()
                if len(hi2) >= 2:
                    d_str.append(hi2.mean() - fv.mean())

        if len(base_ret) < MIN_OBS:
            continue
        br = np.array(base_ret) * 100
        t_br, n = newey_west_t(br)
        print(f"\n=== 深跌区 | {h}日 ===")
        print(f"基准 A 个股信号         : {br.mean():+.3f}% (t={t_br:.2f}, n={n})")
        res["rows"].append({"horizon": h, "metric": "A_baseline",
                            "pct": round(float(br.mean()), 3),
                            "nw_t": round(float(t_br), 2), "n": int(n)})
        if len(d_flow) >= MIN_OBS:
            df = np.array(d_flow) * 100
            tf, nf = newey_west_t(df)
            print(f"  池内 资金高-资金低 Δ   : {df.mean():+.3f}% (t={tf:.2f}, n={nf})"
                  f"  -> {'显著' if abs(tf) > 2 else '不显著'}")
            res["rows"].append({"horizon": h, "metric": "flow_delta_in_pool",
                                "pct": round(float(df.mean()), 3),
                                "nw_t": round(float(tf), 2), "n": int(nf)})
        if len(d_str) >= MIN_OBS:
            ds = np.array(d_str) * 100
            ts, ns = newey_west_t(ds)
            print(f"  池内 板块强-池均值 Δ   : {ds.mean():+.3f}% (t={ts:.2f}, n={ns})"
                  f"  -> {'显著' if abs(ts) > 2 else '不显著'}")
            res["rows"].append({"horizon": h, "metric": "strength_delta_in_pool",
                                "pct": round(float(ds.mean()), 3),
                                "nw_t": round(float(ts), 2), "n": int(ns)})

    os.makedirs(os.path.join(BASE, "results"), exist_ok=True)
    out = os.path.join(BASE, "results", "deep_drawdown_incr.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(f"\n已写入 {out}")


if __name__ == "__main__":
    main()
