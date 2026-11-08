#!/usr/bin/env python3
"""收盘任务：按股票增量刷新本地前复权缓存，再离线扫描；仅打印摘要，绝不发送。

默认日期取交易日历最近交易日，--date 可复现历史信号。
同一日期缓存已覆盖时不联网。跨日刷新受影响股票时重取完整前复权序列，
避免把不同复权基准的增量K线拼接。--offline 只扫描现存缓存。
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'modules'), str(ROOT/'stock-analysis')]
import pandas as pd
from modules.stock_screener import run_shortterm
from modules.push_summary import generate_shortterm_summary
from pull_history import atomic_json, fetch_one
import pull_history
import stock_data


def latest_trading_date(today=None):
    day = today or dt.date.today()
    for _ in range(370):
        if stock_data.is_trading_day(dt.datetime.combine(day, dt.time())):
            return day.isoformat()
        day -= dt.timedelta(days=1)
    raise ValueError('交易日历未找到最近交易日')


def refresh_history(as_of, history_dir):
    """仅更新未覆盖目标日期的股票；失败阻止发布，保留上一次结果。"""
    cache = Path(history_dir)
    universe = json.loads((cache/'universe.json').read_text())['stocks']
    checkpoint_path = cache/'checkpoint.json'
    state = json.loads(checkpoint_path.read_text()) if checkpoint_path.exists() else {}
    covered = state.get('range', ['', ''])[1].replace('-', '')
    if covered and covered >= as_of.replace('-', ''):
        return {'updated': 0, 'coverage_end': covered, 'network': False}
    updated = 0
    # fetch_one 原子替换整只股票的前复权序列；不修改已冻结的回测 policy。
    old_dir = pull_history.HIST_DIR
    pull_history.HIST_DIR = str(cache)
    try:
        for stock in universe:
            if stock['code'] in state.get('unavailable', {}):
                continue
            path = cache/(stock['code']+'.parquet')
            if path.exists():
                dates = pd.read_parquet(path, columns=['date'])['date']
                if not dates.empty and str(dates.max())[:10] >= as_of:
                    continue
            code, n, error = fetch_one(stock, beg='20231101', end=as_of.replace('-', ''))
            if error:
                raise RuntimeError(f'{code} 增量刷新失败：{error}；结果未发布')
            state.setdefault('done', {})[code] = n
            updated += 1
        state['range'] = ['20231101', as_of.replace('-', '')]
        atomic_json(checkpoint_path, state)
    finally:
        pull_history.HIST_DIR = old_dir
    return {'updated': updated, 'coverage_end': as_of, 'network': bool(updated)}


def backtest_snapshot():
    """仅选正式报告已有的统计，随每日结果冻结，HTTP请求不读取回测源文件。"""
    results = json.loads((ROOT/'stock-analysis/shortterm_results.json').read_text())
    policy = json.loads((ROOT/'stock-analysis/shortterm_policy.json').read_text())
    rows = []
    names = {'limit_up': '涨停次日', 'oversold': '超跌双信号'}
    for strategy, name in names.items():
        source = results['strategies'][strategy]
        for key, label in (('selected', '2024-01-01～2026-11-06'), ('selected_2026', '2026审计期')):
            rows.append({'signal': name, 'period': label, **source[key]})
        for group, item in policy['calibration'][strategy].items():
            if group == 'all' or not item['n']:
                continue
            rows.append({'signal': name+' / '+{'first':'首板','multi':'连板2+','double':'双信号'}[group],
                         'period': '2024–2025校准期', **item})
    rows.append({'signal': '涨停次日 / 次日开盘买入、再下日退出情景',
                 'period': '2024-01-01～2026-11-06', **results['strategies']['limit_up']['t1_daily_plan']})
    rows.append({'signal': '超跌双信号 / 收盘触发计划',
                 'period': '2024-01-01～2026-11-06', **results['strategies']['oversold']['close_plan']})
    return {'source': 'stock-analysis/shortterm_backtest.md', 'rows': rows,
            'note': '流动性前1500只、前复权日线，存在幸存者和选池前视偏差；未扣费用/滑点，2026参与过滤取舍。信号收盘收益胜率不等于开盘买入交易胜率。'}


def generate(as_of, history_dir=None, output=None, offline=False):
    day = dt.date.fromisoformat(as_of)
    if as_of > dt.date.today().isoformat():
        raise ValueError('不允许未来日期')
    if latest_trading_date(day) != as_of:
        raise ValueError('指定日期休市，请使用最近交易日')
    cache = Path(history_dir or ROOT/'data/history')
    refresh = {'updated': 0, 'network': False, 'mode': 'offline'} if offline else refresh_history(as_of, cache)
    result = run_shortterm(as_of=as_of, history_dir=cache)
    if result['missing_codes']:
        raise RuntimeError(f"缺少{len(result['missing_codes'])}只缓存，结果未发布")
    result.update(generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                  research_only=True, refresh=refresh, backtest=backtest_snapshot())
    result['summary'] = generate_shortterm_summary(result)
    atomic_json(output or ROOT/'data/shortterm_picks.json', result)
    print(result['summary'])
    return result


def self_test():
    """运行真实准入规则的边界用例；无需网络和历史缓存。"""
    from modules.shortterm_rules import strat_limit_up
    row = dict(open=10, close=11, high=11, low=10, volume=100, amount=1e8,
               turnover=5, vol_ratio=2, rsi14=60, ret_5=.1, boards=1,
               is_limit_up=True, one_price=False, date='2026-11-06')
    policy = {'filters': {}}
    assert strat_limit_up('600000', '普通股', row, policy)
    for name, changes in (('*ST样本', {}), ('普通股', {'suspended': True}),
                           ('普通股', {'one_price': True}), ('普通股', {'amount': None})):
        assert strat_limit_up('600000', name, {**row, **changes}, policy) is None
    assert latest_trading_date(dt.date(2026, 11, 8)) == '2026-11-06'
    print('自测通过：ST、停牌、一字板、数据缺失、周末日期')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', help='信号交易日 YYYY-MM-DD；默认最近交易日')
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        self_test()
    else:
        generate(args.date or latest_trading_date(), offline=args.offline)
