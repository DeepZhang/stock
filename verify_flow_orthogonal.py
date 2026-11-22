# -*- coding: utf-8 -*-
"""资金流是否独立于板块强度？——正交化检验

前面发现：深跌区个股超跌池内，按板块资金高低配对差分 Δ 显著（10日 t=2.56）。
但"控制强度后转负"提示资金可能只是强度的代理变量。

正确的检验不是"高资金组内按强度切分"（那测的是强度），
而是：**在同一强度层内比较资金高低**。若资金在每一强度层都有效，
说明它是独立信息；若只在某层有效或全部失效，说明它与强度共线。

做法：每日在超跌池内，先按板块强度分 3 层，每层内按资金中位数分高低，
计算 flow 增量；再对每层结果加权汇总。

同时计算 stock_flow 与 stock_str 的秩相关，量化共线程度。

用法：python3 verify_flow_orthogonal.py
输出：results/flow_orthogonal.json
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
from verify_flow_robust import build_panel  # noqa: E402

HORIZONS = (10, 20)
POOL_Q = 0.10
TOPN = 20
NSTRAT = 3


def main():
    print("构建面板…")
    close, amount, ok, ret, dist60, volr, sf, ss, dd, ew = build_panel()
    deep = (dd < -0.20).values

    # 共线性：资金 vs 强度
    corrs = []
    for i in np.where(np.asarray(deep, dtype=bool))[0]:
        a, b = sf.iloc[i].dropna(), ss.iloc[i].dropna()
        common = a.index.intersection(b.index)
        if len(common) >= 200:
            corrs.append(a[common].rank().corr(b[common].rank(), method="pearson"))
    corrs = np.array([c for c in corrs if np.isfinite(c)])
    tc, nc = newey_west_t(corrs)
    print(f"\n资金流与板块强度的秩相关（深跌区，逐日）："
          f"均值 {corrs.mean():+.3f} (t={tc:.2f}, n={nc})")
    print("  -> 越接近 0 越独立；正相关说明资金高≈强度高")

    res = {"flow_strength_rank_corr": round(float(corrs.mean()), 4),
           "corr_nw_t": round(float(tc), 2), "rows": []}

    for h in HORIZONS:
        fwd = np.expm1(np.log1p(ret.where(ok)).rolling(h).sum().shift(-h))
        bench = np.expm1(np.log1p(ret.where(ok).mean(axis=1, skipna=True))
                         .rolling(h).sum().shift(-h))
        fwd_ex = fwd.sub(bench, axis=0)
        mm = np.asarray(deep, dtype=bool) & fwd_ex.notna().any(axis=1).values

        per_stratum = {s: [] for s in range(NSTRAT)}
        pooled = []
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
            st = ss.iloc[i][picks].dropna()
            fl = sf.iloc[i][picks].dropna()
            both = st.index.intersection(fl.index)
            if len(both) < 9:
                continue
            strat = pd.qcut(st[both].rank(method="first"), NSTRAT,
                            labels=False, duplicates="drop")
            day_vals = []
            for s in range(NSTRAT):
                members_s = strat[strat == s].index
                if len(members_s) < 4:
                    continue
                fv = fl[members_s]
                med = fv.median()
                hi = f[fv.index.intersection(fv[fv >= med].index)].dropna()
                lo = f[fv.index.intersection(fv[fv < med].index)].dropna()
                if len(hi) >= 1 and len(lo) >= 1:
                    per_stratum[s].append(hi.mean() - lo.mean())
                    day_vals.append(hi.mean() - lo.mean())
            if day_vals:
                pooled.append(np.mean(day_vals))

        print(f"\n=== {h}日 | 深跌区 | 强度分层内比较资金 ===")
        for s in range(NSTRAT):
            v = per_stratum[s]
            if len(v) < MIN_OBS:
                print(f"  强度第{s+1}层: 样本不足 ({len(v)})")
                continue
            arr = np.array(v) * 100
            t, n = newey_west_t(arr)
            label = ["弱", "中", "强"][s]
            print(f"  强度第{s+1}层({label})  资金Δ: {arr.mean():+.3f}% "
                  f"(t={t:.2f}, n={n})")
            res["rows"].append({"horizon": h, "stratum": label,
                                "pct": round(float(arr.mean()), 3),
                                "nw_t": round(float(t), 2), "n": int(n)})
        if len(pooled) >= MIN_OBS:
            arr = np.array(pooled) * 100
            t, n = newey_west_t(arr)
            print(f"  ** 各层平均(正交化后)  资金Δ: {arr.mean():+.3f}% "
                  f"(t={t:.2f}, n={n})")
            res["rows"].append({"horizon": h, "stratum": "ALL_pooled",
                                "pct": round(float(arr.mean()), 3),
                                "nw_t": round(float(t), 2), "n": int(n)})

    os.makedirs(os.path.join(BASE, "results"), exist_ok=True)
    out = os.path.join(BASE, "results", "flow_orthogonal.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(f"\n已写入 {out}")


if __name__ == "__main__":
    main()
