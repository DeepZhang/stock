#!/usr/bin/env python3
"""A-share historical pattern analysis (v1, rule-based backtests with out-of-sample split).
Reads stock-analysis/history/*.parquet, computes features, and backtests rules that the
stock site's screener / market overview can use. Train: 2015-2023, Test: 2024-2026-11.
Outputs: stock-analysis/pattern_report.md and pattern_results.json
"""
import glob
import json
import os

import numpy as np
import pandas as pd

BASE = "/data/home/stock/stock-analysis"
HIST_DIR = "/data/home/stock/data/history"
OUT_MD = os.path.join(BASE, "pattern_report.md")
OUT_JSON = os.path.join(BASE, "pattern_results.json")
SPLIT = "2024-01-01"


def load_panel():
    files = sorted(glob.glob(os.path.join(HIST_DIR, "*.parquet")))
    print(f"loading {len(files)} files...", flush=True)
    frames = []
    for f in files:
        code = os.path.basename(f)[:-8]
        df = pd.read_parquet(f)
        df["code"] = code
        frames.append(df)
    panel = pd.concat(frames, ignore_index=True)
    panel["date"] = pd.to_datetime(panel["date"])
    panel = panel.sort_values(["code", "date"]).reset_index(drop=True)
    print(f"panel: {len(panel)} rows, {panel['code'].nunique()} stocks, "
          f"{panel['date'].min().date()}..{panel['date'].max().date()}", flush=True)
    return panel


def add_features(panel):
    g = panel.groupby("code", sort=False)
    c = panel["close"]
    panel["ret_1"] = g["close"].pct_change(1)
    panel["ret_5"] = g["close"].pct_change(5)
    panel["ret_20"] = g["close"].pct_change(20)
    panel["vol_ma20"] = g["volume"].transform(lambda s: s.rolling(20).mean().shift(1))
    panel["vol_ratio"] = panel["volume"] / panel["vol_ma20"]
    panel["ma20"] = g["close"].transform(lambda s: s.rolling(20).mean())
    panel["above_ma20"] = (c - panel["ma20"]) / panel["ma20"] * 100
    panel["high30"] = g["high"].transform(lambda s: s.rolling(30).max())
    panel["off_high30"] = (panel["high30"] - c) / panel["high30"] * 100
    ema12 = g["close"].transform(lambda s: s.ewm(span=12, adjust=False).mean())
    ema26 = g["close"].transform(lambda s: s.ewm(span=26, adjust=False).mean())
    dif = ema12 - ema26
    dea = dif.groupby(panel["code"]).transform(lambda s: s.ewm(span=9, adjust=False).mean())
    panel["macd_hist"] = (dif - dea) * 2
    panel["macd_bull"] = (dif > dea).astype(int)
    # RSI14 (Wilder)
    delta = g["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.groupby(panel["code"]).transform(
        lambda s: s.ewm(alpha=1 / 14, adjust=False).mean())
    avg_loss = loss.groupby(panel["code"]).transform(
        lambda s: s.ewm(alpha=1 / 14, adjust=False).mean())
    rs = avg_gain / avg_loss.replace(0, np.nan)
    panel["rsi14"] = 100 - 100 / (1 + rs)
    # limit-up proxy: ChiNext/STAR (30/68) 20% limit, else 10%
    panel["is_limit_up"] = np.where(
        panel["code"].str.startswith(("30", "68")),
        panel["pct_chg"] >= 19.0, panel["pct_chg"] >= 9.5)
    # forward returns (no lookahead: computed from future closes)
    panel["fwd_1"] = g["close"].shift(-1) / c - 1
    panel["fwd_5"] = g["close"].shift(-5) / c - 1
    panel["fwd_20"] = g["close"].shift(-20) / c - 1
    # 3-day volume surge (screener's actual condition)
    vr = panel["vol_ratio"] >= 1.8
    panel["vol_surge_3d"] = (vr & vr.groupby(panel["code"]).shift(1)
                             & vr.groupby(panel["code"]).shift(2))
    return panel


def evaluate(df, mask, label, horizons=("fwd_1", "fwd_5", "fwd_20")):
    out = {"rule": label}
    for period_name, sub in (("train", df[df["date"] < SPLIT]),
                             ("test", df[df["date"] >= SPLIT])):
        sel = sub[mask(sub)]
        base = sub
        row = {}
        for h in horizons:
            s = sel[h].dropna()
            b = base[h].dropna()
            row[h] = {
                "n": int(len(s)),
                "mean_pct": round(float(s.mean() * 100), 3) if len(s) else None,
                "win_rate": round(float((s > 0).mean() * 100), 2) if len(s) else None,
                "base_mean_pct": round(float(b.mean() * 100), 3) if len(b) else None,
            }
        out[period_name] = row
    return out


