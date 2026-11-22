# -*- coding: utf-8 -*-
"""每日复盘推荐：把「哪些板块/个股能买、哪些要规避」算成可直接看的结论。

来自 2016-2026 全样本实测（2683 只 A 股，含流动性/价格/涨停过滤），
详见 docs/daily_recommend_backtest.md。核心结论：

  1. 市场状态决定打法，不是同一套逻辑跑到底：
       深跌区  个股「超跌反弹」有效。20日跌幅前10% 组合，未来10日 +2.97%
               （t=1.94），叠加量比>1.0 后 +3.20%（t=2.06，胜率55.2%）；
               绝对收益 +3.39%，剔除低价股与低流动性后仍有 +3.39%。
       中度回撤 胜率 49.3% ≈ 抛硬币，不给个股推荐。
       浅回撤/高位 无稳定信号，不给个股推荐。
  2. 「少跌就是强」被否定：深跌区距60日高最近的一档，10日超额 -1.90%
     （t=-4.84），是全部测试里最强的反向关系。抗跌票在深跌区系统性跑输。
  3. 板块强度对超跌个股没有加成（强板块 vs 弱板块差 +0.001%，t=0.27）。
     板块只用于「观察 + 规避提示」，不参与个股打分。
  4. 板块强度×资金的四象限绝对收益全部不显著（最高 t=2.24），
     因此不按板块择时，只标注资金与强度方向供人工参考。

因此本模块的推荐逻辑是单层的：先用市场状态决定「今天该不该出手」，
再在可交易池里按超跌 + 放量排序。板块强弱单独成栏，明确标注仅供参考。
"""
import json
import os
import threading

import numpy as np
import pandas as pd

import data_paths

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HIST_DIR = data_paths.history_dir() or os.path.join(BASE_DIR, "data", "history")
IDX_DIR = data_paths.index_dir() or os.path.join(BASE_DIR, "data", "sector_index")
MEMBERS_FILE = data_paths.members_file() or os.path.join(BASE_DIR, "data", "sector_members.json")
CACHE_DIR = os.path.join(BASE_DIR, "data", "panel_cache")

MIN_PRICE = 5.0          # 股价下限，剔除低价壳票
LIQ_FLOOR = 5e7          # 单日成交额下限
LIQ_AVG_FLOOR = 1e8      # 20 日均成交额下限（实测加这层后收益仍稳定）
LIMIT_UP_GAP = 0.093     # T+1 开盘涨幅超过此值视为买不到（涨停/一字板）
HOLD_DAYS = 10           # 回测最优持有期
STOP_LOSS = -0.08        # 提示用的止损位

# 只加载每只股票的尾部若干根 bar。最长窗口是市场净值的 750 根滚动分位，
# 它需要 250 根回撤预热 + 750 根样本 = 999 根才能填满，取 1050 留余量。
# 实测 1050 根与全历史 2887 根的所有输出完全一致（回撤分位亦对齐）；
# 全历史在 1.8G 内存的线上会被 OOM 杀掉。
TAIL_BARS = 1050

# 状态 -> 打法。usable=False 时不给个股推荐，只给规避提示。
REGIME_PLAYBOOK = {
    "deep_drawdown": {
        "usable": True,
        "method": "超跌反弹",
        "label": "深跌区",
        "note": "历史同类时段（337 个深跌交易日），20日跌幅前10%池内按量能排序取前20，"
                "未来10日均值 +3.16%（t=2.10，胜率53.9%）。这是反弹博弈，不是趋势跟随。",
        "hold": HOLD_DAYS,
    },
    "drawdown": {
        "usable": False,
        "method": "空仓观望",
        "label": "中度回撤区",
        "note": "历史上该状态选强选弱胜率都在 49% 左右，等同抛硬币。"
                "不给个股推荐，等状态明确后再动手。",
        "hold": None,
    },
    "shallow": {
        "usable": False,
        "method": "观望",
        "label": "浅回撤区",
        "note": "该状态下超跌与强势信号都不显著（|t|<1.4），不构成可执行优势。",
        "hold": None,
    },
    "high": {
        "usable": False,
        "method": "不追高",
        "label": "高位区",
        "note": "高位区超跌股无稳定超额（t<0.6），追高风险大于收益。",
        "hold": None,
    },
}

