# -*- coding: utf-8 -*-
"""板块复盘面板：基于本地历史日线，给出板块强弱、板块内分化、市场状态。

设计原则（来自 2015-2026 全样本实测，见 docs/sector_review_backtest.md）：
  1. 只呈现事实（距高点多少、涨跌多少、多少个新高），不给买卖建议。
  2. 必须同时给出市场状态。深跌市里"跌得少"的板块实测倾向补跌
     （20日超额 -0.804%，t=-2.34，但只有 5 个独立时段，证据强度有限）。
  3. 必须给出板块内分化度，用于区分"真普涨"和"少数股拉动"。

数据来源：
  data/history/<code>.parquet  个股日线（2015~）
  data/sector_members.json     板块成分（冻结快照）
  data/sector_index/<bk>.json  官方 BK 板块指数日线
"""
import json
import os
import sys
import threading

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, os.path.join(BASE_DIR, "modules"))
import data_paths  # noqa: E402

HIST_DIR = data_paths.history_dir() or os.path.join(BASE_DIR, "data", "history")
IDX_DIR = data_paths.index_dir() or os.path.join(BASE_DIR, "data", "sector_index")
MEMBERS_FILE = data_paths.members_file() or os.path.join(BASE_DIR, "data", "sector_members.json")
CACHE_DIR = os.path.join(BASE_DIR, "data", "panel_cache")

sys.path.insert(0, os.path.join(BASE_DIR, "modules"))
from price_clean import (HARD_LIMIT, RET_LIMIT, STRUCT_BARS,  # noqa: E402
                         clean_frame, load_clean, tail_return)

MIN_BARS = 60
LIQ_FLOOR = 5e7          # 可交易流动性下限：5000 万成交额
NEW_HIGH_TOL = 0.005     # 距高点 0.5% 以内算创新高

# 本模块自身与 App 同进程运行，路径按 BASE_DIR 解析
_STATS = {"truncated": 0, "dropped": 0, "unusable": 0, "cleaned": 0}

_lock = threading.Lock()
_mem = {}


def _ensure_dirs():
    os.makedirs(CACHE_DIR, exist_ok=True)


def load_members():
    """板块 -> 成分股代码列表。"""
    with open(MEMBERS_FILE, encoding="utf-8") as f:
        raw = json.load(f)
    out = {}
    for name, v in (raw.get("sectors") or {}).items():
        out[name] = {
            "bk": v.get("bk", ""),
            "codes": [m["code"] for m in v.get("members", []) if m.get("code")],
            "names": {m["code"]: m.get("name", "") for m in v.get("members", [])},
        }
    return out


def _read_stock(path):
    """读取并清洗个股日线。

    旧实现：任一日 |收益|>35% 就 return None，整只股票从板块里消失。
    实测（data/history 全量）锂矿概念丢 20.5% 成分、年化被低估 4.39pct；
    美的 000333、格力 000651 等高分红白马因早期负价被误杀，导致板块成分
    系统性偏向低分红高波动股。
    现改为两级清洗（见 price_clean.py）：成片损坏截断早期历史，孤立脏 bar
    置 NaN 打洞而非删行——删行会让前后两根相邻，凭空造出假跳空。
    """
    d, qc = load_clean(path, min_bars=MIN_BARS)
    if d is None:
        _STATS["unusable"] += 1
        return None
    if qc.get("truncated_bars"):
        _STATS["truncated"] += 1
    if qc.get("holes"):
        _STATS["dropped"] += 1
    _STATS["cleaned"] += 1
    cols = [c for c in ("date", "close", "high", "amount", "turnover", "pct_chg")
            if c in d.columns]
    return d[cols].copy()


