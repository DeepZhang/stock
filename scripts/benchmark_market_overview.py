"""真实上游分项冷缓存计时，或 HTTP 接口计时；不改动运行中的服务。"""
import argparse
import json
from pathlib import Path
import sys
import time
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'modules'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', help='只计时指定 HTTP JSON 接口')
    parser.add_argument('--runs', type=int, default=2)
    args = parser.parse_args()
    for run in range(args.runs):
        if args.url:
            started = time.perf_counter()
            with urlopen(args.url, timeout=120) as response:
                body = json.load(response)
                print(json.dumps({'run': run + 1, 'status': response.status,
                                  'seconds': round(time.perf_counter()-started, 6),
                                  'body': body}, ensure_ascii=False), flush=True)
        else:
            import market_data
            for name in ('get_index_data', 'get_limit_stats', 'get_market_fund_flow',
                         'get_sector_flow_analysis'):
                market_data._cache.clear()
                started = time.perf_counter()
                body = getattr(market_data, name)()
                print(json.dumps({'run': run + 1, 'function': name,
                                  'seconds': round(time.perf_counter()-started, 6),
                                  'body': body}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
