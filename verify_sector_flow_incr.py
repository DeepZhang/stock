# -*- coding: utf-8 -*-
"""增量检验：资金流在「板块强势」之上是否还有额外价值？

verify_sector_flow.py 只报了各象限的绝对超额与其水平 t 值，
无法回答"加了资金过滤，比不加更好吗"。水平 t 值高只说明该象限正收益，
不代表资金有增量。本脚本做配对差分：

  每日在「强势前 25%」板块内，按资金流再分高/低两组：
    delta = mean(强+资金高) - mean(强+资金低)
  对 delta 序列做 NW t 检验。只有 delta 显著为正，资金才算增量。

对照组：只取强势前 25%（不加资金过滤）作为基准，看加入资金后是否提升。

用法：python3 verify_sector_flow_incr.py
输出：results/sector_flow_incr.json
"""
import json
import os
import sys

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "modules"))
from verify_sector_flow import (  # noqa: E402
    MIN_OBS, MIN_XS, NW_LAG, load_all, build, newey_west_t, episodes,
)

HORIZONS = (5, 10, 20)
COST_RT = 0.30


def incremental(score, flow, fwd_ex, mask, sel_q=0.25):
    """强势前 sel_q 内，按资金中位数分两组，返回配对差分统计。"""
    mm = np.asarray(mask, dtype=bool) & fwd_ex.notna().any(axis=1).values
    if mm.sum() < MIN_OBS:
        return None
    deltas, base, hi, lo = [], [], [], []
    for i in np.where(mm)[0]:
        s, f, fe = score.iloc[i], flow.iloc[i], fwd_ex.iloc[i]
        common = s.dropna().index.intersection(f.dropna().index).intersection(fe.dropna().index)
        if len(common) < MIN_XS:
            continue
        sv, fv = s[common], f[common]
        k = max(2, int(round(len(common) * sel_q)))
        strong = sv.sort_values(ascending=False).index[:k]     # 强势前 sel_q
        fs = fv[strong]
        fmed = fs.median()
        hv = fe[strong][fs >= fmed].dropna()
        lv = fe[strong][fs < fmed].dropna()
        bv = fe[strong].dropna()
        if len(hv) < 2 or len(lv) < 2:
            continue
        hi.append(hv.mean()); lo.append(lv.mean()); base.append(bv.mean())
        deltas.append(hv.mean() - lv.mean())
    if len(deltas) < MIN_OBS:
        return None
    d = np.array(deltas) * 100
    t_d, n = newey_west_t(d)
    t_hi, _ = newey_west_t(np.array(hi) * 100)
    t_base, _ = newey_west_t(np.array(base) * 100)
    return {
        "n": int(n),
        "strong_base_pct": round(float(np.mean(base) * 100), 3),
        "strong_base_t": round(float(t_base), 2) if np.isfinite(t_base) else None,
        "strong_highflow_pct": round(float(np.mean(hi) * 100), 3),
        "strong_highflow_t": round(float(t_hi), 2) if np.isfinite(t_hi) else None,
        "strong_lowflow_pct": round(float(np.mean(lo) * 100), 3),
        "delta_pct": round(float(d.mean()), 3),
        "delta_nw_t": round(float(t_d), 2) if np.isfinite(t_d) else None,
        "net_highflow_pct": round(float(np.mean(hi) * 100) - COST_RT, 3),
    }


def main():
    print("读取数据…")
    members, close, amount = load_all()
    L, A, F = build(members, close, amount)
    print(f"板块 {L.shape[1]} 个，{L.index[0].date()} ~ {L.index[-1].date()}")

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

    res = []
    print(f"\n{'H':>3}{'feature':<13}{'状态':<16}{'强势基准':>10}{'t':>7}"
          f"{'强+资金高':>11}{'强+资金低':>11}{'增量Δ':>9}{'Δ的t':>7}{'净额':>9}")
    print("-" * 106)
    for h in HORIZONS:
        fwd = np.expm1(lr.rolling(h).sum().shift(-h))
        fwd_ex = fwd.sub(fwd.mean(axis=1, skipna=True), axis=0)
        for fname, score in feats.items():
            for rname, m in regimes.items():
                r = incremental(score, F, fwd_ex, m)
                if not r:
                    continue
                r.update({"horizon": h, "feature": fname, "regime": rname})
                res.append(r)
                print(f"{h:>3}{fname:<13}{rname:<16}{r['strong_base_pct']:>9.3f}%"
                      f"{(r['strong_base_t'] or 0):>7.2f}"
                      f"{r['strong_highflow_pct']:>10.3f}%"
                      f"{r['strong_lowflow_pct']:>10.3f}%"
                      f"{r['delta_pct']:>8.3f}%{(r['delta_nw_t'] or 0):>7.2f}"
                      f"{r['net_highflow_pct']:>8.3f}%")

    os.makedirs(os.path.join(BASE, "results"), exist_ok=True)
    out = os.path.join(BASE, "results", "sector_flow_incr.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"cost_round_trip_pct": COST_RT, "rows": res}, f,
                  ensure_ascii=False, indent=1)
    print(f"\n已写入 {out}")


if __name__ == "__main__":
    main()
