# -*- coding: utf-8 -*-
"""价格数据清洗：按「聚集性」而非「幅度」区分故障类型。

【数据缺陷实测】（本地 data/history/*.parquet，约 566 万行）

1. 减法前复权负价
   29 只股票存在 close<=0 的行（共 3739 行），集中在 2015-2016 年高分红股
   （美的 000333、格力 000651、兖矿 600188、云天化 600096 等）。
   根因：adjusted = raw - 累计分红，累计分红超过早期股价即为负。
   这批股票 2018 年后 corr(pct_chg, close收益)=1.000（复权正确），
   2015-2016 为 -0.05（该段损坏）。损坏集中在早期，尾部可信。

2. 关键发现：故障的判别特征是「聚集性」，不是「幅度」
   - 结构性损坏：连续几十到上千根 bar 成片损坏，是区域故障。
     实测 600188 有 1397 根连续损坏（2021 年前），000333 有 303 根
     （2016 年前），是前复权负价造成的整段失效。
   - 孤立脏 bar：散落的单点跳空，是点故障。
     实测 300394 共 42 个脏 bar，散布在 16 个簇里，最大簇仅 8 根。

   若按「幅度」判别会严重误判：300394 的孤立跳空高达 +30%，
   而结构性区里也有 +21% 的 bar。因此本模块按簇大小判别——
   簇 >= STRUCT_BARS(10) 且结束于历史前 60% 视为结构性损坏并截断，
   其余脏 bar 一律只丢当根及后一根（后者以脏价为基准，收益同样错）。

3. 阈值为何取 21.5%
   A 股涨跌幅限制：主板 ±10%、创业板/科创板 ±20%、ST ±5%。
   实测创业板日收益 p99.9 = 20.38%、p99.99 = 20.24%——合法涨停常略超 20%，
   故阈值取 21.5% 留出噪音带。原 35% 阈值只抓到 2418 行 / 49 只，漏掉一半。

4. 旧实现 `if (ret.abs() > 0.35).any(): return None` 整只丢弃
   实测剔除 125/4370 只。锂矿概念丢 20.5% 成分、年化被低估 4.39pct；
   美的、格力等高分红白马被误杀，板块成分系统性偏向低分红高波动股。
   修复后 2683 只可用（原 2618 只），仅 31 只需截断、保留率中位 0.87。

5. pct_chg 列不可用于重建历史
   实测 (1+pct_chg).cumprod() 重建，600188 最小值 -145.9、000333 为 -22.4
   ——pct_chg 与 close 同源损坏，无法相互修复。
"""
import os

import numpy as np
import pandas as pd

# 单根 bar 越界阈值：超过即判为脏 bar（A 股 20% 涨跌幅 + 噪音带）
RET_LIMIT = 0.215
# 簇内两根脏 bar 间隔不超过此值即视为同一簇
CLUSTER_GAP = 5
# 簇长达到此值判为结构性损坏（成片失效），否则视为孤立点故障
STRUCT_BARS = 10
# 结构性区结束位置须在历史前此比例内才截断，避免近期损坏砍掉全部数据
STRUCT_EARLY_FRAC = 0.60
# 新股上市初期无涨跌幅限制，前 N 根不做判定
LISTING_GRACE = 10
MIN_BARS = 120

# 兼容旧接口名
HARD_LIMIT = RET_LIMIT


def _bad_mask(close, limit=RET_LIMIT, grace=LISTING_GRACE):
    """脏 bar 判定：close<=0 恒为脏，或 |日收益|>limit。

    grace 只豁免「收益越界」——新股上市初期无涨跌幅限制，+32% 是合法的。
    但 close<=0 是物理上不可能的负价，与上市初期无关，永不豁免。
    """
    close = pd.Series(close, dtype=float).reset_index(drop=True)
    ret = close.pct_change(fill_method=None)
    nonpos = (close <= 0).fillna(False)
    missing = close.isna()
    jump = (ret.abs() > limit).fillna(False)
    if len(jump):
        jump.iloc[:grace] = False
    return nonpos | missing | jump


def _clusters(idx, gap=CLUSTER_GAP):
    """把脏 bar 序号按间隔聚成簇，返回 [[i,...], ...]。"""
    if len(idx) == 0:
        return []
    cuts = np.flatnonzero(np.diff(idx) > gap)
    return [g for g in np.split(idx, cuts + 1) if len(g)]


