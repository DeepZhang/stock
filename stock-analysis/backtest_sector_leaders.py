"""板块龙头强势信号回测（研究用，不接页面与 API）。
板块：modules/stock_data.py 的 SECTOR_MAP，即选股器用的 23 个资金流向板块
（东财 BK 代码），成分股用选股器同款东财 clist 接口拉取并冻结在
sector_members.json。不用 sector_api/sector_leader 的东财行业板块（m:90 t:2）。
样本（与 Phase 1 一致）：东财 5563 只按成交额降序冻结前 1500 只
（data/history/universe.json）；盒内 data/history/*.parquet 前复权日线
（fqt=1），2023-11-01~2026-11-06，2023 年仅预热，统计期 2024-01-01~2026-11-06。
口径：信号日收盘买入，持有 5 个交易日，收盘价上涨算胜；统计单位为股票 x 信号日，
允许重复持有，不含费用、滑点与停牌退出。基线（预先锁定）：5 日涨幅 >= 5%。
过滤器逐个与基线比胜率（全期、2024-25 校准、2026 验证），仅三期胜率都高于
基线且每期 n>=50 时保留，否则砍掉。上榜门槛：组合信号全期 n>=20；每板块按
胜率、n、平均收益降序取 Top 3。用法见 README 式说明：先 --fetch-members，再运行。
"""
import argparse, hashlib, json, statistics, sys
from datetime import datetime, timezone
from pathlib import Path
import numpy as np, pandas as pd, requests
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'modules'))
from stock_data import SECTOR_MAP
from stock_screener import CLIST_URL
HIST_DIR = ROOT / 'data' / 'history'
OUT_DIR = ROOT / 'stock-analysis'
MEMBERS_FILE = OUT_DIR / 'sector_members.json'
RESULTS_FILE = OUT_DIR / 'sector_leaders_results.json'
POLICY_FILE = OUT_DIR / 'sector_leaders_policy.json'
START = pd.Timestamp('2024-01-01'); END = pd.Timestamp('2026-11-06')
CAL_END = pd.Timestamp('2025-12-31'); VAL_START = pd.Timestamp('2026-01-01')
BASE_RET5 = 0.05; MIN_SIGNALS = 20; MIN_FILTER_N = 50; HOLD = 5
PERIODS = {'full': (START, END), 'calibration': (START, CAL_END), 'validation': (VAL_START, END)}
# 候选过滤器（阈值在统计前已锁定）。判定函数输入特征表，返回布尔列。
FILTERS = {
    'vol_ratio_ge_1_5': ('量比>=1.5', lambda d: d['vol_ratio'] >= 1.5),
    'vol_ratio_le_3': ('量比<=3', lambda d: d['vol_ratio'] <= 3),
    'ma_bull': ('均线多头 MA5>MA10>MA20 且收盘>MA5',
                lambda d: (d['ma5'] > d['ma10']) & (d['ma10'] > d['ma20']) & (d['close'] > d['ma5'])),
    'rs_sector_ge_5': ('相对强度：20日涨幅高于同板块中位数 5pp',
                     lambda d: (d['ret20'] - d['rs_sector_median']) >= 0.05),
    'ret20_ge_10': ('20日涨幅>=10%', lambda d: d['ret20'] >= 0.10),
    'turnover_1_20': ('换手率 1%~20%', lambda d: (d['turnover'] >= 1) & (d['turnover'] <= 20)),
    'amount_ge_100m': ('成交额>=1亿元', lambda d: d['amount'] >= 100_000_000),
    'ret5_ge_10': ('5日涨幅>=10%', lambda d: d['ret5'] >= 0.10),
}

def summarise(rets):
    """与 Phase 1 的 stats() 同口径：n/胜场/胜率/平均收益/盈亏比。"""
    s = pd.Series(list(rets), dtype=float).dropna()
    n = int(len(s))
    if n == 0:
        return {'n': 0, 'wins': 0, 'win_rate': None, 'mean_ret': None, 'payoff': None}
    wins = int((s > 0).sum())
    gain = float(s[s > 0].mean()) if wins else 0.0
    loss = float(-s[s <= 0].mean()) if n - wins else 0.0
    payoff = (gain / loss) if loss else None
    return {'n': n, 'wins': wins, 'win_rate': wins / n,
            'mean_ret': float(s.mean()), 'payoff': payoff}

