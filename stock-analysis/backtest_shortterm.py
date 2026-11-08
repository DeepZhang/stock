#!/usr/bin/env python3
"""Phase1 可复现研究：单只计算特征，分段事件缓存；报告不覆盖上轮核查记录。"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from modules.shortterm_rules import features, filter_masks, eligible
from modules.stock_screener import check_breakout_bars, run_shortterm
from pull_history import atomic_json

CACHE = ROOT / 'data/history'
OUT = ROOT / 'stock-analysis'
MARKER = '\n<!-- FORMAL_PHASE1 -->\n'


def stats(values):
    s = pd.Series(values, dtype=float).replace([np.inf, -np.inf], np.nan).dropna()
    n = len(s)
    wins = int((s > 0).sum())
    gain, loss = s[s > 0].mean(), -s[s < 0].mean()
    return {'n': n, 'wins': wins, 'win_rate': 100*wins/n if n else None,
            'mean_pct': float(s.mean()*100) if n else None,
            'payoff_ratio': float(gain/loss) if pd.notna(gain) and pd.notna(loss) and loss > 0 else None}


def close_exit(d, strategy):
    """可复现收盘触发情景：信号收盘为基准，超跌第3日盈利退出否则第5日。
    不声称信号形成后可在同一收盘成交；不是可执行交易回测。
    """
    c = d['close']
    horizon = 1 if strategy == 'limit_up' else 5
    result = c.shift(-horizon)/c-1
    if strategy == 'oversold':
        stop = pd.concat([c*.95, d['low5']], axis=1).max(axis=1)
        alive = pd.Series(True, index=d.index)
        result[:] = np.nan
        # 任一未来日缺失：整个样本不统计（避免末端截尾偏差）。
        complete = c.shift(-5).notna()
        for day in range(1, 6):
            future = c.shift(-day)
            ret = future/c-1
            exit_now = alive & ((future < stop) | ((day == 3) & (ret > 0)) | (day == 5))
            result.loc[exit_now & complete] = ret.loc[exit_now & complete]
            alive &= ~exit_now
    return result


def daily_exit_scenarios(d):
    """OHLC保守情景：同日高低顺序未知时先止损；不假设分批成交。"""
    entry = d['open'].shift(-1)
    high = d['high'].shift(-1)
    low = d['low'].shift(-1)
    close = d['close'].shift(-1)
    hypothetical = close/entry-1
    hypothetical = hypothetical.mask(high >= entry*1.03, .03)
    hypothetical = hypothetical.mask(low <= entry*.97, -.03)
    hypothetical = hypothetical.where(entry.notna())
    both = (high >= entry*1.03) & (low <= entry*.97)
    # 合规持有时间对照：次日开盘进入，再下一交易日才可卖，跳空用开盘。
    sell_open = d['open'].shift(-2)
    sell_high = d['high'].shift(-2)
    sell_low = d['low'].shift(-2)
    compliant = d['close'].shift(-2)/entry-1
    compliant = compliant.mask(sell_high >= entry*1.03, .03)
    compliant = compliant.mask(sell_low <= entry*.97, -.03)
    compliant = compliant.mask(sell_open >= entry*1.03, sell_open/entry-1)
    compliant = compliant.mask(sell_open <= entry*.97, sell_open/entry-1)
    compliant = compliant.where(sell_open.notna())
    return hypothetical, compliant, both


def collect(stocks):
    frames = []
    segments = CACHE / 'events'
    segments.mkdir(exist_ok=True)
    # 特征/收益算法变更自动失效，文件校验避免错用旧样本。
    algorithm = hashlib.sha256((Path(__file__).read_bytes() + (ROOT/'modules/shortterm_rules.py').read_bytes())).hexdigest()
    for ix in range(0, len(stocks), 100):
        chunk = stocks[ix:ix+100]
        files = [CACHE / (s['code']+'.parquet') for s in chunk]
        if any(not p.exists() for p in files):
            raise RuntimeError(f'missing cache in segment {ix}; pull history first')
        fingerprint = hashlib.sha256((algorithm + ''.join(s['code']+str(p.stat().st_mtime_ns) for s,p in zip(chunk,files))).encode()).hexdigest()
        path = segments / f'{ix:04}.parquet'
        meta = path.with_suffix('.json')
        if meta.exists() and json.loads(meta.read_text())['hash'] == fingerprint:
            frames.append(pd.read_parquet(path))
            continue
        events = []
        for stock,p in zip(chunk,files):
            d = features(pd.read_parquet(p), stock['code'])
            d['code'] = stock['code']
            d['name'] = stock['name']
            d['ret_1'] = d['close'].shift(-1)/d['close']-1
            d['ret_3'] = d['close'].shift(-3)/d['close']-1
            d['ret_f5'] = d['close'].shift(-5)/d['close']-1
            d['plan_oversold'] = close_exit(d, 'oversold')
            d['intraday_research'], d['t1_daily_plan'], d['intraday_both'] = daily_exit_scenarios(d)
            # 下日开盘到收盘，仅研究情景（普通A股T+1不能当天卖新买仓位）。
            d['open_close_1'] = d['close'].shift(-1)/d['open'].shift(-1)-1
            for strategy in ('limit_up', 'oversold'):
                mask = d['is_limit_up'] if strategy == 'limit_up' else (d['ret_5'].le(-.08) | d['rsi14'].le(20))
                sub = d[mask & (d.index >= 20) & d['date'].between('2024-01-01','2026-11-06')].copy()
                sub['strategy'] = strategy
                # 仅是当前名称的敏感性分析，不能当作历史ST排除。
                sub['current_name_eligible'] = not ('ST' in stock['name'].upper() or '退' in stock['name'])
                sub['return'] = sub['ret_1'] if strategy == 'limit_up' else sub['ret_f5']
                sub['outcome_date'] = d['date'].shift(-1 if strategy == 'limit_up' else -5).loc[sub.index]
                events.append(sub)
        segment = pd.concat(events, ignore_index=True)
        segment.to_parquet(path, index=False)
        atomic_json(meta, {'hash': fingerprint})
        frames.append(segment)
        print(f'events checkpoint {ix+len(chunk)}/{len(stocks)}', flush=True)
    return pd.concat(frames, ignore_index=True)


def metric_text(s):
    def fmt(v):
        return '—' if v is None else f'{v:.3f}'
    return f"{s['n']} / {s['wins']} / {fmt(s['win_rate'])}% / {fmt(s['mean_pct'])}% / {fmt(s['payoff_ratio'])}"


def compare(d, label, mask, baseline=None):
    before = stats((baseline if baseline is not None else d)['return'])
    after = stats(d.loc[mask, 'return'])
    train = d['date'] <= '2025-12-31'
    # 标签未来收益跨年时不进入校准集，保守移除最后10个自然日。
    train &= d['date'] <= '2025-12-21'
    if 'outcome_date' in d:
        train &= d['outcome_date'] <= '2025-12-31'
    test = d['date'] >= '2026-01-01'
    bt, at = stats(d.loc[train, 'return']), stats(d.loc[train & mask, 'return'])
    bv, av = stats(d.loc[test, 'return']), stats(d.loc[test & mask, 'return'])
    keep = all(a['n'] >= 50 and a['win_rate'] > b['win_rate'] for a,b in ((after,before),(at,bt),(av,bv)))
    return {'filter': label, 'before': before, 'after': after,
            'calibration_before': bt, 'calibration_after': at,
            'validation_before': bv, 'validation_after': av, 'keep': keep}


def backtest():
    universe = json.loads((CACHE/'universe.json').read_text())
    stocks = universe['stocks'][:1500]
    events = collect(stocks)
    import pyarrow.parquet as pq
    lengths = [pq.read_metadata(CACHE/(stock['code']+'.parquet')).num_rows for stock in stocks]
    coverage = {'bars': sum(lengths), 'min_rows': min(lengths), 'max_rows': max(lengths)}
    policy = {'calibration_end': '2025-12-31', 'selection_end': '2026-11-06', 'filters': {}, 'calibration': {},
              'note': '2024–25校准；2026过滤审计，仍有筛选偏差，非严格独立检验'}
    policy['universe_sha256'] = hashlib.sha256((CACHE/'universe.json').read_bytes()).hexdigest()
    results = {'universe_sha256': policy['universe_sha256'], 'event_rows':len(events), 'coverage':coverage, 'universe_n': len(universe['stocks']), 'sample_n':len(stocks),
               'universe_fetched_at':universe['fetched_at'], 'strategies': {}}
    lines = ['# Phase 1 正式回测', '',
             '## 样本、口径与限制', '',
             f"东财当前名单 {len(universe['stocks'])} 只，按抓取时成交额降序冻结前 {len(stocks)} 只；抓取时间 {universe['fetched_at']}。",
             f"共{coverage['bars']}根日线；单只最少{coverage['min_rows']}、最多{coverage['max_rows']}根；信号至少需要21根当时已知日线，与扫描入口一致。",
             '历史区间 2023-11-01～2026-11-06；2023年仅预热，统计信号期2024-01-01～2026-11-06。新上市股票无历史部分不补造。',
             '当前流动性选池有幸存者偏差和成分前视偏差，不能称全市场无偏结果；本轮没有2015–2023训练样本。',
             '前复权收盘价收益；涨停阈值主板9.5%、创业板/科创板19%，沿用analyze_patterns.py；这是代理信号，不鉴定特殊上市日是否真实封板。',
             'RSI Wilder ewm(alpha=1/14, adjust=False)，零平均损失保持旧脚本NaN口径。每个持有期独立剔除未来收益缺失样本。',
             '统计单位为股票×信号日，允许重叠持有，不是独立交易/组合收益；未扣费用、滑点，未处理不可成交和未来停牌导致的退出延迟。',
             '基准为信号收盘至未来收盘的条件收益，不能假设收盘信号生成后还能按该收盘买入。普通A股次日新买仓位不能当天卖出。依据：[上交所交易规则3.1.4–3.1.5](https://www.sse.com.cn/lawandrules/sselawsrules2025/trade/universal/c/c_20260424_10816492.shtml)。',
             '日线无历史ST/退市/公告/封单状态：当前名称敏感性单列，不能冒充历史硬过滤；封单与公告过滤不参与收益选择，预期胜率不包含这些风险。',
             '候选阈值预先固定；保留须全期、2024–25校准期及2026审计期均n≥50且胜率提升，再验证组合。2026参与取舍，因此属于探索性检验，仍需新数据复核。',
             '复现：`.venv/bin/python stock-analysis/pull_history.py --batch-size 100 --batches 1` 可续跑一批；前1500只齐全后运行 `.venv/bin/python stock-analysis/backtest_shortterm.py`。全市场缓存补齐用 `--all-stocks --batch-size 100 --batches 1`，随后回归用 `backtest_shortterm.py --regression`。',
             '表中：n / 胜场 / 胜率 / 平均收益 / 盈亏比（平均正收益÷平均负收益绝对值）；零收益不算胜场。', '']
    for strategy in ('limit_up','oversold'):
        raw = events[events['strategy']==strategy].copy()
        base = raw[~raw['one_price']] if strategy=='limit_up' else raw
        # 非ST/停牌等是线上候选准入约束，不借今日状态伪装历史统计。
        report = {'raw':stats(raw['return']), 'base':stats(base['return']), 'filters':[]}
        lines += [f'## {strategy}', '', f"原始信号：{metric_text(report['raw'])}。",
                  f"{'剔除信号日一字板后' if strategy=='limit_up' else 'OR副策略基线'}：{metric_text(report['base'])}。", '']
        if strategy=='limit_up':
            report['board_groups'] = {label: stats(base.loc[mask,'return']) for label,mask in
                                      (('首板',base['boards']==1),('连板2+',base['boards']>=2),('高位3+',base['boards']>=3))}
            for label,value in report['board_groups'].items():
                lines.append(f"- 排除一字板后{label}：{metric_text(value)}")
            lines.append('')
            op = compare(raw,'排除一字板',~raw['one_price'])
            report['one_price'] = op
            lines += [f"一字板独立对照：{metric_text(op['before'])} → {metric_text(op['after'])}。此项为可参与性约束，收益不提升也不把买不进的板纳入候选。", '']
        lines += ['|过滤器|全期前|全期后|2024–25前→后胜率(n)|2026前→后胜率(n)|结论|', '|---|---|---|---|---|---|']
        retained=[]
        current=base
        for key, mask in filter_masks(base,strategy).items():
            r=compare(base,key,mask)
            # 独立收益提升之外，逐项组合也须三期通过。
            combo_mask=filter_masks(current,strategy)[key]
            combo=compare(current,key,combo_mask)
            r['combination'] = combo
            r['enabled'] = r['keep'] and combo['keep']
            reasons=[]
            for prefix, item in (('独立',r),('组合',combo)):
                for period,a,b in (('全期',item['after'],item['before']),
                                   ('校准',item['calibration_after'],item['calibration_before']),
                                   ('2026',item['validation_after'],item['validation_before'])):
                    if a['n'] < 50:
                        reasons.append(prefix+period+'n<50')
                    elif a['win_rate'] <= b['win_rate']:
                        reasons.append(prefix+period+'胜率未提升')
            r['reason'] = '保留' if r['enabled'] else '；'.join(reasons)
            if r['enabled']:
                retained.append(key)
                current=current[combo_mask]
            report['filters'].append(r)
            def brief(a,b):
                return f"{a['win_rate']:.2f}({a['n']})→{b['win_rate']:.2f}({b['n']})" if a['n'] and b['n'] else '样本不足'
            lines.append(f"|{key}|{metric_text(r['before'])}|{metric_text(r['after'])}|{brief(r['calibration_before'],r['calibration_after'])}|{brief(r['validation_before'],r['validation_after'])}|{r['reason']}|")
        policy['filters'][strategy]=retained
        cal=current[(current['date'] <= '2025-12-21') & (current['outcome_date'] <= '2025-12-31')]
        groups = {'all': cal}
        if strategy=='limit_up':
            groups.update(first=cal[cal['boards']==1],multi=cal[cal['boards']>=2])
        else:
            groups.update(double=cal[cal['double_signal']],single=cal[~cal['double_signal']])
        policy['calibration'][strategy]={k:stats(v['return']) for k,v in groups.items()}
        report['selected'] = stats(current['return'])
        report['selected_2026'] = stats(current.loc[current['date']>='2026-01-01','return'])
        report['years'] = {year:stats(current.loc[current['date'].str.startswith(year),'return']) for year in ('2024','2025','2026')}
        report['without_202409'] = stats(current.loc[~current['date'].str.startswith('2024-09'),'return'])
        report['current_name_sensitivity']=stats(current.loc[current['current_name_eligible'],'return'])
        report['horizons'] = {h:stats(current[h]) for h in ('ret_1','ret_3','ret_f5','open_close_1')}
        report['intraday_research'] = stats(current['intraday_research'])
        report['t1_daily_plan'] = stats(current['t1_daily_plan'])
        report['intraday_both_n'] = int(current['intraday_both'].sum())
        report['close_plan'] = stats(current['ret_1'] if strategy=='limit_up' else current['plan_oversold'])
        policy.setdefault('audit', {})[strategy] = {'selected_2026': report['selected_2026'],
                                                  'next_open_scenario': report['t1_daily_plan'] if strategy=='limit_up' else None}
        lines += ['', f"最终保留：{retained or '无收益阈值过滤'}；组合结果：{metric_text(report['selected'])}。",
                  f"2026组合：{metric_text(report['selected_2026'])}。",
                  f"再按今日名称去ST/退市的敏感性结果：{metric_text(report['current_name_sensitivity'])}（非历史状态回测）。", '',
                  '### 持有期与卖出情景', '', '|情景|n / 胜场 / 胜率 / 平均收益 / 盈亏比|', '|---|---|']
        for h,v in report['horizons'].items():
            lines.append(f'|{h}|{metric_text(v)}|')
        if strategy=='limit_up':
            lines += [f"|次日开盘买入同日±3%情景（T+1不可执行）|{metric_text(report['intraday_research'])}|",
                      f"|次日开盘买入、再下日±3%/尾盘退出|{metric_text(report['t1_daily_plan'])}|"]
        lines += [f"|收盘触发计划|{metric_text(report['close_plan'])}|", '',
                  *([f"涨停日线±3%情景：同根K线双触发{report['intraday_both_n']}个，保守先止损。不模拟分批；T+1版本跳空按开盘越过阈值的价格退出，其他阈值按理想价成交，未含成交限制或费用。"] if strategy=='limit_up' else []),
                  'ret_1/ret_3/ret_f5分别为信号收盘至未来1/3/5个有日线交易日收盘。open_close_1为下日开盘到收盘研究情景，不能当作新买仓位的可执行收益。',
                  '涨停的+3%/-3%已做保守OHLC情景对照，但盘中分批与高低先后没有分钟数据验证；候选中标为研究纪律，不声称这些阈值提升胜率。',
                  '超跌收盘计划：以信号收盘为参考，跌破max(参考价×0.95,过去5日最低价)于该日收盘退出；否则第3日盈利退出，未盈利持至第5日。需要完整5日窗口；实际入场价不同，盘中触发/成交未覆盖。', '']
        lines += ['### 年份与极端行情敏感性', '']
        for year,value in report['years'].items():
            lines.append(f"- {year}：{metric_text(value)}")
        lines += [f"- 去掉2024年9月信号：{metric_text(report['without_202409'])}（只排除信号月，不声称完全消除后续行情影响）。", '']
        results['strategies'][strategy]=report
    lines += ['## 缺失数据与硬约束的处理', '',
              '- 非ST/退市/停牌、正成交量和成交额：候选准入执行；无法恢复历史状态的条件不宣称已验证胜率。信号日一字板拒绝；次日一字板仍可能无法买入。',
              '- 成交额≥1亿、换手区间及量比上限：以表中结果决定收益过滤取舍；未通过的阈值不强留，缺失换手时不通过已启用的换手条件。',
              '- 封单强度：历史日线没有封单，不以量比替代；未测、未启用，输出null。',
              '- 公开利空关键词：实现针对调用方截至信号日已公开文本的规则；无公告源时输出unknown。没有历史语料，因此未启用为全市场收益过滤，不报告伪造提升。',
              '- 首板/连板分组胜率见policy校准表；组n<50回退整体胜率，同胜率按成交额降序；不使用未来2026收益评分。Top10是候选数量上限，非收益保证。', '']
    for strategy, groups in policy['calibration'].items():
        for key,value in groups.items():
            lines.append(f"- {strategy}/{key} 校准：{metric_text(value)}")
    lines += ['## 决策结论', '',
              '收益过滤保留换手1%–20%、量比≤3（涨停）与RSI≤20且5日跌幅≥8%（超跌）；其余收益条件按表砍掉。',
              '主策略的收盘信号统计优势不能转化为已验证的开盘入场优势：次日开盘到收盘及遵守T+1的退出情景均须看上表，禁止将信号胜率当作交易胜率。',
              '本轮交付研究选股引擎；未证明一套可执行的高胜率净收益交易方案，所有候选标记research_only。公告、封单和历史风险状态缺失不作假验证。', '']
    atomic_json(OUT/'shortterm_policy.json',policy)
    atomic_json(OUT/'shortterm_results.json',results)
    report_path=OUT/'shortterm_backtest.md'
    old=report_path.read_text().split(MARKER)[0]
    if not old.startswith('> 续跑说明'):
        old='> 续跑说明：开头为上轮核查原始记录，已保留；本轮正式结果见下方“Phase 1 正式回测”。\n\n'+old
    report_path.write_text(old+MARKER+'\n'.join(lines)+'\n')
    print(json.dumps({k:v['selected'] for k,v in results['strategies'].items()},ensure_ascii=False),flush=True)


def regression():
    universe=json.loads((CACHE/'universe.json').read_text())['stocks']
    result=run_shortterm()
    if result['missing_codes']:
        raise RuntimeError(f'full-universe regression missing {len(result["missing_codes"])} histories')
    breakout=[]
    valid=0
    for stock in universe:
        path=CACHE/(stock['code']+'.parquet')
        if not path.exists():
            continue
        d=pd.read_parquet(path)
        d=d[d['date']<='2026-11-06']
        if len(d)<60 or d.iloc[-1]['date']!='2026-11-06':
            continue
        valid+=1
        # 旧策略原来排除创业板/科创板，回归保留该约束；不以全市场代替板块池而不披露。
        if stock['code'].startswith(('30','688')):
            continue
        item=check_breakout_bars(stock['code'],stock['name'],d.tail(120).to_dict('records'))
        if item:
            breakout.append(item)
    result['breakout_count']=len(breakout)
    result['breakout']=breakout
    result['universe_n']=len(universe)
    result['valid_asof_n']=valid
    atomic_json(CACHE/'regression_20261106.json',result)
    lines=['', '## 2026-11-06 固定日期全市场回归', '',
           f"冻结名单{len(universe)}项，成功缓存{len(universe)-len(result['unavailable_codes'])}只；{len(result['unavailable_codes'])}项连续3次HTTP200无历史，单列不可用，未记为成功或凭空补数据；当日有日线且至少60条历史{valid}只。",
           '列表尾部含PT金田A、神城A退等退市残留；空历史不等于网络失败，检查点unavailable可用--retry-unavailable重试。全市场扫描覆盖所有可用缓存，非5563只均在市。',
           f"全市场可用历史扫描：超跌{result['counts']['oversold']}，涨停{result['counts']['limit_up']}，旧突破{len(breakout)}；新策略各取Top10。",
           f"逐步计数（原始信号→当前名称/可交易性准入→排除一字板；最后收益过滤见上述候选数）：{result['stages']}。",
           'v2参考33/14/30不能视为本仓库基线：本仓库原无超跌/涨停；本轮有回测过滤与非ST/一字板约束；旧突破用东财前复权日线而旧版在线用新浪120条非复权数据，且本轮全市场主板池与旧板块池不同。上述数据源/股票池/规则差异均会影响数量，不改阈值凑数。',
           'run_screen仍为旧突破列表接口；此处全市场缓存回归调用相同旧突破规则入口，不冒称线上板块池实时结果。/api/prediction不存在；/api/shortterm及新页面留Phase3。', '']
    p=OUT/'shortterm_backtest.md'
    p.write_text(p.read_text().split('\n## 2026-11-06 固定日期全市场回归')[0]+'\n'.join(lines))
    print(json.dumps({k:result[k] for k in ('counts','breakout_count','universe_n','valid_asof_n')},ensure_ascii=False))


if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--regression',action='store_true')
    a=ap.parse_args()
    regression() if a.regression else backtest()
