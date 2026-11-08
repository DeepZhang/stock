"""统一榜单：按板块合并三路信号，只读本地 JSON 文件。
 
胜率口径全部取自回测报告实测数，不编造：
- 涨停次日：63.03%（n=10762，持有次日/1 天），
  stock-analysis/shortterm_backtest.md；产物
  data/shortterm_picks.json 的 backtest.rows 同值（百分比口径）。
- 超跌反弹：69.03%（n=3213，持有 3-5 天），来源同上。
- 板块龙头：每只票挂自己的历史 5 日胜率与样本数 n（分数口径），
  组合全期 48.77%（n=166876）；产物
  data/sector_leaders_picks.json 的 backtest.combo.full 与
  各板块 combo.full 同值（full 口径）。
- 旧突破选股器：stock-analysis/ 里没有它的单独回测，胜率栏
  不写数字，页面标“未单独回测”。
 
板块归属：板块龙头按产物里的板块分；旧突破选股器用产物自带
的 sector 字段；短线票没有板块字段，按仓库内冻结的
stock-analysis/sector_members.json（2026-11-08 东财概念成分）
归属，一只票可属于多个板块。只读本地文件，不做同步扫描，
不调外部服务。
"""
 
import json
 
try:
    from stock_data import SECTOR_MAP
except ImportError:  # 直接以包方式导入时
    from modules.stock_data import SECTOR_MAP
 
DEFAULT_THRESHOLD = 0.5
TOP_PER_SECTOR = 3
 
# （信号名，胜率，样本数 n，持有天数）：回测报告实测数，见模块说明。
LIMIT_UP = ("涨停次日", 0.6303, 10762, "次日（1天）")
OVERSOLD = ("超跌反弹", 0.6903, 3213, "3-5天")
LEADER_COMBO = (0.4877, 166876)
SIGNAL_LEADER = "板块龙头"
SIGNAL_SCREEN = "旧突破选股器"
LEADER_HOLDING = "5天"
 
 
def load_json(path):
    """读一个本地 JSON 文件，缺失或损坏返回 None。"""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None
 
 
def shortterm_rates(shortterm):
    """短线两策略的（胜率，n）：优先用产物 backtest.rows，缺失用常量。"""
    rates = {"limit_up": (LIMIT_UP[1], LIMIT_UP[2]),
             "oversold": (OVERSOLD[1], OVERSOLD[2])}
    rows = []
    if isinstance(shortterm, dict):
        rows = (shortterm.get("backtest") or {}).get("rows") or []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = row.get("signal")
        rate = row.get("win_rate")
        n = row.get("n")
        if rate is None or n is None:
            continue
        rate = float(rate)
        if rate > 1:  # 产物里回测行以百分比计，如 63.0273
            rate = rate / 100.0
        period = row.get("period") or ""
        full = "2024-01-01~2026-11-06" in period
        # 同名行有多条（如 2026 年计入子样本）：只取全期行。
        if name == "涨停次日" and (full or rates["limit_up"] == (LIMIT_UP[1], LIMIT_UP[2])):
            rates["limit_up"] = (rate, int(n))
        elif name == "超跌双信号" and (full or rates["oversold"] == (OVERSOLD[1], OVERSOLD[2])):
            rates["oversold"] = (rate, int(n))
    return rates
 
 
def _combo_full(entry):
    """取一组数据里的 combo.full（胜率，n），胜率为分数口径。"""
    if not isinstance(entry, dict):
        return None, None
    full = (entry.get("combo") or {}).get("full") or {}
    rate = full.get("win_rate")
    n = full.get("n")
    return (float(rate) if rate is not None else None,
            int(n) if n is not None else None)
 
 
def _overall_combo(leaders):
    """板块龙头组合全期胜率：优先用产物 backtest.combo.full。"""
    if isinstance(leaders, dict):
        rate, n = _combo_full(leaders.get("backtest") or {})
        if rate is not None:
            return rate, n
    return LEADER_COMBO
 
 
def _code_sectors(members):
    """冻结成员文件转成 代码 -> [板块名]，只保留 SECTOR_MAP 里的板块。"""
    mapping = {}
    if not isinstance(members, dict):
        return mapping
    sectors = members.get("sectors") or {}
    for name, entry in sectors.items():
        if name not in SECTOR_MAP:
            continue
        for member in (entry or {}).get("members") or []:
            code = member.get("code") if isinstance(member, dict) else None
            if code:
                mapping.setdefault(code, []).append(name)
    return mapping
 
 