def add_features(df):
    """单只票的日线特征。量比口径与 modules/shortterm_rules.features 一致。"""
    d = df.sort_values('date').drop_duplicates('date').reset_index(drop=True).copy()
    d['date'] = pd.to_datetime(d['date'])
    c = d['close'].astype(float)
    d['ret5'] = c.pct_change(5)
    d['ret20'] = c.pct_change(20)
    d['ma5'] = c.rolling(5).mean(); d['ma10'] = c.rolling(10).mean()
    d['ma20'] = c.rolling(20).mean(); d['ma60'] = c.rolling(60).mean()
    d['vol_ratio'] = d['volume'].astype(float) / d['volume'].astype(float).rolling(20).mean().shift(1)
    d['ret_fwd5'] = c.shift(-HOLD) / c - 1
    d['has_fwd5'] = c.shift(-HOLD).notna()
    d['pos'] = np.arange(len(d))
    return d

def fetch_members():
    """拉取 23 个板块的成分股（选股器同款接口），不剔除 30/688 板块。
    成分股以拉取当日为准并冻结存档；回测样本仍与 Phase 1 一致只取前 1500 只。"""
    out = {'fetched_at': datetime.now(timezone.utc).isoformat(),
           'source': 'push2.eastmoney.com/api/qt/clist/get fs=b:BKxxxx fields=f12,f14',
           'note': 'SECTOR_MAP from modules/stock_data.py; members frozen as-of fetch date',
           'sectors': {}}
    for name, bk in SECTOR_MAP.items():
        members, pn = [], 1
        while True:
            params = {'pn': str(pn), 'pz': '200', 'po': '1', 'np': '1',
                      'fltt': '2', 'invt': '2', 'fid': 'f12',
                      'fs': f'b:{bk}', 'fields': 'f12,f14'}
            r = requests.get(CLIST_URL, params=params,
                             headers={'User-Agent': 'Mozilla/5.0'}, timeout=30)
            r.raise_for_status()
            data = r.json().get('data') or {}
            diff = data.get('diff') or []
            for row in diff:
                members.append({'code': row['f12'], 'name': row['f14']})
            total = int(data.get('total') or 0)
            if len(members) >= total or not diff:
                break
            pn += 1
        out['sectors'][name] = {'bk': bk, 'total': total, 'members': members}
        print(f'{name} {bk}: {len(members)}/{total}', flush=True)
    MEMBERS_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    return out

def load_universe():
    u = json.loads((HIST_DIR / 'universe.json').read_text(encoding='utf-8'))
    stocks = u['stocks'][:1500]
    names = {s['code']: s.get('name', '') for s in stocks}
    return u, [s['code'] for s in stocks], names

def load_features(codes):
    """返回 {code: 特征表}；无历史文件的票记入 missing。"""
    feats, missing, total_bars = {}, [], 0
    for code in codes:
        p = HIST_DIR / f'{code}.parquet'
        if not p.exists():
            missing.append(code); continue
        d = add_features(pd.read_parquet(p))
        feats[code] = d; total_bars += len(d)
    return feats, missing, total_bars

def sector_median_ret20(feats, members):
    """每板块每日的成分股 20 日涨幅中位数（仅限样本内成分股），用于相对强度。"""
    med = {}
    for name, codes in members.items():
        series = [feats[c].set_index('date')['ret20'] for c in codes if c in feats]
        if series:
            med[name] = pd.concat(series, axis=1, sort=False).median(axis=1)
    return med

def base_mask(d):
    """基线强势信号：5日涨幅>=5%，当日已知日线>=21根、OHLCV>0、未来5日收盘存在。"""
    ok = (d['pos'] >= 20) & d['has_fwd5'] & (d['ret5'] >= BASE_RET5)
    ok &= (d['date'] >= START) & (d['date'] <= END)
    ok &= (d[['open', 'high', 'low', 'close', 'volume']] > 0).all(axis=1)
    return ok.fillna(False)

def prepare_rs(feats, medians, code_sectors):
    """给每只票挂相对强度中位数。多板块票取各板块中位数的最小值，即相对
    最强的那个板块口径（ret20-中位数最大）；单板块分榜时再用该板块自己的中位数。"""
    for code, d in feats.items():
        secs = code_sectors.get(code) or []
        if not secs:
            d['rs_sector_median'] = np.nan; continue
        cols = [medians[s].reindex(d['date']) for s in secs if s in medians]
        d['rs_sector_median'] = pd.concat(cols, axis=1, sort=False).min(axis=1).values if cols else np.nan

def filter_masks(d):
    base = base_mask(d)
    out = {}
    for k, (_, fn) in FILTERS.items():
        try:
            out[k] = (base & fn(d).fillna(False)).fillna(False)
        except Exception:
            out[k] = base & False
    return base, out