def detect_clean_start(close, limit=RET_LIMIT, grace=LISTING_GRACE):
    """返回首个可信 bar 序号：最后一个「结构性损坏簇」之后。

    孤立脏 bar 不会触发截断，只会在后续按点丢弃。
    """
    bad = _bad_mask(close, limit=limit, grace=grace)
    n = len(bad)
    idx = np.flatnonzero(bad.values)
    if not len(idx):
        return 0
    start = 0
    for g in _clusters(idx):
        # 成片损坏且结束在历史前段 -> 早期结构性损坏，截断
        if len(g) >= STRUCT_BARS and g[-1] < STRUCT_EARLY_FRAC * n:
            start = int(g[-1]) + 1
    return start


def _punch(d, bad):
    """把脏 bar 的价格字段置 NaN（打洞），而不是删行。

    【为什么不能删行】删行会让前后两根变成相邻，凭空造出跳空。
    实测 002756：原始 1.14 -> 2.23(+96%) -> 3.43 -> 3.98(+16%)，
    删掉中间两根后 1.14 与 3.98 相邻，伪造出 +249% 的假收益。
    实测该做法在 157 只股票上造出 568 个伪越界点，其中 92% 是伪影。

    打洞则让 pct_change 自然在洞处产出 NaN（洞本身与洞后一根都无法
    计算收益），跳空永不被跨越；rolling/max/mean 等默认 skipna，
    窗口自动跳过洞。时间轴保持完整，".iloc[-k]" 仍是 k 根之前。
    """
    if not bad.any():
        return d, 0
    out = d.copy()
    cols = [c for c in ("open", "close", "high", "low", "amount", "volume",
                        "pe", "pb") if c in out.columns]
    out.loc[bad.values, cols] = np.nan
    # pct_chg 必须由清洗后的 close 重算，不能沿用原始值：原始 pct_chg 里
    # 留着脏 bar 的收益（实测 002756 在洞处仍有 +95.61%），下游若读该列
    # 就会拿到越界值，使打洞失效。重算后洞与洞后一根自然是 NaN。
    if "pct_chg" in out.columns:
        out["pct_chg"] = frame_returns(out) * 100
    return out, int(bad.sum())


def clean_frame(d, limit=RET_LIMIT, min_bars=MIN_BARS, grace=LISTING_GRACE,
                struct_bars=STRUCT_BARS):
    """对内存中的日线 DataFrame 做两级清洗（用于官方指数等）。

    返回 (df, qc)；df 不可用时为 None。清洗方式为「截断早期结构性损坏段
    + 对其余脏 bar 打洞」，不删行，理由见 _punch 文档。
    qc 字段：
      truncated_bars  被截断的早期 bar 数
      holes           打了洞的脏 bar 数
      bars            清洗后行数（含洞）
      valid_bars      有效 bar 数（不含洞）
      reason          不可用原因（可用时为 None）
    """
    if d is None or len(d) == 0 or "close" not in d.columns:
        return None, {"reason": "空或缺 close 列", "truncated_bars": 0, "holes": 0}
    d = (d.drop_duplicates("date", keep="last")
           .sort_values("date")
           .reset_index(drop=True))
    if len(d) < min_bars:
        return None, {"reason": "样本不足(%d)" % len(d), "truncated_bars": 0,
                      "holes": 0}

    n = len(d)
    idx = np.flatnonzero(_bad_mask(d["close"], limit=limit, grace=grace).values)
    start = 0
    for g in _clusters(idx):
        # 损坏簇紧贴序列开头：起点即坏，无有效数据可保留，一律截断，
        # 不受簇大小约束（否则会留下前导残段与空洞）
        if g[0] <= grace:
            start = max(start, int(g[-1]) + 1)
        elif len(g) >= struct_bars and g[-1] < STRUCT_EARLY_FRAC * n:
            start = max(start, int(g[-1]) + 1)
    if n - start < min_bars:
        return None, {"reason": "截断后样本不足(%d)" % (n - start),
                      "truncated_bars": start, "holes": 0}

    d = d.iloc[start:].reset_index(drop=True)
    bad = _bad_mask(d["close"], limit=limit, grace=grace)
    # 末端脏 bar 无法打洞（当前价缺失就没有意义），直接裁掉
    last = np.flatnonzero(~bad.values)
    if not len(last):
        return None, {"reason": "全部为脏 bar", "truncated_bars": start, "holes": 0}
    d = d.iloc[:int(last[-1]) + 1].reset_index(drop=True)
    bad = bad.iloc[:len(d)]

    d, holes = _punch(d, bad)
    valid = int(d["close"].notna().sum())
    if valid < min_bars:
        return None, {"reason": "打洞后有效样本不足(%d)" % valid,
                      "truncated_bars": start, "holes": holes}
    return d, {"truncated_bars": start, "holes": holes, "bars": len(d),
               "valid_bars": valid, "reason": None}