_lock = threading.Lock()
_cache = {}


# ---------------------------------------------------------------- 数据装载

def _read_all(limit=None, tail_bars=None, with_open=False):
    """读取全部个股日线，返回 close/amount（可选 open）三张对齐矩阵。

    tail_bars 只保留每只股票最后 N 根 bar。全历史约 2887 根，而本模块最长
    的窗口是市场净值的 750 根滚动分位（250 根回撤窗口 + 分位样本），
    再多读纯属浪费内存。线上 1.8G 内存加载全历史会被 OOM 杀掉，
    限制尾部后内存稳定在三分之一以内，指标口径不变。

    with_open 默认 False：open 矩阵原本每次都会构建却从未被读取，
    白占约三分之一的峰值内存，改为按需构建。
    """
    import sys
    sys.path.insert(0, os.path.join(BASE_DIR, "modules"))
    from price_clean import load_clean

    files = sorted(f for f in os.listdir(HIST_DIR) if f.endswith(".parquet"))
    if limit:
        files = files[:limit]
    close, amount, opn = {}, {}, {}
    for fn in files:
        code = fn[:-8]
        d, _ = load_clean(os.path.join(HIST_DIR, fn))
        if d is None or len(d) < 80:
            continue
        if tail_bars:
            d = d.iloc[-tail_bars:]
        idx = pd.DatetimeIndex(d["date"])
        close[code] = pd.Series(d["close"].values, index=idx)
        if "amount" in d.columns:
            amount[code] = pd.Series(d["amount"].values, index=idx)
        if with_open and "open" in d.columns:
            opn[code] = pd.Series(d["open"].values, index=idx)
    CL = pd.DataFrame(close).sort_index()
    AM = pd.DataFrame(amount).sort_index().reindex(index=CL.index, columns=CL.columns)
    OP = (pd.DataFrame(opn).sort_index().reindex(index=CL.index, columns=CL.columns)
          if with_open else None)
    return CL, AM, OP


def _names():
    with open(MEMBERS_FILE, encoding="utf-8") as f:
        raw = json.load(f)
    out = {}
    for v in (raw.get("sectors") or {}).values():
        for m in v.get("members", []):
            if m.get("code"):
                out[m["code"]] = m.get("name", "")
    return out


def _code_sectors():
    """个股 -> 所属板块列表（一只股票可能属于多个板块）。"""
    with open(MEMBERS_FILE, encoding="utf-8") as f:
        raw = json.load(f)
    out = {}
    for name, v in (raw.get("sectors") or {}).items():
        for m in v.get("members", []):
            c = m.get("code")
            if c:
                out.setdefault(c, []).append(name)
    return out


# ---------------------------------------------------------------- 状态判定

def market_regime(lvl):
    """按市场等权净值回撤分档。与 sector_review._market_state 同口径。"""
    hi = lvl.rolling(250, min_periods=120).max()
    dd = float(lvl.iloc[-1] / hi.iloc[-1] - 1)
    if dd < -0.20:
        regime = "deep_drawdown"
    elif dd < -0.10:
        regime = "drawdown"
    elif dd < 0:
        regime = "shallow"
    else:
        regime = "high"
    roll = (lvl / hi - 1).tail(750)
    pct = round(float((roll <= dd).mean() * 100), 1) if len(roll) > 120 else None
    return regime, round(dd * 100, 2), pct


# ---------------------------------------------------------------- 选股