def aggregate(feats, get_mask):
    """get_mask(d)->布尔列；返回分时期汇总胜率。"""
    out = {p: [] for p in PERIODS}
    for d in feats.values():
        m = get_mask(d)
        for p, (lo, hi) in PERIODS.items():
            sel = m & (d['date'] >= lo) & (d['date'] <= hi)
            if sel.any():
                out[p].append(d.loc[sel, 'ret_fwd5'].values)
    res = {}
    for p, parts in out.items():
        arr = np.concatenate(parts) if parts else np.array([], dtype=float)
        res[p] = summarise(arr)
    return res

def decide_kept(base_stats, filter_stats):
    """保留规则（预先锁定）：全期、校准期、验证期胜率都高于基线，且每期 n>=50。"""
    kept, table = [], []
    for k, (label, _) in FILTERS.items():
        st = filter_stats.get(k)
        if st is None:
            continue
        ok = all(st[p]['win_rate'] is not None and st[p]['win_rate'] > base_stats[p]['win_rate']
                 and st[p]['n'] >= MIN_FILTER_N for p in PERIODS)
        table.append({'name': k, 'label': label, 'stats': st, 'kept': bool(ok)})
        if ok:
            kept.append(k)
    return kept, table

def combo_mask(d, kept):
    base, fmask = filter_masks(d)
    m = base.copy()
    for k in kept:
        m &= fmask[k]
    return m

def stock_combo_stats(d, kept, sector_median=None):
    """单只票组合信号统计。sector_median 给定时用该板块自己的相对强度中位数。"""
    if sector_median is not None:
        d = d.copy()
        d['rs_sector_median'] = sector_median.reindex(d['date']).values
    m = combo_mask(d, kept)
    st = period_stats(d, m)
    sig_dates = d.loc[m, 'date']
    return {'full': st['full'], 'validation': st['validation'],
            'last_signal': (sig_dates.max().strftime('%Y-%m-%d') if len(sig_dates) else None),
            'signals_2026': int(((sig_dates >= VAL_START)).sum()),
            'signals_on_last_day': bool((sig_dates == END).any())}

def rank_top3(rows):
    """按胜率、n、平均收益降序；胜率缺失（n=0）不入榜。"""
    rows = [r for r in rows if r['full']['n'] >= MIN_SIGNALS and r['full']['win_rate'] is not None]
    rows.sort(key=lambda r: (r['full']['win_rate'], r['full']['n'], r['full']['mean_ret'] or 0),
              reverse=True)
    return rows[:3]

def rank_sectors(feats, medians, members, kept, names):
    """members: {板块: [样本内代码]}。返回 {板块: {'combo': 分期汇总, 'top3': [...]}}。"""
    out = {}
    for sec, codes in members.items():
        rows, combo_rets = [], {p: [] for p in PERIODS}
        for code in codes:
            if code not in feats:
                continue
            d = feats[code]
            s = stock_combo_stats(d, kept, medians.get(sec))
            s.update({'code': code, 'name': names.get(code, ''),
                      'excluded_live': code.startswith(('30', '688'))})
            rows.append(s)
        # 板块组合汇总：用各票在本板块口径下的组合信号逐期汇总。
        for code in codes:
            if code not in feats or sec not in medians:
                continue
            d = feats[code].copy()
            d['rs_sector_median'] = medians[sec].reindex(d['date']).values
            m = combo_mask(d, kept)
            for p, (lo, hi) in PERIODS.items():
                sel = m & (d['date'] >= lo) & (d['date'] <= hi)
                if sel.any():
                    combo_rets[p].append(d.loc[sel, 'ret_fwd5'].values)
        combo = {p: summarise(np.concatenate(v) if v else np.array([], dtype=float))
                 for p, v in combo_rets.items()}
        out[sec] = {'combo': combo, 'top3': rank_top3(rows), 'candidates': len(rows)}
    return out

def period_stats(d, mask):
    out = {}
    for name, (lo, hi) in PERIODS.items():
        m = mask & (d['date'] >= lo) & (d['date'] <= hi)
        out[name] = summarise(d.loc[m, 'ret_fwd5'])
    return out