def _stock_metrics(d, code, name):
    """单只股票的当前特征（只取最后一行，历史滚动量用尾部窗口算）。"""
    c = d["close"]
    amt = d["amount"] if "amount" in d else pd.Series(np.nan, index=d.index)
    n = len(c)

    def ret(k):
        # 不可写成 c.iloc[-1-k]：若窗口内含洞，该位置可能是 NaN 而
        # 更早处仍有价，等价于跨洞拼接。tail_return 对含洞窗口返回 NaN。
        return tail_return(d, k)

    hi60 = float(c.iloc[-60:].max()) if n >= 60 else float(c.max())
    hi250 = float(c.iloc[-250:].max()) if n >= 60 else float(c.max())
    ma20 = float(c.iloc[-20:].mean()) if n >= 20 else np.nan
    ma60 = float(c.iloc[-60:].mean()) if n >= 60 else np.nan
    amt20 = float(amt.iloc[-20:].mean()) if n >= 20 else np.nan

    return {
        "code": code,
        "name": name,
        "date": d["date"].iloc[-1].strftime("%Y-%m-%d"),
        "close": round(float(c.iloc[-1]), 3),
        "ret5": round(ret(5) * 100, 2) if np.isfinite(ret(5)) else None,
        "ret20": round(ret(20) * 100, 2) if np.isfinite(ret(20)) else None,
        "ret60": round(ret(60) * 100, 2) if np.isfinite(ret(60)) else None,
        "dist_high60": round((float(c.iloc[-1]) / hi60 - 1) * 100, 2),
        "dist_high250": round((float(c.iloc[-1]) / hi250 - 1) * 100, 2),
        "above_ma20": bool(np.isfinite(ma20) and c.iloc[-1] > ma20),
        "above_ma60": bool(np.isfinite(ma60) and c.iloc[-1] > ma60),
        "amount": float(amt.iloc[-1]) if np.isfinite(amt.iloc[-1]) else 0.0,
        "amt20": amt20 if np.isfinite(amt20) else 0.0,
        "vol_ratio": round(float(amt.iloc[-1] / amt20), 2) if amt20 and np.isfinite(amt.iloc[-1]) else None,
        "turnover": round(float(d["turnover"].iloc[-1]), 2) if "turnover" in d else None,
        "bars": n,
    }