def pick_pale_stocks(CL, AM, regime, top_n=20):
    """深跌区选股：20日跌幅最深 + 量比放大。

    打分只用两个已在回测中验证为正的维度，不加板块强度（实测 t=0.27 无效）。
    """
    if regime != "deep_drawdown":
        return [], None

    last = CL.index[-1]
    liq20 = AM.rolling(20, min_periods=10).mean()
    tradable = ((CL.loc[last] >= MIN_PRICE) &
                (AM.loc[last] >= LIQ_FLOOR) &
                (liq20.loc[last] >= LIQ_AVG_FLOOR))
    pool = tradable[tradable].index
    if len(pool) < 50:
        return [], None

    cl = CL[pool]
    am = AM[pool]
    # 信号都在 T 日收盘可得
    ret20 = cl.iloc[-1] / cl.iloc[-21] - 1 if len(cl) > 21 else None
    if ret20 is None:
        return [], None
    amt_now = am.iloc[-1]
    amt_ma20 = am.iloc[-21:].mean()
    vol_ratio = amt_now / amt_ma20.replace(0, np.nan)

    # 超跌前 10% 成池，池内按量比排序取前 top_n。
    # 回测（337个深跌交易日）：+3.16%/10日，t=2.10，胜率53.9%。
    # 不用硬性量比阈值——全市场缩量时会把池子卡到只剩个位数。
    pale_rank = ret20.rank(pct=True)
    pool_codes = pale_rank[pale_rank <= 0.10].index
    if len(pool_codes) == 0:
        return [], None
    vr_pool = vol_ratio.reindex(pool_codes).dropna()
    codes = list(vr_pool.nlargest(min(top_n, len(vr_pool))).index)
    if not codes:
        codes = list(pale_rank.nsmallest(min(top_n, len(pale_rank))).index)

    nm = _names()
    c2s = _code_sectors()
    rows = []
    for c in codes:
        r20 = float(ret20[c])
        vr = float(vol_ratio.get(c, np.nan))
        c_now = float(cl[c].iloc[-1])
        hi60 = float(cl[c].iloc[-60:].max()) if len(cl) > 60 else np.nan
        rows.append({
            "code": c,
            "name": nm.get(c, ""),
            "close": round(c_now, 2),
            "ret20": round(r20 * 100, 2),
            "ret5": round(float(cl[c].iloc[-1] / cl[c].iloc[-6] - 1) * 100, 2) if len(cl) > 6 else None,
            "dist_high60": round((c_now / hi60 - 1) * 100, 2) if hi60 == hi60 else None,
            "vol_ratio": round(vr, 2) if vr == vr else None,
            "sectors": c2s.get(c, [])[:3],
            "score": round(float(pale_rank[c]) * 100, 1),
        })
    rows.sort(key=lambda x: (-(x["vol_ratio"] or 0), x["ret20"]))
    return rows[:top_n], {
        "pool_size": int(len(pool)),
        "pale_pool": int(len(pool_codes)),
        "rule": "20日跌幅前10%% 成池，池内按当日成交额/20日均额排序取前%d" % top_n,
        "backtest": {"mean_10d": 3.16, "t": 2.10, "win_rate": 53.9, "n_days": 337},
    }


# ---------------------------------------------------------------- 板块层

def sector_board(CL_sector):
    """板块强弱 + 资金方向。只作观察，明确标注不参与选股。"""
    rets = CL_sector.pct_change(fill_method=None)
    rets = rets.where(rets.abs() <= 0.215)
    mkt = rets.mean(axis=1, skipna=True)
    s20 = CL_sector.pct_change(20, fill_method=None)
    rs = s20.sub(mkt.rolling(20).sum(), axis=0)
    last = rs.index[-1]
    rel = rs.loc[last].dropna()
    if rel.empty:
        return []
    pct = rel.rank(pct=True)
    out = []
    for name in rel.index:
        s = CL_sector[name].dropna()
        if len(s) < 61:
            continue
        out.append({
            "name": name,
            "ret20": round(float(s.iloc[-1] / s.iloc[-21] - 1) * 100, 2),
            "rel20": round(float(rel[name]) * 100, 2),
            "strength_pct": round(float(pct[name]) * 100, 1),
            "ret5": round(float(s.iloc[-1] / s.iloc[-6] - 1) * 100, 2),
            "dist_high250": round(float(s.iloc[-1] / s.iloc[-250:].max() - 1) * 100, 2)
            if len(s) >= 250 else None,
        })
    out.sort(key=lambda x: -x["strength_pct"])
    return out