def _filter_ok(name, ctx):
    if name == 'vol_ratio_ge_1_5':
        return ctx['vr'] is not None and ctx['vr'] >= 1.5
    if name == 'vol_ratio_le_3':
        return ctx['vr'] is not None and ctx['vr'] <= 3
    if name == 'ma_bull':
        return (ctx['ma5'] is not None and ctx['ma5'] > ctx['ma10'] > ctx['ma20']
                and ctx['close'] > ctx['ma5'])
    if name == 'rs_sector_ge_5':
        return ctx['rs'] is not None and ctx['rs'] >= 0.05
    if name == 'ret20_ge_10':
        return ctx['ret20'] is not None and ctx['ret20'] >= 0.10
    if name == 'turnover_1_20':
        return ctx['turnover'] is not None and 1 <= ctx['turnover'] <= 20
    if name == 'amount_ge_100m':
        return ctx['amount'] is not None and ctx['amount'] >= 100_000_000
    if name == 'ret5_ge_10':
        return ctx['ret5'] is not None and ctx['ret5'] >= 0.10
    raise KeyError(name)

def _sector_median_plain(feats, codes):
    """纯循环算每板块每日成分股 20 日涨幅中位数（与 pandas 口径独立重算）。"""
    by_date = {}
    for code in codes:
        d = feats.get(code)
        if d is None:
            continue
        closes = list(d['close'].astype(float)); dates = list(d['date'])
        for i in range(20, len(d)):
            by_date.setdefault(dates[i], []).append(closes[i] / closes[i - 20] - 1)
    return {dt: statistics.median(v) for dt, v in by_date.items() if v}

def verify(feats, members, kept, sector_results):
    """独立重算：纯循环逐根日线重算组合信号，与主流程的 Top3 及板块汇总逐项比对。"""
    mismatches, checked = [], 0
    for sec, codes in members.items():
        med = _sector_median_plain(feats, codes)
        totals = {p: [0, 0, 0.0] for p in PERIODS}  # n, wins, sum_ret
        plain = {}
        for code in codes:
            d = feats.get(code)
            if d is None:
                continue
            checked += 1
            n = len(d)
            closes = list(d['close'].astype(float)); vols = list(d['volume'].astype(float))
            opens = list(d['open'].astype(float)); highs = list(d['high'].astype(float))
            lows = list(d['low'].astype(float)); amounts = list(d['amount'].astype(float))
            turns = list(d['turnover'].astype(float)); dates = list(d['date'])
            cnt = {p: [0, 0, 0.0] for p in PERIODS}
            for i in range(20, n - HOLD):
                dt = dates[i]
                if dt < START or dt > END:
                    continue
                if not (opens[i] > 0 and highs[i] > 0 and lows[i] > 0 and closes[i] > 0 and vols[i] > 0):
                    continue
                ret5 = closes[i] / closes[i - 5] - 1
                if ret5 < BASE_RET5:
                    continue
                ret20 = closes[i] / closes[i - 20] - 1
                ma5 = sum(closes[i - 4:i + 1]) / 5; ma10 = sum(closes[i - 9:i + 1]) / 10
                ma20 = sum(closes[i - 19:i + 1]) / 20
                vr = vols[i] / (sum(vols[i - 20:i]) / 20) if sum(vols[i - 20:i]) else None
                sec_med = med.get(dt)
                rs = (ret20 - sec_med) if sec_med is not None else None
                ctx = {'vr': vr, 'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'close': closes[i],
                       'rs': rs, 'ret20': ret20, 'turnover': turns[i], 'amount': amounts[i], 'ret5': ret5}
                if not all(_filter_ok(k, ctx) for k in kept):
                    continue
                fwd = closes[i + HOLD] / closes[i] - 1
                for p, (lo, hi) in PERIODS.items():
                    if lo <= dt <= hi:
                        cnt[p][0] += 1; cnt[p][1] += int(fwd > 0); cnt[p][2] += fwd
            plain[code] = cnt
            for p in PERIODS:
                totals[p][0] += cnt[p][0]; totals[p][1] += cnt[p][1]; totals[p][2] += cnt[p][2]
        res = sector_results[sec]
        for p in PERIODS:
            st = res['combo'][p]
            n, w, s = totals[p]
            mean = (s / n) if n else None
            if st['n'] != n or st['wins'] != w or (mean is None) != (st['mean_ret'] is None) or (mean is not None and abs(mean - st['mean_ret']) > 1e-9):
                mismatches.append(f'{sec} combo {p}: main={st} plain n={n} wins={w} mean={mean}')
        for row in res['top3']:
            cnt = plain.get(row['code'], {p: [0, 0, 0.0] for p in PERIODS})['full']
            n, w, s = cnt
            mean = (s / n) if n else None
            if row['full']['n'] != n or row['full']['wins'] != w or (mean is not None and abs(mean - row['full']['mean_ret']) > 1e-9):
                mismatches.append(f"{sec} {row['code']}: main={row['full']} plain n={n} wins={w} mean={mean}")
    return {'ok': not mismatches, 'checked_stocks': checked, 'mismatches': mismatches}