def main():
    panel = load_panel()
    panel = add_features(panel)
    # drop inf rows (e.g. close=0 placeholder bars -> inf fwd returns / vol ratios)
    panel = panel.replace([np.inf, -np.inf], np.nan)
    # require enough history + valid features
    df = panel.dropna(subset=["ret_20", "vol_ratio", "ma20", "macd_hist", "rsi14",
                              "fwd_1", "fwd_5", "fwd_20"]).copy()
    print(f"analysis rows: {len(df)}", flush=True)

    rules = [
        ("screener_now: vol>=1.8 & MACD bull & price>MA20 & within 5% of 30d high",
         lambda d: (d["vol_ratio"] >= 1.8) & (d["macd_hist"] > 0)
         & (d["close"] > d["ma20"]) & (d["off_high30"] <= 5)),
        ("screener_3d: 3-day vol surge + MACD bull + price>MA20 + near 30d high",
         lambda d: d["vol_surge_3d"] & (d["macd_hist"] > 0)
         & (d["close"] > d["ma20"]) & (d["off_high30"] <= 5)),
        ("vol_surge_only: vol_ratio>=1.8",
         lambda d: d["vol_ratio"] >= 1.8),
        ("vol_surge_strong: vol_ratio>=2.5",
         lambda d: d["vol_ratio"] >= 2.5),
        ("momentum: past 20d return top (ret_20>=10%)",
         lambda d: d["ret_20"] >= 0.10),
        ("reversal: past 5d big drop (ret_5<=-8%)",
         lambda d: d["ret_5"] <= -0.08),
        ("limit_up: limit-up today",
         lambda d: d["is_limit_up"]),
        ("rsi_overbought: RSI14>=80",
         lambda d: d["rsi14"] >= 80),
        ("rsi_oversold: RSI14<=20",
         lambda d: d["rsi14"] <= 20),
        ("above_ma20_far: price 5%+ above MA20",
         lambda d: d["above_ma20"] >= 5),
        ("below_ma20_far: price 5%+ below MA20",
         lambda d: d["above_ma20"] <= -5),
    ]
    results = [evaluate(df, mask, label) for label, mask in rules]

    # momentum / reversal quintile tables (test period only)
    def quintile_table(col, label):
        test = df[df["date"] >= SPLIT].copy()
        test["q"] = test.groupby("date")[col].transform(
            lambda s: pd.qcut(s, 5, labels=False, duplicates="drop"))
        rows = []
        for q in sorted(test["q"].dropna().unique()):
            s = test[test["q"] == q]
            rows.append({
                "quintile": int(q),
                "n": int(len(s)),
                "fwd_1_mean_pct": round(float(s["fwd_1"].mean() * 100), 3),
                "fwd_5_mean_pct": round(float(s["fwd_5"].mean() * 100), 3),
                "fwd_20_mean_pct": round(float(s["fwd_20"].mean() * 100), 3),
            })
        return {"factor": label, "test_quintiles": rows}

    quintiles = [
        quintile_table("ret_20", "past 20d return"),
        quintile_table("ret_5", "past 5d return"),
        quintile_table("vol_ratio", "volume ratio"),
        quintile_table("rsi14", "RSI14"),
    ]

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"rules": results, "quintiles": quintiles}, f, ensure_ascii=False, indent=2)

    lines = ["# A股历史规律分析 v1", "",
             f"样本：{df['code'].nunique()} 只，{df['date'].min().date()}..{df['date'].max().date()}，"
             f"共 {len(df)} 行。训练期 < {SPLIT}，检验期 >= {SPLIT}。",
             "口径：fwd_1/5/20 为未来 1/5/20 个交易日收益；base_mean 为同期全市场均值。",
             ""]
    lines.append("## 规则回测（均值% / 胜率% / 样本数）")
    for r in results:
        lines.append(f"### {r['rule']}")
        for per in ("train", "test"):
            parts = []
            for h in ("fwd_1", "fwd_5", "fwd_20"):
                v = r[per][h]
                parts.append(f"{h}: {v['mean_pct']}%（胜率{v['win_rate']}%/n={v['n']}，基准{v['base_mean_pct']}%）")
            lines.append(f"- {per}：{' | '.join(parts)}")
        lines.append("")
    lines.append("## 因子五分位（检验期）")
    for q in quintiles:
        lines.append(f"### {q['factor']}")
        for row in q["test_quintiles"]:
            lines.append(f"- Q{row['quintile']}: n={row['n']} fwd_1={row['fwd_1_mean_pct']}% "
                         f"fwd_5={row['fwd_5_mean_pct']}% fwd_20={row['fwd_20_mean_pct']}%")
        lines.append("")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"wrote {OUT_MD} and {OUT_JSON}", flush=True)


if __name__ == "__main__":
    main()
