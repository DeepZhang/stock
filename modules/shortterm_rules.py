"""Phase 1 规则与研究计划。无交易接口；历史同类胜率不代表未来概率。"""
import math
import re

import numpy as np
import pandas as pd

RISK_WORDS = ('立案调查', '被立案', '业绩大幅下修', '业绩下修', '退市风险', '财务造假')


def features(bars, code):
    """仅使用当日及以前数据；RSI 与 analyze_patterns.py 的 Wilder 口径一致。"""
    d = pd.DataFrame(bars).sort_values('date').drop_duplicates('date').reset_index(drop=True)
    for col in ('open', 'close', 'high', 'low', 'volume', 'amount', 'pct_chg', 'turnover'):
        if col not in d:
            d[col] = np.nan
        d[col] = pd.to_numeric(d[col], errors='coerce')
    c = d['close']
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    d['rsi14'] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    d['ret_5'] = c.pct_change(5, fill_method=None)
    d['vol_ratio'] = d['volume'] / d['volume'].rolling(20).mean().shift(1)
    d['is_limit_up'] = d['pct_chg'] >= (19 if code.startswith(('30', '68')) else 9.5)
    groups = (~d['is_limit_up']).cumsum()
    d['boards'] = d['is_limit_up'].astype(int).groupby(groups).cumsum()
    d['one_price'] = d['is_limit_up'] & d['high'].eq(d['low']) & d['open'].eq(c)
    d['low5'] = d['low'].rolling(5).min()
    d['double_signal'] = (d['ret_5'] <= -.08) & (d['rsi14'] <= 20)
    return d.replace([np.inf, -np.inf], np.nan)


def eligible(name, row):
    """无法确认可交易性时拒绝；历史 ST/公告状态不可由今日名称回填。"""
    if not name or re.search(r'ST|退', name, re.I):
        return False
    for key in ('open', 'close', 'high', 'low', 'volume', 'amount'):
        value = row.get(key)
        try:
            if value is None or not math.isfinite(float(value)) or float(value) <= 0:
                return False
        except (TypeError, ValueError):
            return False
    return not row.get('suspended', False)


def risk_keyword(texts):
    """仅检查调用方提供且截至信号日已公开的文本；未提供时不声称通过公告排雷。"""
    return any(word in text for text in texts for word in RISK_WORDS)


def filter_masks(d, strategy):
    """候选阈值预先固定，是否启用由回测 policy 决定。无封单字段则不伪造代理。"""
    if strategy == 'limit_up':
        return {
            'amount_ge_100m': d['amount'] >= 1e8,
            'turnover_1_20': d['turnover'].between(1, 20),
            'turnover_2_15': d['turnover'].between(2, 15),
            'first_board': d['boards'] == 1,
            'boards_lt_3': d['boards'] < 3,
            'volume_ratio_le_3': d['vol_ratio'].le(3),
            'volume_ratio_le_5': d['vol_ratio'].le(5),
        }
    return {'double_signal': d['double_signal']}


def passes_filters(row, strategy, policy):
    d = pd.DataFrame([row])
    masks = filter_masks(d, strategy)
    return all(bool(masks[key].iloc[0]) for key in policy.get('filters', {}).get(strategy, []))


def historical_score(row, strategy, policy):
    group = ('first' if row['boards'] == 1 else 'multi') if strategy == 'limit_up' else ('double' if row['double_signal'] else 'single')
    stats = policy.get('calibration', {}).get(strategy, {})
    item = stats.get(group, {})
    if item.get('n', 0) < 50:
        item = stats.get('all', {})
    return {'historical_win_rate': item.get('win_rate'), 'historical_n': item.get('n', 0),
            'score_group': group, 'calibration_end': policy.get('calibration_end'),
            'expected_win_rate': item.get('win_rate'),
            'score_note': '同类历史收盘价信号收益胜率，非次日开盘买入盈利概率'}


def sell_plan(strategy, row):
    """价格待实际入场后计算；信号收盘价不是次日可成交价。"""
    common = {'buy_reference': '次日集合竞价/开盘价，待实际报价确认',
              'buy_price': None, 'stop_price': None,
              'research_only': True,
              'execution_note': 'A股次日新买仓位当天不可卖；当日卖出情景仅用于研究或已有可卖底仓。'}
    if strategy == 'limit_up':
        common.update(stop_loss_pct=-3, take_profit_pct=3, holding_days=1,
                      stop_formula='实际买入价 × 0.97',
                      take_profit_rule='研究情景：相对入场价冲高3%分批了结；跌破-3%止损；尾盘退出，不隔夜。新买仓位须等下一可卖交易日。',
                      evidence='参见回测报告的收盘止损/止盈情景；日线不能验证分批和盘中先后顺序。')
    else:
        common.update(stop_loss_pct=-5, holding_days='3–5', recent_low=float(row['low5']),
                      stop_formula='max(实际买入价 × 0.95, 信号日前5日最低价)，若开盘已低于近期低点则放弃',
                      take_profit_rule='第3日收盘盈利则退出，否则第5日收盘退出；可卖日起收盘跌破止损位退出。',
                      evidence='收盘触发回测，不宣称盘中立即成交；跳空按当日收盘计价。')
    return common


def candidate(code, name, row, strategy, policy):
    if not eligible(name, row) or not passes_filters(row, strategy, policy):
        return None
    if strategy == 'limit_up' and (not row['is_limit_up'] or row['one_price']):
        return None
    if strategy == 'oversold':
        if not (row['ret_5'] <= -.08 or row['rsi14'] <= 20):
            return None
        if pd.isna(row.get('low5')) or float(row['low5']) <= 0:
            return None
    out = {key: (None if pd.isna(row.get(key)) else float(row[key])) for key in
           ('close', 'pct_chg', 'turnover', 'vol_ratio', 'amount', 'rsi14', 'ret_5')}
    out.update(code=code, name=name, signal_type=strategy, date=str(row['date'])[:10],
               boards=int(row['boards']), board_type='首板' if row['boards'] == 1 else ('连板' if row['boards'] >= 2 else None),
               seal_strength=None, seal_note='日线数据不含封单金额，未启用封单评分',
               announcement_status='unknown', plan=sell_plan(strategy, row))
    out.update(historical_score(row, strategy, policy))
    out['validation_2026'] = policy.get('audit', {}).get(strategy, {}).get('selected_2026')
    out['next_open_scenario'] = policy.get('audit', {}).get(strategy, {}).get('next_open_scenario')
    return out


def strat_limit_up(code, name, row, policy):
    return candidate(code, name, row, 'limit_up', policy)


def strat_oversold(code, name, row, policy, announcements=None):
    if announcements is not None and risk_keyword(announcements):
        return None
    result = candidate(code, name, row, 'oversold', policy)
    if result and announcements is not None:
        result['announcement_status'] = 'provided_texts_checked'
    return result


def rank_candidates(items, limit=10):
    return sorted(items, key=lambda r: (-(r['expected_win_rate'] if r['expected_win_rate'] is not None else -1),
                                         -r['amount'], r['code']))[:limit]