def run(verify_flag=False):
    uni, codes, names = load_universe()
    sample_n = len(codes)
    excluded_st = [c for c in codes if 'ST' in names.get(c, '') or '退' in names.get(c, '')]
    codes = [c for c in codes if c not in set(excluded_st)]
    members_doc = json.loads(MEMBERS_FILE.read_text(encoding='utf-8'))
    members = {}
    code_sectors = {}
    for sec, doc in members_doc['sectors'].items():
        inc = [m['code'] for m in doc['members'] if m['code'] in set(codes)]
        members[sec] = inc
        for c in inc:
            code_sectors.setdefault(c, []).append(sec)
    feats, missing, total_bars = load_features(codes)
    excluded_bars = sum(len(pd.read_parquet(HIST_DIR / f'{c}.parquet'))
                        for c in excluded_st if (HIST_DIR / f'{c}.parquet').exists())
    medians = sector_median_ret20(feats, members)
    prepare_rs(feats, medians, code_sectors)
    base_stats = aggregate(feats, lambda d: filter_masks(d)[0])
    filter_stats = {k: aggregate(feats, lambda d, k=k: filter_masks(d)[1][k]) for k in FILTERS}
    kept, table = decide_kept(base_stats, filter_stats)
    combo_stats = aggregate(feats, lambda d: combo_mask(d, kept))
    sector_results = rank_sectors(feats, medians, members, kept, names)
    result = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'universe': {'fetched_at': uni.get('fetched_at'), 'total_listed': len(uni['stocks']),
                     'sample': sample_n, 'sample_after_st_exclusion': len(codes),
                     'sha256': hashlib.sha256((HIST_DIR / 'universe.json').read_bytes()).hexdigest()},
        'members_fetched_at': members_doc.get('fetched_at'),
        'excluded_st_by_current_name': excluded_st,
        'history': {'start': '2023-11-01', 'end': '2026-11-06', 'stats_period': '2024-01-01~2026-11-06',
                    'stocks_with_history': len(feats), 'missing_history': missing,
                    'total_bars': total_bars, 'excluded_st_bars': excluded_bars,
                    'adjust': 'forward-adjusted fqt=1'},
        'base': {'label': '5日涨幅>=5%', 'stats': base_stats},
        'filters': table, 'kept_filters': kept, 'combo': combo_stats,
        'sectors': sector_results,
    }
    verify_res = None
    if verify_flag:
        verify_res = verify(feats, members, kept, sector_results)
        result['verify'] = verify_res
        print('VERIFY', 'OK' if verify_res['ok'] else 'FAIL', 'checked_stocks=', verify_res['checked_stocks'])
        for m in verify_res['mismatches'][:20]:
            print('  MISMATCH', m)
    RESULTS_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    POLICY_FILE.write_text(json.dumps({
        'base': 'ret5>=5%', 'kept_filters': kept,
        'filters': {k: FILTERS[k][0] for k in FILTERS},
        'min_signals_for_ranking': MIN_SIGNALS, 'hold_days': HOLD,
        'ranking': 'win_rate desc, n desc, mean_ret desc',
        'note': 'research only; not wired into /api/screen or /api/prediction',
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    return result, verify_res

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--fetch-members', action='store_true')
    ap.add_argument('--verify', action='store_true')
    args = ap.parse_args()
    if args.fetch_members:
        fetch_members()
        return 0
    result, verify_res = run(args.verify)
    b = result['base']['stats']
    print(f"BASE 5日涨幅>=5%: full n={b['full']['n']} win={b['full']['win_rate']:.4f} mean={b['full']['mean_ret']:.4f}")
    for row in result['filters']:
        s = row['stats']
        print(f"FILTER {row['name']}: full n={s['full']['n']} win={s['full']['win_rate']} "
              f"cal={s['calibration']['win_rate']} val={s['validation']['win_rate']} kept={row['kept']}")
    print('KEPT:', result['kept_filters'])
    c = result['combo']
    print(f"COMBO: full n={c['full']['n']} win={c['full']['win_rate']:.4f} mean={c['full']['mean_ret']:.4f}")
    for sec, sr in result['sectors'].items():
        tops = '; '.join(f"{t['name']}{t['code']} {t['full']['win_rate']:.2%}({t['full']['n']})" for t in sr['top3'])
        print(f'{sec}: {tops}')
    if verify_res is not None and not verify_res['ok']:
        return 1
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