def _sector_index(bk):
    """官方板块指数日线；缺失返回 None。"""
    path = os.path.join(IDX_DIR, f"{bk}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            kl = json.load(f)
    except Exception:
        return None
    rows = []
    for line in kl:
        p = line.split(",")
        if len(p) < 7:
            continue
        try:
            rows.append((p[0], float(p[2]), float(p[6])))
        except (ValueError, IndexError):
            continue
    if len(rows) < MIN_BARS:
        return None
    d = pd.DataFrame(rows, columns=["date", "close", "amount"])
    d["date"] = pd.to_datetime(d["date"])
    d = d.sort_values("date").reset_index(drop=True)
    # 官方指数同样可能有复权脏 bar，套用同一套清洗
    d, _ = clean_frame(d, min_bars=MIN_BARS)
    return d if d is not None else None


def _index_metrics(d):
    c = d["close"]
    n = len(c)

    def ret(k):
        # 见 _stock_metrics.ret：含洞窗口直接返回 NaN，不跨洞拼接
        return tail_return(d, k)

    hi60 = float(c.iloc[-60:].max())
    hi250 = float(c.iloc[-250:].max()) if n >= 250 else float(c.max())
    ma20 = float(c.iloc[-20:].mean())
    ma60 = float(c.iloc[-60:].mean())
    amt20 = float(d["amount"].iloc[-20:].mean())

    return {
        "date": d["date"].iloc[-1].strftime("%Y-%m-%d"),
        "close": round(float(c.iloc[-1]), 2),
        "ret5": round(ret(5) * 100, 2) if np.isfinite(ret(5)) else None,
        "ret20": round(ret(20) * 100, 2) if np.isfinite(ret(20)) else None,
        "ret60": round(ret(60) * 100, 2) if np.isfinite(ret(60)) else None,
        "dist_high60": round((float(c.iloc[-1]) / hi60 - 1) * 100, 2),
        "dist_high250": round((float(c.iloc[-1]) / hi250 - 1) * 100, 2),
        "above_ma20": bool(c.iloc[-1] > ma20),
        "above_ma60": bool(c.iloc[-1] > ma60),
        "vol_ratio": round(float(d["amount"].iloc[-1] / amt20), 2) if amt20 else None,
        "bars": n,
    }


def _fund_flow_last():
    """板块资金流最新值：近5日均额 / 近20日均额 - 1（%）。

    与 daily_recommend.sector_fund_flow 同一口径（实测两边差值恒为 0：
    官方板块指数数据干净，clean_frame 不触发打洞）。样本不足 300 行的
    板块（光纤、电网、固态电池）自动排除，不返回伪造值。
    """
    F = _fund_flow_series()
    return F.iloc[-1].to_dict() if not F.empty else {}


def _fund_flow_series():
    out = {}
    for name, info in load_members().items():
        d = _sector_index(info["bk"])
        if d is None or len(d) < 300:
            # 与 daily_recommend 同门槛：短历史板块（光纤154行、电网169行、
            # 固态电池0字节）不参与，避免用薄样本算出的资金值误导
            continue
        out[name] = d.set_index("date")["amount"]
    if not out:
        return pd.DataFrame()
    A = pd.DataFrame(out).sort_index()
    m5 = A.rolling(5, min_periods=3).mean()
    m20 = A.rolling(20, min_periods=10).mean()
    return (m5 / m20 - 1) * 100


def _percentile(sorted_vals, v):
    """v 在 sorted_vals 中的分位（0~100，越大越强）。"""
    if not len(sorted_vals):
        return None
    import bisect
    pos = bisect.bisect_left(sorted_vals, v)
    return round(pos / len(sorted_vals) * 100, 0)


# 状态 → 动量信号可信度。来自 2748 天 × 23 板块实测（Newey-West 调整）：
#   deep_drawdown  所有信号失效，low_vol 甚至反向（t=-2.11）
#   drawdown       信号微弱，t 值普遍 <1.2
#   shallow        唯一稳健区间，dist_high60 前 25% 超额 +0.446%（t=3.09）
#   high           dd>=0 仅 298 天、122 个连续时段、正时段 0/0，
#                  样本高度重叠，不构成独立样本，无法验证
REGIME_MOM_RELIABILITY = {
    "deep_drawdown": {"usable": False, "note": "深跌区动量失效，低波动反而跑输，不建议据强度选板块"},
    "drawdown":      {"usable": False, "note": "中度回撤区信号微弱（t 值普遍<1.2），仅供参考"},
    "shallow":       {"usable": True,  "note": "浅回撤区动量最稳健，最强板块倾向继续走强"},
    "high":          {"usable": None,  "note": "样本不足无法验证（仅122个高度重叠时段），勿将回测结论套用"},
}


def _market_state(idx_series):
    """用板块官方指数的等权合成判断市场状态。"""
    frames = []
    for name, d in idx_series.items():
        if d is None or len(d) <= 120:
            continue
        c, _ = clean_frame(d, min_bars=120)
        if c is None:
            continue
        frames.append(c.set_index("date")["close"].pct_change(fill_method=None).rename(name))
    if not frames:
        return None
    rets = pd.concat(frames, axis=1)
    # 清洗已在上方完成；这里只防个别指数残留越界值污染合成状态
    rets = rets.where(rets.abs() <= HARD_LIMIT)
    ew = rets.mean(axis=1, skipna=True).dropna()
    if len(ew) < 250:
        return None
    lvl = (1 + ew).cumprod()
    hi250 = lvl.rolling(250, min_periods=120).max()
    dd = float(lvl.iloc[-1] / hi250.iloc[-1] - 1)
    ma20 = float(lvl.iloc[-20:].mean())
    ma60 = float(lvl.iloc[-60:].mean())

    # 回撤本身也是滚动分位指标：给出近 3 年该回撤深度的分位，
    # 区分"罕见深跌"和"常态震荡"
    roll_dd = (lvl / hi250 - 1).tail(750)
    pct = round(float((roll_dd <= dd).mean() * 100), 1) if len(roll_dd) > 120 else None

    regime = ("deep_drawdown" if dd < -0.20 else
              "drawdown" if dd < -0.10 else
              "shallow" if dd < 0 else "high")
    rel = REGIME_MOM_RELIABILITY[regime]
    return {
        "as_of": ew.index[-1].strftime("%Y-%m-%d"),
        "dd_from_250d_high": round(dd * 100, 2),
        "dd_percentile_3y": pct,
        "above_ma20": bool(lvl.iloc[-1] > ma20),
        "above_ma60": bool(lvl.iloc[-1] > ma60),
        "ret20": round(float(lvl.iloc[-1] / lvl.iloc[-21] - 1) * 100, 2),
        "regime": regime,
        "mom_reliability": rel["usable"],
        "mom_note": rel["note"],
        "sector_count": len(frames),
    }


def build_panel(force=False):
    """构建完整面板；带内存 + 磁盘缓存。"""
    _ensure_dirs()
    with _lock:
        if not force and "panel" in _mem:
            return _mem["panel"]

    stamp = os.path.join(CACHE_DIR, "panel.json")
    if not force and os.path.exists(stamp):
        try:
            with open(stamp, encoding="utf-8") as f:
                panel = json.load(f)
            with _lock:
                _mem["panel"] = panel
            return panel
        except Exception:
            pass

    _STATS.update({"truncated": 0, "dropped": 0, "unusable": 0, "cleaned": 0})
    members = load_members()
    stocks = {}
    for fn in os.listdir(HIST_DIR) if os.path.isdir(HIST_DIR) else []:
        if not fn.endswith(".parquet"):
            continue
        code = fn[:-8]
        d = _read_stock(os.path.join(HIST_DIR, fn))
        if d is not None:
            stocks[code] = d

    idx_series, idx_metrics = {}, {}
    for name, info in members.items():
        d = _sector_index(info["bk"])
        idx_series[name] = d
        if d is not None:
            idx_metrics[name] = _index_metrics(d)

    sectors = []
    for name, info in members.items():
        rows = []
        for code in info["codes"]:
            d = stocks.get(code)
            if d is None:
                continue
            rows.append(_stock_metrics(d, code, info["names"].get(code, "")))
        if not rows:
            continue
        df = pd.DataFrame(rows)
        tradable = df[df["amount"] >= LIQ_FLOOR]

        # 分化度指标
        n = len(df)
        new_high = int((df["dist_high60"] > -NEW_HIGH_TOL * 100).sum())
        deep_low = int((df["dist_high60"] < -40).sum())
        up20 = int((df["ret20"] > 0).sum())
        med_dh = float(df["dist_high60"].median())
        med_r20 = float(df["ret20"].median()) if df["ret20"].notna().any() else None

        idxm = idx_metrics.get(name)
        # 板块强度优先用官方指数；没有则用成分股中位
        strength = idxm if idxm else {
            "date": df["date"].max(),
            "dist_high60": round(med_dh, 2),
            "ret20": round(med_r20, 2) if med_r20 is not None else None,
            "dist_high250": None, "ret5": None, "ret60": None,
            "above_ma20": None, "above_ma60": None, "vol_ratio": None,
        }

        # 分化判定
        if n and up20 / n >= 0.6:
            breadth = "普涨"
        elif n and up20 / n <= 0.2:
            breadth = "普跌"
        elif new_high > 0 and up20 / n < 0.4:
            breadth = "少数拉动"
        else:
            breadth = "分化"

        sectors.append({
            "name": name,
            "bk": info["bk"],
            "has_index": idxm is not None,
            "fund": None,
            "strength": strength,
            "breadth": {
                "label": breadth,
                "count": n,
                "tradable": int(len(tradable)),
                "new_high": new_high,
                "deep_low": deep_low,
                "up20_ratio": round(up20 / n * 100, 1) if n else None,
                "med_dist_high60": round(med_dh, 2),
                "med_ret20": round(med_r20, 2) if med_r20 is not None else None,
                "med_ret60": round(float(df["ret60"].median()), 2) if df["ret60"].notna().any() else None,
            },
            "stocks": rows,
            "_tradable_codes": set(tradable["code"].tolist()),
        })

    # 板块排名（按官方指数距250日高点；无指数的排最后）
    sectors.sort(key=lambda s: (
        s["strength"].get("dist_high250") is None,
        -(s["strength"].get("dist_high250") or -999),
    ))
    for i, s in enumerate(sectors, 1):
        s["rank"] = i

    # 资金流：纯观察项，只给数值，不划分强弱档。
    # 【为什么不分档】板块层的「强势+资金高」已被配对差分否证
    # （10日 Δ-0.079% t=-0.59，20日 Δ-0.213% t=-0.97），
    # 在板块页标强弱等于展示一个已知无效的划分。个股超跌池内的
    # 分档才有证据（+0.626% t=2.56），那套标签留在个股页。
    flow = _fund_flow_last()
    fv = [flow[s["name"]] for s in sectors
          if s["name"] in flow and np.isfinite(flow[s["name"]])]
    fmed = float(np.median(fv)) if fv else None
    for s in sectors:
        v = flow.get(s["name"])
        s["fund"] = round(float(v), 1) if v is not None and np.isfinite(v) else None
    # 全市场口径提示：正值板块数 / 有效板块数
    fpos = sum(1 for x in fv if x > 0)

    panel = {
        "generated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "as_of": max([s["strength"].get("date") or "" for s in sectors] or [""]),
        "market": _market_state(idx_series),
        "sectors": sectors,
        "coverage": {
            "stocks_with_history": len(stocks),
            "sectors": len(sectors),
            "sectors_with_index": sum(1 for s in sectors if s["has_index"]),
            "liq_floor": LIQ_FLOOR,
            "truncated": _STATS["truncated"],
            "isolated_fixed": _STATS["dropped"],
            "unusable": _STATS["unusable"],
            "ret_limit": RET_LIMIT,
            "struct_bars": STRUCT_BARS,
        },
        "method": {
            "clean": ("脏 bar 判定 |日收益|>%.1f%%（A股涨跌幅上限20%%+噪音带）；"
                      "成片损坏（簇>=%d根且结束于历史前60%%）截断早期历史；"
                      "其余脏 bar 置 NaN 打洞，不删行" % (RET_LIMIT * 100, STRUCT_BARS)),
            "holes": "洞与洞后一根的收益均为 NaN，区间收益遇洞返回 NaN，不跨洞拼接",
            "note": "旧版按整只股票丢弃 125/4370 只，使锂矿概念年化低估 4.39pct",
        },
        "fund_factor": {
            "median": round(fmed, 1) if fmed is not None else None,
            "n": len(fv),
            "positive_n": fpos,
            "no_tag": "板块层不分强弱档（该划分已在配对差分中被否证），只给数值供参考",
            "field": "fund = 近5日均额 / 近20日均额 - 1（%）",
            "is_proxy": ("成交额代理变量，非真实主力资金流；无龙虎榜/北向/大单数据"),
            "evidence": {
                "scope": "深跌区 · 个股超跌池内 · 按所属板块资金配对差分",
                "delta_10d_pct": 0.626, "t_10d": 2.56,
                "delta_20d_pct": 1.435, "t_20d": 2.77,
                "independent_periods": 27,
                "split_robust": "30/50/70 分位切分均为正，前/后半样本同向，20日 4/4 独立时段为正",
            },
            "at_sector_level": (
                "板块层「强势+资金高」被否证：配对差分后全持有期不显著且多为负"
                "（10日 Δ-0.079% t=-0.59；20日 Δ-0.213% t=-0.97）。"
                "故本列只作观察，不参与板块排序，也不作为买入信号。"
            ),
        },
    }

    # set 不能序列化，输出前移除
    light = json.loads(json.dumps(panel, ensure_ascii=False, default=str))
    for s in light["sectors"]:
        s.pop("_tradable_codes", None)
    try:
        with open(stamp, "w", encoding="utf-8") as f:
            json.dump(light, f, ensure_ascii=False)
    except Exception:
        pass

    with _lock:
        _mem["panel"] = light
    return light


def get_panel(force=False):
    return build_panel(force=force)


def get_sector_stocks(name, limit=None, require_liquidity=True, sort="dist_high60"):
    """某板块成分股明细，带板块内分位。"""
    panel = get_panel()
    sec = next((s for s in panel["sectors"] if s["name"] == name), None)
    if sec is None:
        return None
    rows = [dict(r) for r in sec["stocks"]]
    if require_liquidity:
        rows = [r for r in rows if (r.get("amount") or 0) >= LIQ_FLOOR]

    vals = sorted([r["dist_high60"] for r in rows if r.get("dist_high60") is not None])
    for r in rows:
        r["pct_in_sector"] = _percentile(vals, r["dist_high60"]) if r.get("dist_high60") is not None else None

    key = sort if sort in ("dist_high60", "ret20", "ret60", "amount", "vol_ratio") else "dist_high60"
    rows.sort(key=lambda r: (-(r.get(key) if r.get(key) is not None else -1e9)))
    if limit:
        rows = rows[:int(limit)]
    return {"sector": sec["name"], "as_of": sec["strength"].get("date"),
            "breadth": sec["breadth"], "count": len(rows), "stocks": rows}