def load_sector_close():
    """按板块读取官方 BK 指数收盘价。"""
    with open(MEMBERS_FILE, encoding="utf-8") as f:
        raw = json.load(f)
    cols = {}
    for name, v in (raw.get("sectors") or {}).items():
        bk = v.get("bk")
        p = os.path.join(IDX_DIR, "%s.json" % bk)
        if not os.path.exists(p):
            continue
        try:
            with open(p, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue
        rows = []
        for line in data:
            q = line.split(",")
            if len(q) < 7:
                continue
            try:
                rows.append((q[0], float(q[2]), float(q[6])))
            except Exception:
                continue
        if len(rows) < 300:
            continue
        idx = pd.DatetimeIndex([r[0] for r in rows])
        cols[name] = pd.Series([r[1] for r in rows], index=idx)
    return pd.DataFrame(cols).sort_index()


def sector_fund_flow(CL_sector):
    """资金方向：近5日均额 / 近20日均额。仅作方向提示。"""
    with open(MEMBERS_FILE, encoding="utf-8") as f:
        raw = json.load(f)
    amt = {}
    for name, v in (raw.get("sectors") or {}).items():
        p = os.path.join(IDX_DIR, "%s.json" % v.get("bk"))
        if not os.path.exists(p):
            continue
        try:
            with open(p, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue
        rows = []
        for line in data:
            q = line.split(",")
            if len(q) < 7:
                continue
            try:
                rows.append((q[0], float(q[6])))
            except Exception:
                continue
        if len(rows) < 300:
            continue
        idx = pd.DatetimeIndex([r[0] for r in rows])
        amt[name] = pd.Series([r[1] for r in rows], index=idx)
    if not amt:
        return {}
    A = pd.DataFrame(amt).sort_index()
    flow = A.rolling(5).mean().iloc[-1] / A.rolling(20).mean().iloc[-1] - 1
    return {k: round(float(v) * 100, 1) for k, v in flow.items() if v == v}


# ---------------------------------------------------------------- 对外入口

def build(force=False):
    """构建每日复盘结论。带进程内缓存，按交易日缓存。"""
    with _lock:
        CL, AM, OP = _read_all(tail_bars=TAIL_BARS)
        if CL.empty:
            return {"error": "no_history", "date": None}

        rets = CL.pct_change(fill_method=None)
        rets = rets.where(rets.abs() <= 0.215)
        ew = rets.mean(axis=1, skipna=True).dropna()
        lvl = (1 + ew).cumprod()
        regime, dd, dd_pct = market_regime(lvl)
        play = dict(REGIME_PLAYBOOK[regime])

        picks, pick_meta = pick_pale_stocks(CL, AM, regime)

        sc = load_sector_close()
        board = sector_board(sc) if not sc.empty else []
        flow = sector_fund_flow(sc) if not sc.empty else {}
        for b in board:
            b["fund"] = flow.get(b["name"])

        # 板块只作观察：分位前1/3 记为强势，后1/4 记为规避
        strong = [b for b in board if b["strength_pct"] >= 66.7]
        weak = [b for b in board if b["strength_pct"] <= 25]
        avoid = [b for b in weak]

        date = CL.index[-1].strftime("%Y-%m-%d")
        return {
            "date": date,
            "generated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
            "market": {
                "regime": regime,
                "label": play["label"],
                "dd_from_250d_high": dd,
                "dd_percentile_3y": dd_pct,
                "usable": play["usable"],
                "method": play["method"],
                "note": play["note"],
                "hold_days": play["hold"],
                "stock_count": int(CL.shape[1]),
            },
            "picks": picks,
            "pick_meta": pick_meta,
            "sectors_strong": strong[:6],
            "sectors_avoid": avoid[:6],
            "sectors_all": board,
            "disclaimer": (
                "板块强弱与资金仅作观察，实测对个股收益无加成"
                "（强板块 vs 弱板块差 +0.001%，t=0.27），不构成买入依据。"
                "个股推荐仅为深跌区的统计反弹，非趋势建议。"
            ),
        }


def get(force=False):
    key = "daily"
    with _lock:
        if not force and key in _cache:
            return _cache[key]
    data = build(force=force)
    with _lock:
        _cache[key] = data
    return data
