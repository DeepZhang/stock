#!/usr/bin/env python3
"""回测数字自洽检查：独立从事件重算最终策略，并检查所有统计分母。"""
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from modules.shortterm_rules import filter_masks


def check(node):
    count=0
    if isinstance(node,dict):
        if {'n','wins','win_rate','mean_pct','payoff_ratio'} <= node.keys():
            n,wins=node['n'],node['wins']
            assert 0<=wins<=n
            if n:
                assert np.isclose(node['win_rate'],100*wins/n)
                assert node['mean_pct'] is not None
            else:
                assert node['win_rate'] is None and node['mean_pct'] is None
            count+=1
        for value in node.values():
            count+=check(value)
    elif isinstance(node,list):
        count+=sum(check(v) for v in node)
    return count


def main():
    result=json.loads((ROOT/'stock-analysis/shortterm_results.json').read_text())
    policy=json.loads((ROOT/'stock-analysis/shortterm_policy.json').read_text())
    number=check(result)+check(policy)
    events=pd.concat([pd.read_parquet(p) for p in sorted((ROOT/'data/history/events').glob('*.parquet'))],ignore_index=True)
    assert len(events)==result['event_rows']
    for strategy in ('limit_up','oversold'):
        d=events[events['strategy']==strategy]
        if strategy=='limit_up':
            d=d[~d['one_price']]
        for key in policy['filters'][strategy]:
            d=d[filter_masks(d,strategy)[key]]
            enabled=next(f for f in result['strategies'][strategy]['filters'] if f['filter']==key)
            assert enabled['enabled'] and enabled['keep'] and enabled['combination']['keep']
        a=d['return'].dropna().to_numpy()
        expected=result['strategies'][strategy]['selected']
        assert len(a)==expected['n']
        assert int(np.sum(a>0))==expected['wins']
        assert np.isclose(100*np.mean(a),expected['mean_pct'])
    print(json.dumps({'checked_stat_groups':number,'independent_recompute':True,'event_rows':len(events)}))


if __name__=='__main__':
    main()