def frame_returns(d):
    """日收益，洞处为 NaN。

    洞与其后一根的收益都是 NaN：洞本身无价，洞后一根的基准是洞，
    两者都不可计算——这正是打洞相对删行的优势。
    """
    return d["close"].astype(float).pct_change(fill_method=None)


def tail_return(d, k):
    """最近 k 根的区间收益；窗口内含洞则返回 NaN（不跨越洞拼接）。

    【注意】不可写成 c.dropna().iloc[-k-1] —— 那样洞会被悄悄跳过，
    「最近 5 日收益」实际变成「最近 5 个有效 bar 的收益」，区间被拉长。
    正确做法是直接在等长序列上取 iloc[-k-1]，洞自然传播为 NaN。
    洞后第一根收益也不可算（基准是洞），故取 -k-2。
    """
    c = d["close"].astype(float).reset_index(drop=True)
    if k < 1 or len(c) < k + 1:
        return np.nan
    window = c.iloc[-k - 1:]
    if window.isna().any():
        return np.nan
    return float(c.iloc[-1] / c.iloc[-k - 1] - 1)


def load_clean(path, min_bars=MIN_BARS, limit=RET_LIMIT, grace=LISTING_GRACE):
    """读取并清洗单只股票日线。返回 (df, qc)，df 为 None 表示不可用。"""
    empty = {"truncated_bars": 0, "holes": 0}
    if not os.path.exists(path):
        return None, dict(empty, reason="文件缺失")
    try:
        d = pd.read_parquet(path)
    except Exception as e:  # 损坏文件不应中断整批扫描
        return None, dict(empty, reason="读取失败:%s" % e)
    if d is None or len(d) == 0 or "close" not in d.columns:
        return None, dict(empty, reason="空或缺列")
    keep = [c for c in ("date", "open", "close", "high", "low", "amount",
                        "volume", "turnover", "pct_chg", "pe", "pb") if c in d.columns]
    d = d[keep].copy()
    if "date" in d.columns:
        d["date"] = pd.to_datetime(d["date"])
    return clean_frame(d, limit=limit, min_bars=min_bars, grace=grace)


def clean_return_matrix(close, amount=None, limit=RET_LIMIT,
                        liquidity_floor=None, min_stocks=8):
    """多股日收益矩阵，逐 bar 剔除越界值。

    参数
      close           收盘价矩阵（列=股票，行=日期，前复权）
      amount          成交额矩阵，用于流动性过滤
      liquidity_floor 最低成交额（元），低于此值的股票当日不计入
      min_stocks      当日有效股票数下限，不足则该板块当日返回 NaN

    返回 (ret, coverage)；coverage 为每日有效股票数。
    """
    px = close.astype(float).replace([np.inf, -np.inf], np.nan)
    ret = px.pct_change(fill_method=None)
    # 负价会同时污染前后两根收益，一并剔除
    bad = (px <= 0).values
    if bad.any():
        prev = np.zeros_like(bad)
        prev[1:] = bad[:-1]
        nxt = np.zeros_like(bad)
        nxt[:-1] = bad[1:]
        ret = ret.where(~(bad | prev | nxt))
    # A 股涨跌幅上限，越界即脏数据
    ret = ret.where(ret.abs() <= limit)
    if amount is not None:
        amt = amount.reindex_like(px)
        ret = ret.where(amt >= liquidity_floor) if liquidity_floor is not None \
            else ret.where(amt.notna())
    ret = ret.replace([np.inf, -np.inf], np.nan)
    cov = ret.notna().sum(axis=1)
    ret = ret.where(cov >= min_stocks, axis=0)
    return ret, cov


def sector_returns(close, amount=None, winsor=RET_LIMIT, min_stocks=8, **kw):
    """板块等权日收益：对成分股日收益求均值（非价格均值）。

    价格均值会让高价股主导板块走势；收益均值才是等权配置的日收益。
    """
    ret, cov = clean_return_matrix(close, amount, limit=winsor,
                                   min_stocks=min_stocks, **kw)
    ew = ret.mean(axis=1, skipna=True)
    return ew.where(cov >= min_stocks), cov
