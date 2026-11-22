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
  4. **板块资金流在深跌区超跌池内有效**（2026-11 新增，见
     docs/sector_signal_backtest.md）：池内按所属板块资金高低配对差分，
     10日 Δ=+0.626%(t=2.56)，20日 Δ=+1.435%(t=2.77)。切分阈值 30/50/70
     分位都为正，前/后半样本同向，20日 4/4 独立时段为正。
     资金与强度秩相关 +0.304(t=7.51) 存在共线，但强度分层后资金在弱/中层
     仍有效（20日 中层 t=3.64）——资金不是强度的代理变量。
     **限制**：深跌区 353 天仅 27 个独立时段，证据强度按 27 计。
  5. **板块层「强势 + 资金」不可交易**（本次否证）：先进前25%强势池再按资金
     分高低配对差分，10日 Δ=-0.079%(t=-0.59)、20日 Δ=-0.213%(t=-0.97)，
     全部持有期不显著且多为负。2×2 象限表看似「强+资金高」更优，
     但那是「强/弱」维度的边界效应，配对差分后消失——**页面不得展示
     未做增量检验的象限表**。

因此本模块的推荐逻辑是单层的：先用市场状态决定「今天该不该出手」，
再在可交易池里按超跌 + 放量排序；深跌区在此之上叠加板块资金流分档。
板块强弱单独成栏，明确标注仅供参考。
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