def _add_signal(pool, code, name, signal, win_rate, n, holding, date):
    """把一个信号并进板块候选池：同一代码一行，同一信号只记一次。"""
    row = pool.setdefault(code, {"code": code, "name": name, "signals": []})
    if signal not in [s["signal"] for s in row["signals"]]:
        row["signals"].append({"signal": signal, "win_rate": win_rate,
                                "n": n, "holding": holding, "date": date})
 
 
def _finish_row(row):
    """算出一行的排序胜率：各信号胜率的最大值，并注明来源信号。"""
    rated = [s for s in row["signals"] if s["win_rate"] is not None]
    if rated:
        best = max(rated, key=lambda s: (s["win_rate"], s["n"] or 0))
        row["best_win_rate"] = best["win_rate"]
        row["best_n"] = best["n"]
        row["best_signal"] = best["signal"]
    else:
        row["best_win_rate"] = None
        row["best_n"] = None
        row["best_signal"] = None
    return row
 
 
def build_unified(shortterm=None, leaders=None, screen=None, members=None,
                  threshold=DEFAULT_THRESHOLD, top_n=TOP_PER_SECTOR):
    """按板块合并三路信号：文件缺失时该来源贡献为空，不报错。"""
    rates = shortterm_rates(shortterm)
    code_sectors = _code_sectors(members)
    leader_sectors = leaders.get("sectors") or {} if isinstance(leaders, dict) else {}
    as_of = ""
    if isinstance(leaders, dict):
        as_of = leaders.get("as_of") or ""
    if not as_of and isinstance(shortterm, dict):
        as_of = shortterm.get("as_of") or ""
    combo_rate, combo_n = _overall_combo(leaders)
    recommended = []
    skipped = []
    total_candidates = 0
    total_picks = 0
    for name in SECTOR_MAP:
        entry = leader_sectors.get(name) or {}
        combo_rate_s, combo_n_s = _combo_full(entry)
        pool = {}
        for pick in entry.get("top3") or []:
            if not isinstance(pick, dict) or not pick.get("code"):
                continue
            rate = pick.get("win_rate")
            _add_signal(pool, pick["code"], pick.get("name") or pick["code"],
                        SIGNAL_LEADER, float(rate) if rate is not None else None,
                        int(pick["n"]) if pick.get("n") is not None else None,
                        LEADER_HOLDING, pick.get("last_signal"))
        if isinstance(shortterm, dict):
            for key, sig in (("limit_up", LIMIT_UP[0]), ("oversold", OVERSOLD[0])):
                rate, n = rates[key]
                holding = LIMIT_UP[3] if key == "limit_up" else OVERSOLD[3]
                for pick in shortterm.get(key) or []:
                    if not isinstance(pick, dict) or not pick.get("code"):
                        continue
                    if name not in code_sectors.get(pick["code"], []):
                        continue
                    _add_signal(pool, pick["code"], pick.get("name") or pick["code"],
                                sig, rate, n, holding, pick.get("date"))
        if isinstance(screen, dict):
            for pick in screen.get("list") or []:
                if not isinstance(pick, dict) or not pick.get("code"):
                    continue
                if pick.get("sector") != name:
                    continue
                _add_signal(pool, pick["code"], pick.get("name") or pick["code"],
                            SIGNAL_SCREEN, None, None, None, pick.get("date"))
        rows = [_finish_row(row) for row in pool.values()]
        rows.sort(key=lambda r: (-(r["best_win_rate"] if r["best_win_rate"] is not None else -1.0),
                                 -(r["best_n"] or 0), r["code"]))
        total_candidates += len(rows)
        if combo_rate_s is None:
            skipped.append({"sector": name, "sector_code": SECTOR_MAP[name],
                            "combo_win_rate": None, "combo_n": combo_n_s,
                            "reason": "无组合胜率数据"})
        elif combo_rate_s < threshold:
            skipped.append({"sector": name, "sector_code": SECTOR_MAP[name],
                            "combo_win_rate": combo_rate_s, "combo_n": combo_n_s,
                            "reason": "组合胜率低于 50%"})
        else:
            picks = rows[:top_n]
            total_picks += len(picks)
            recommended.append({"sector": name, "sector_code": SECTOR_MAP[name],
                                "combo_win_rate": combo_rate_s, "combo_n": combo_n_s,
                                "candidate_count": len(rows), "picks": picks})
    return {"as_of": as_of, "threshold": threshold,
            "combo_win_rate": combo_rate, "combo_n": combo_n,
            "sectors": recommended, "skipped": skipped,
            "counts": {"sectors": len(SECTOR_MAP),
                       "recommended_sectors": len(recommended),
                       "skipped_sectors": len(skipped),
                       "candidates": total_candidates,
                       "picks": total_picks}}
