#!/usr/bin/env python3
"""东财前复权日线；固定样本池、每批检查点，重复执行可续跑。

python stock-analysis/pull_history.py --batch-size 100 --batches 1
默认只拉一批；--all-stocks 用于固定日期全市场回归的数据补齐。
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime as dt
import json
from pathlib import Path
import time
import threading

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
BASE = str(ROOT / 'stock-analysis')
HIST_DIR = str(ROOT / 'data/history')
CLIST_URL = 'https://push2test.eastmoney.com/api/qt/clist/get'
KLINE_URL = 'https://push2his.eastmoney.com/api/qt/stock/kline/get'
HEADERS = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://data.eastmoney.com/'}
_LOCAL = threading.local()


def http_get(*args, **kwargs):
    # 每线程复用连接，线程间不共享cookies/会话；仍按批限制并发。
    if not hasattr(_LOCAL, 'session'):
        _LOCAL.session = requests.Session()
    return _LOCAL.session.get(*args, **kwargs)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def secid(code):
    return ('1.' if code.startswith(('6', '9')) else '0.') + code


def fetch_stock_list():
    """冻结当前成交额顺序；100/页，检查重复、完整性，避免接口截断误判。"""
    path = ROOT / 'data/history/universe.json'
    if path.exists():
        return json.loads(path.read_text())['stocks']
    stocks = []
    page = 1
    while True:
        r = http_get(CLIST_URL, params={
            'pn': page, 'pz': 100, 'po': 1, 'np': 1, 'fltt': 2, 'invt': 2,
            'fid': 'f6', 'fs': 'm:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23',
            'fields': 'f12,f14,f6,f2,f3,f8,f10,f124',
        }, headers=HEADERS, timeout=25)
        r.raise_for_status()
        d = r.json().get('data') or {}
        diff = d.get('diff') or []
        if not diff:
            raise RuntimeError(f'empty universe page {page}')
        for x in diff:
            stocks.append({'code': x['f12'], 'name': x['f14'], 'amount': x['f6'],
                           'quote_timestamp': x.get('f124')})
        if len({s['code'] for s in stocks}) != len(stocks):
            raise RuntimeError('duplicate universe page')
        print(f'universe page={page} got={len(stocks)}/{d["total"]}', flush=True)
        if len(stocks) >= d['total']:
            break
        page += 1
        time.sleep(.15)
    stocks.sort(key=lambda x: (-float(x['amount']) if isinstance(x['amount'], (int, float)) else 0, x['code']))
    atomic_json(path, {'fetched_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                       'source': CLIST_URL, 'sort': 'current amount descending', 'stocks': stocks})
    return stocks


def fetch_one(stock, beg='20231101', end='20261106'):
    code = stock['code']
    empty_count = 0
    for attempt in range(3):
        try:
            r = http_get(KLINE_URL, params={
                'secid': secid(code), 'klt': '101', 'fqt': '1', 'beg': beg, 'end': end,
                'fields1': 'f1,f2,f3,f4,f5',
                'fields2': 'f51,f52,f53,f54,f55,f56,f57,f59,f61',
            }, headers=HEADERS, timeout=20)
            r.raise_for_status()
            data = r.json().get('data')
            if not data or not data.get('klines'):
                empty_count += 1
                raise ValueError('empty history (not marked complete)')
            rows = []
            for line in data['klines']:
                p = line.split(',')
                rows.append(dict(zip(('date', 'open', 'close', 'high', 'low', 'volume',
                                      'amount', 'pct_chg', 'turnover'),
                                     [p[0]] + [float(v) for v in p[1:]])))
            df = pd.DataFrame(rows).sort_values('date').drop_duplicates('date')
            if (df[['open', 'close', 'high', 'low']] <= 0).any().any():
                raise ValueError('invalid price')
            path = Path(HIST_DIR) / f'{code}.parquet'
            tmp = path.with_suffix('.parquet.tmp')
            df.to_parquet(tmp, index=False)
            tmp.replace(path)
            return code, len(df), None
        except Exception as exc:
            if attempt < 2:
                time.sleep(attempt + 1)
            else:
                return code, 0, ('empty history confirmed (3 HTTP 200 responses)' if empty_count == 3 else str(exc))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--batch-size', type=int, default=100)
    ap.add_argument('--batches', type=int, default=1)
    ap.add_argument('--workers', type=int, choices=range(1, 9), default=4)
    ap.add_argument('--all-stocks', action='store_true')
    ap.add_argument('--retry-unavailable', action='store_true')
    ap.add_argument('--beg', default='20231101')
    ap.add_argument('--end', default='20261106')
    a = ap.parse_args()
    if a.batch_size < 1 or a.batches < 1:
        ap.error('batch-size and batches must be positive')
    Path(HIST_DIR).mkdir(parents=True, exist_ok=True)
    stocks = fetch_stock_list()
    selected = stocks if a.all_stocks else stocks[:1500]
    checkpoint = Path(HIST_DIR) / 'checkpoint.json'
    state = json.loads(checkpoint.read_text()) if checkpoint.exists() else {'range': [a.beg, a.end], 'done': {}, 'errors': {}}
    if state['range'] != [a.beg, a.end]:
        raise ValueError('range differs from checkpoint; use a separate cache, never merge qfq vintages')
    state.setdefault('unavailable', {})
    todo = [s for s in selected if (a.retry_unavailable or s['code'] not in state['unavailable']) and
            (s['code'] not in state['done'] or not (Path(HIST_DIR) / (s['code'] + '.parquet')).exists())]
    for batch in range(a.batches):
        chunk = todo[batch*a.batch_size:(batch+1)*a.batch_size]
        if not chunk:
            break
        with ThreadPoolExecutor(max_workers=a.workers) as pool:
            for fut in as_completed([pool.submit(fetch_one, s, a.beg, a.end) for s in chunk]):
                code, n, error = fut.result()
                if error and error.startswith('empty history confirmed'):
                    state['unavailable'][code] = error
                    state['errors'].pop(code, None)
                elif error:
                    state['errors'][code] = error
                else:
                    state['done'][code] = n
                    state['errors'].pop(code, None)
                    state['unavailable'].pop(code, None)
                atomic_json(checkpoint, state)
        print(f'batch={batch+1} done={len(state["done"])} errors={len(state["errors"])} unavailable={len(state['unavailable'])} target={len(selected)}', flush=True)
        if state['errors']:
            raise RuntimeError('batch has failed downloads; rerun to retry')


if __name__ == '__main__':
    main()