# 落盘缓存：进程内缓存重启即失效，而线上 1.8G 内存下完整构建一次要读
# 5478 只 parquet、峰值约 950MB、耗时约 60 秒。若服务在开盘前重启
# （OOM 或部署），早上第一次打开页面就要干等一分钟。
#
# 失效判据用**数据自身的指纹**而非墙钟时间：历史 parquet 与板块指数由
# 不同脚本按需拉取（实测 parquet 停在 11-20、指数在 11-22），拿当天日期
# 做判据会把旧行情当成新数据。因此校验两个目录的指纹（文件数 + 最新
# mtime）是否变化，任一变化即重建——宁可慢一次，也不给出过期结论。
DISK_CACHE = os.path.join(CACHE_DIR, "daily_recommend.json")


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
    """深跌区选股：20日跌幅最深 + 量比放大 + 所属板块资金流入。

    打分维度（均在回测中验证为正）：
      1. 20 日跌幅前 10% 成池（主筛）
      2. 池内按量比排序（已验证 +3.16%/10日）
      3. 池内板块资金流排序（新增，配对差分验证）

    第 3 项的依据（verify_deep_drawdown_incr.py / verify_flow_robust.py）：
    在深跌区超跌池内，按个股所属板块资金流高低做配对差分：
      10日 Δ=+0.626%(t=2.56)，20日 Δ=+1.435%(t=2.77)
    切分阈值 30/50/70 分位都为正；前/后半样本同向；20日 4/4 独立时段为正。
    正交化检验（verify_flow_orthogonal.py）：资金与强度秩相关 +0.304(t=7.51)，
    存在共线，但强度分层后资金在弱/中层仍有效（20日 中层 t=3.64）。
    板块强度本身在深跌区无效（t=0.27），故只加资金、不加强度。

    样本限制：深跌区 353 个交易日仅来自 27 个独立时段，证据强度按 27 计。
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

    # 所属板块资金流（深跌区超跌池内的有效增量因子，见函数 docstring）
    c2s = _code_sectors()
    sec_flow = {}
    try:
        F = sector_fund_flow_series()
        if not F.empty:
            last_f = F.iloc[-1]
            sec_flow = {k: float(v) for k, v in last_f.items() if v == v}
    except Exception:
        sec_flow = {}

    def _stock_flow(code):
        """个股所属板块资金流的最大值（多板块取最强）。"""
        v = [sec_flow[s] for s in c2s.get(code, []) if s in sec_flow]
        return max(v) if v else float("nan")

    nm = _names()
    rows = []
    for c in codes:
        r20 = float(ret20[c])
        vr = float(vol_ratio.get(c, np.nan))
        c_now = float(cl[c].iloc[-1])
        hi60 = float(cl[c].iloc[-60:].max()) if len(cl) > 60 else np.nan
        sf = _stock_flow(c)
        rows.append({
            "code": c,
            "name": nm.get(c, ""),
            "close": round(c_now, 2),
            "ret20": round(r20 * 100, 2),
            "ret5": round(float(cl[c].iloc[-1] / cl[c].iloc[-6] - 1) * 100, 2) if len(cl) > 6 else None,
            "dist_high60": round((c_now / hi60 - 1) * 100, 2) if hi60 == hi60 else None,
            "vol_ratio": round(vr, 2) if vr == vr else None,
            "sector_flow": round(sf, 1) if sf == sf else None,
            "sectors": c2s.get(c, [])[:3],
            "score": round(float(pale_rank[c]) * 100, 1),
        })

    # 板块资金流的口径：回测是在「已选出的 top_n 只」内部按资金中位数分高低
    # 比较（verify_deep_drawdown_incr.py），不是在量比同档之间比较。
    # 因此保持 top_n 的选取集合不变（不削弱已验证的量比主信号），
    # 只把资金高的排到前面，并标出哪一半是资金强的一档。
    flows = [r["sector_flow"] for r in rows if r["sector_flow"] is not None]
    flow_med = float(np.median(flows)) if flows else None
    for r in rows:
        f = r["sector_flow"]
        r["flow_strong"] = None if (f is None or flow_med is None) else bool(f >= flow_med)
    if flow_med is not None:
        # 回测证据只支持「两档之间」的差别（资金强的一半 > 资金弱的一半），
        # 不支持按资金数值逐只排序。因此先按档位分前后，档内仍按量比——
        # 量比是已验证的主信号，不能因为资金档位被打乱。
        rows.sort(key=lambda x: (0 if x.get("flow_strong") else 1,
                                 -(x["vol_ratio"] or 0)))
    return rows[:top_n], {
        "flow_median": round(flow_med, 1) if flow_med is not None else None,
        "flow_strong_n": sum(1 for r in rows if r.get("flow_strong")),
        "pool_size": int(len(pool)),
        "pale_pool": int(len(pool_codes)),
        "rule": ("20日跌幅前10%% 成池，池内按量比取前%d（不变量比主信号），"
                 "再按所属板块资金流排序，并标出资金强/弱两档" % top_n),
        "backtest": {"mean_10d": 3.16, "t": 2.10, "win_rate": 53.9, "n_days": 337},
        "flow_factor": {
            "delta_10d_pct": 0.626, "t_10d": 2.56,
            "delta_20d_pct": 1.435, "t_20d": 2.77,
            "note": "深跌区超跌池内，板块资金高低配对差分；20日4/4独立时段为正",
            "caveat": "27 个独立时段",
        },
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


def sector_fund_flow_series():
    """板块资金流序列：近5日均额 / 近20日均额 - 1（百分比）。

    返回 DataFrame（index=日期, columns=板块名）。只保留行数>=300 的板块，
    数据不完整的（如光纤154行、电网169行、固态电池0字节）会被自动排除。
    """
    with open(MEMBERS_FILE, encoding="utf-8") as f:
        raw = json.load(f)
    amt = {}
    for name, v in (raw.get("sectors") or {}).items():
        p = os.path.join(IDX_DIR, "%s.json" % v.get("bk"))
        if not os.path.exists(p) or os.path.getsize(p) == 0:
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
        return pd.DataFrame()
    A = pd.DataFrame(amt).sort_index()
    m5 = A.rolling(5, min_periods=3).mean()
    m20 = A.rolling(20, min_periods=10).mean()
    return (m5 / m20 - 1) * 100


def sector_fund_flow(CL_sector=None):
    """资金方向：近5日均额 / 近20日均额。仅作方向提示。"""
    F = sector_fund_flow_series()
    if F.empty:
        return {}
    flow = F.iloc[-1]
    return {k: round(float(v), 1) for k, v in flow.items() if v == v}


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


def _dir_fingerprint(path):
    """目录指纹：parquet/json 文件数 + 最新 mtime。

    数据是按需拉取的，用最新 mtime 能捕捉到「又拉了一批」，
    用文件数能捕捉到「新增了标的」。两者都很便宜（只 stat 不读内容）。
    """
    if not os.path.isdir(path):
        return [0, 0]
    count, newest = 0, 0
    for fn in os.listdir(path):
        if not (fn.endswith(".parquet") or fn.endswith(".json")):
            continue
        try:
            m = os.path.getmtime(os.path.join(path, fn))
        except OSError:
            continue
        count += 1
        if m > newest:
            newest = m
    return [count, round(newest, 3)]


def _load_disk_cache():
    """读落盘缓存；不存在、损坏或指纹不匹配时返回 None。"""
    if not os.path.exists(DISK_CACHE):
        return None
    try:
        with open(DISK_CACHE, encoding="utf-8") as f:
            blob = json.load(f)
    except Exception:
        return None
    if not isinstance(blob, dict) or "data" not in blob:
        return None
    fp = blob.get("fingerprint") or {}
    if fp.get("history") != _dir_fingerprint(HIST_DIR):
        return None
    if fp.get("index") != _dir_fingerprint(IDX_DIR):
        return None
    data = blob["data"]
    if not isinstance(data, dict) or data.get("error"):
        return None
    return data


def _save_disk_cache(data):
    """原子写落盘缓存，避免读到写了一半的文件。"""
    if not isinstance(data, dict) or data.get("error") or not data.get("date"):
        return
    blob = {
        "data": data,
        "fingerprint": {
            "history": _dir_fingerprint(HIST_DIR),
            "index": _dir_fingerprint(IDX_DIR),
        },
    }
    tmp = "%s.tmp.%d" % (DISK_CACHE, os.getpid())
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(blob, f, ensure_ascii=False)
        os.replace(tmp, DISK_CACHE)
    except Exception:
        # 写缓存失败绝不能影响主流程：删掉半截文件后静默返回，
        # 下次请求会重新构建。
        try:
            os.remove(tmp)
        except Exception:
            pass


def get(force=False):
    key = "daily"
    with _lock:
        if not force and key in _cache:
            return _cache[key]

    # 进程内缓存未命中：先试落盘缓存（重启后也能秒开）。
    # force=True 表示调用方明确要求重算（如 15:30 定时预热），跳过落盘。
    if not force:
        cached = _load_disk_cache()
        if cached is not None:
            with _lock:
                _cache[key] = cached
            return cached

    data = build(force=force)
    with _lock:
        _cache[key] = data
    # force 只表示「别用旧结果」，新算出来的结果仍要落盘——
    # 15:30 定时预热走的正是这条路径，不写入就白预热了。
    _save_disk_cache(data)
    return data
