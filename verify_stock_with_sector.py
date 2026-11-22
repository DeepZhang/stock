# -*- coding: utf-8 -*-
"""板块强度能否提升个股筛选的准确率？

前面的检验说明：板块层信号太薄、资金流无形量。但用户真正的目标是
"更好的筛选方式、准确率提升"。本脚本做**最有决策价值的一问**：

  在个股超跌+放量（项目已验证的最强个股信号）之上，
  叠加"所属板块强势"过滤，能否提高准确率？

对照组：
  A. 个股信号单独（超跌前10% → 按量比排序取20）
  B. 个股信号 ∩ 所属板块强（板块 dist_high60 前25%）
  C. 个股信号 ∩ 所属板块弱（后25%）
  D. 个股信号 ∩ 板块资金高
分市场状态给出，因为既有结论明确"信号方向随状态翻转"。

用法：python3 verify_stock_with_sector.py
输出：results/stock_with_sector.json
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
POOL_Q = 0.10       # 超跌前 10% 成池
TOPN = 20           # 取前 20 只


def main():
    print("读取数据…")
    members, close, amount = load_all()
    L, A, F = build(members, close, amount)
    print(f"板块 {L.shape[1]}，股票 {close.shape[1]}，"
          f"{close.index[0].date()} ~ {close.index[-1].date()}")

    ret = close.pct_change(fill_method=None)
    ok = (close >= PRICE_FLOOR) & (amount >= AMT_FLOOR) & ret.abs().le(RET_LIMIT)
    ew_bench = ret.where(ok).mean(axis=1, skipna=True)

    # 个股信号
    dist60 = close / close.rolling(60, min_periods=40).max() - 1     # 超跌程度
    volr = amount / amount.rolling(20, min_periods=10).mean()        # 量比

    # 所属板块强度 / 资金（多板块取最强）
    sec_str = L / L.rolling(60, min_periods=40).max() - 1
    M = pd.DataFrame(False, index=close.columns, columns=sec_str.columns)
    for name, info in members.items():
        if name not in sec_str.columns:
            continue
        cols = [c for c in info["codes"] if c in close.columns]
        if cols:
            M.loc[cols, name] = True
    stock_str = pd.DataFrame(np.nan, index=close.index, columns=close.columns)
    stock_flow = pd.DataFrame(np.nan, index=close.index, columns=close.columns)
    for name in sec_str.columns:
        sel = M[name].values
        if not sel.any():
            continue
        colv = sec_str[name].values[:, None]
        stock_str.loc[:, sel] = np.fmax(stock_str.loc[:, sel].values,
                                        np.broadcast_to(colv, (len(close), int(sel.sum()))))
        fv = F[name].values[:, None] if name in F.columns else np.full((len(close), 1), np.nan)
        stock_flow.loc[:, sel] = np.fmax(stock_flow.loc[:, sel].values,
                                         np.broadcast_to(fv, (len(close), int(sel.sum()))))

    # 市场状态
    ew = L.pct_change(fill_method=None).mean(axis=1, skipna=True)
    mkt = (1 + ew.fillna(0)).cumprod()
    dd = mkt / mkt.rolling(250, min_periods=120).max() - 1
    regimes = {
        "深跌(dd<-20%)": (dd < -0.20).values,
        "中度(-20~-10%)": ((dd >= -0.20) & (dd < -0.10)).values,
        "浅回撤(-10~0%)": ((dd >= -0.10) & (dd < 0)).values,
        "高位(dd>=0)": (dd >= 0).values,
    }

    res = {"cost_round_trip_pct": COST_RT, "rows": []}
    for h in HORIZONS:
        fwd = np.expm1(np.log1p(ret.where(ok)).rolling(h).sum().shift(-h))
        bench = np.expm1(np.log1p(ew_bench).rolling(h).sum().shift(-h))
        fwd_ex = fwd.sub(bench, axis=0)
        for rname, rmask in regimes.items():
            mm = np.asarray(rmask, dtype=bool) & fwd_ex.notna().any(axis=1).values
            if mm.sum() < MIN_OBS:
                continue
            variants = {"A 个股信号单独": "none", "B ∩ 板块强": "strong",
                        "C ∩ 板块弱": "weak", "D ∩ 板块资金高": "flowhi"}
            daily = {k: [] for k in variants}
            for i in np.where(mm)[0]:
                d = dist60.iloc[i]
                v = volr.iloc[i]
                f = fwd_ex.iloc[i]
                common = d.dropna().index.intersection(v.dropna().index).intersection(
                    f.dropna().index).intersection(ok.iloc[i][ok.iloc[i]].index)
                if len(common) < 200:
                    continue
                dv = d[common]
                k = max(5, int(len(common) * POOL_Q))
                pool = dv.sort_values().index[:k]          # 最超跌
                s = stock_str.iloc[i][pool]
                fl = stock_flow.iloc[i][pool]
                for key, mode in variants.items():
                    sel = pool
                    if mode == "strong" and s.notna().sum() >= 5:
                        thr = stock_str.iloc[i][common].quantile(0.75)
                        sel = s[s >= thr].index
                    elif mode == "weak" and s.notna().sum() >= 5:
                        thr = stock_str.iloc[i][common].quantile(0.25)
                        sel = s[s <= thr].index
                    elif mode == "flowhi" and fl.notna().sum() >= 5:
                        thr = stock_flow.iloc[i][common].quantile(0.75)
                        sel = fl[fl >= thr].index
                    if len(sel) < 5:
                        continue
                    vr = v[sel].dropna().sort_values(ascending=False)
                    if len(vr) < 5:
                        continue
                    pick = vr.index[:TOPN]
                    val = f[pick].dropna()
                    if len(val) >= 3:
                        daily[key].append(val.mean())
            print(f"\n=== {h}日 | {rname}（超额%，n=组合天数，成本 {COST_RT}%）")
            print(f"{'变体':<18}{'毛超额':>10}{'NW t':>8}{'净超额':>10}{'n':>6}{'独立时段':>9}")
            for key in variants:
                v = daily[key]
                if len(v) < MIN_OBS:
                    continue
                arr = np.array(v) * 100
                t, n = newey_west_t(arr)
                print(f"{key:<18}{arr.mean():>9.3f}%"
                      f"{(t if np.isfinite(t) else 0):>8.2f}"
                      f"{arr.mean()-COST_RT:>9.3f}%{n:>6}")
                res["rows"].append({"horizon": h, "regime": rname, "variant": key,
                                    "excess_pct": round(float(arr.mean()), 3),
                                    "nw_t": round(float(t), 2) if np.isfinite(t) else None,
                                    "n": int(n), "net_pct": round(float(arr.mean()) - COST_RT, 3)})

    os.makedirs(os.path.join(BASE, "results"), exist_ok=True)
    out = os.path.join(BASE, "results", "stock_with_sector.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(f"\n已写入 {out}")


if __name__ == "__main__":
    main()
