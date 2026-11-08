"""运行：.venv/bin/python -m unittest discover -s tests -v"""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'stock-analysis'))
from modules import shortterm_rules as rules
from modules.stock_screener import run_shortterm, check_breakout_bars
from modules import stock_screener as screener
from analyze_patterns import add_features
from backtest_shortterm import stats, close_exit, compare, daily_exit_scenarios
from pull_history import atomic_json, secid


def bars(n=80):
    close = 10 + np.sin(np.arange(n)/3)
    return pd.DataFrame({'date':pd.bdate_range('2026-07-01',periods=n).strftime('%Y-%m-%d'),
                         'open':close, 'close':close, 'high':close+.2, 'low':close-.2,
                         'volume':1000., 'amount':2e8, 'pct_chg':0., 'turnover':5.})


class RulesTests(unittest.TestCase):
    def setUp(self):
        self.d=bars()
        self.row=rules.features(self.d,'600000').iloc[-1].copy()
        self.row['is_limit_up']=True
        self.row['one_price']=False
        self.row['boards']=1
        self.row['pct_chg']=10
        self.policy={'filters':{},'calibration_end':'2025-12-31',
                     'calibration':{'limit_up':{'all':{'n':100,'win_rate':60},'first':{'n':4,'win_rate':100}}}}

    def test_eligibility_boundaries(self):
        for name in ('*ST例','st例','退市例',''):
            self.assertFalse(rules.eligible(name,self.row))
        for field in ('open','close','high','low','volume','amount'):
            for value in (0, np.nan, None, '-'):
                row=self.row.copy(); row[field]=value
                self.assertFalse(rules.eligible('普通股',row))
        row=self.row.copy(); row['suspended']=True
        self.assertFalse(rules.eligible('普通股',row))
        self.assertTrue(rules.eligible('普通股',self.row))

    def test_one_price_rejected(self):
        row=self.row.copy(); row['one_price']=True
        self.assertIsNone(rules.strat_limit_up('600000','普通股',row,self.policy))
        self.assertIsNotNone(rules.strat_limit_up('600000','普通股',self.row,self.policy))

    def test_thresholds_boards_and_no_future(self):
        d=self.d.copy(); d.loc[70:72,'pct_chg']=[9.5,10,10]
        f=rules.features(d,'600000')
        self.assertEqual(f.loc[70:73,'boards'].tolist(),[1,2,3,0])
        pd.testing.assert_frame_equal(rules.features(d.iloc[:73],'600000'),f.iloc[:73].reset_index(drop=True))
        self.assertFalse(rules.features(d,'300001').iloc[70]['is_limit_up'])
        d.loc[70,'pct_chg']=19
        self.assertTrue(rules.features(d,'688001').iloc[70]['is_limit_up'])

    def test_rsi_and_volume_match_existing(self):
        d=self.d.copy(); d['code']='600000'
        expected=add_features(d)
        actual=rules.features(self.d,'600000')
        for col in ('rsi14','vol_ratio','ret_5','is_limit_up'):
            np.testing.assert_allclose(expected[col].astype(float),actual[col].astype(float),equal_nan=True)
        monotonic=self.d.copy(); monotonic['close']=np.arange(len(monotonic))+10
        self.assertTrue(rules.features(monotonic,'600000')['rsi14'].isna().all())

    def test_filters_missing_values(self):
        self.policy['filters']={'limit_up':['turnover_1_20']}
        for v,passes in ((1,True),(20,True),(.99,False),(20.01,False),(np.nan,False)):
            row=self.row.copy(); row['turnover']=v
            self.assertEqual(rules.passes_filters(row,'limit_up',self.policy),passes)

    def test_keyword_and_missing_announcements(self):
        row=self.row.copy(); row['ret_5']=-.1; row['rsi14']=30; row['boards']=0
        self.assertTrue(rules.risk_keyword(['收到立案调查通知']))
        self.assertFalse(rules.risk_keyword(['正常经营公告']))
        self.assertIsNone(rules.strat_oversold('600000','普通股',row,self.policy,['立案调查']))
        item=rules.strat_oversold('600000','普通股',row,self.policy)
        self.assertEqual(item['announcement_status'],'unknown')
        self.assertIsNone(item['board_type'])
        missing=row.copy(); missing['low5']=np.nan
        self.assertIsNone(rules.strat_oversold('600000','普通股',missing,self.policy))
        self.assertEqual(rules.strat_oversold('600000','普通股',row,self.policy,[])['announcement_status'],'provided_texts_checked')

    def test_score_fallback_and_plan(self):
        item=rules.strat_limit_up('600000','普通股',self.row,self.policy)
        self.assertEqual(item['expected_win_rate'],60)
        self.assertEqual(item['historical_n'],100)
        self.assertIsNone(item['seal_strength'])
        self.assertIsNone(item['plan']['buy_price'])
        self.assertIsNone(item['plan']['stop_price'])
        self.assertIn('当天不可卖',item['plan']['execution_note'])
        json.dumps(item,allow_nan=False,ensure_ascii=False)

    def test_ranking_and_top10(self):
        items=[{'code':str(i),'expected_win_rate':60 if i%2 else 50,'amount':i} for i in range(20)]
        ranked=rules.rank_candidates(items)
        self.assertEqual(len(ranked),10)
        self.assertEqual(ranked[0]['code'],'19')
        self.assertEqual(ranked[-1]['code'],'1')

    def test_stats_zero_and_missing(self):
        s=stats([.1,-.05,0,np.nan])
        self.assertEqual(s['n'],3); self.assertEqual(s['wins'],1)
        self.assertEqual(s['win_rate'],100/3)
        self.assertEqual(s['payoff_ratio'],2)
        self.assertIsNone(stats([])['win_rate'])

    def test_close_plan_stops_and_censoring(self):
        d=pd.DataFrame({'close':[10,9,9,9,9,9,9,9], 'low5':[8]*8})
        r=close_exit(d,'oversold')
        self.assertAlmostEqual(r.iloc[0],-.1)
        self.assertTrue(r.iloc[-5:].isna().all())
        d['close']=[10,10,10,11,9,9,9,9]
        self.assertAlmostEqual(close_exit(d,'oversold').iloc[0],.1)

    def test_daily_scenario_stop_first_and_gap(self):
        d=pd.DataFrame({'open':[10,10,9], 'close':[10,10,9],
                        'high':[10,11,9.5], 'low':[10,9,8.5]})
        research,t1,both=daily_exit_scenarios(d)
        self.assertTrue(both.iloc[0])
        self.assertAlmostEqual(research.iloc[0],-.03)
        self.assertAlmostEqual(t1.iloc[0],-.1)
        self.assertTrue(pd.isna(research.iloc[-1]))
        self.assertTrue(pd.isna(t1.iloc[-2]))

    def test_filter_requires_gain_and_n50(self):
        d=pd.DataFrame({'date':['2025-01-01']*100+['2026-01-01']*100,'return':[.01,-.01]*100})
        self.assertFalse(compare(d,'all',pd.Series(True,index=d.index))['keep'])
        m=d['return']>0
        self.assertTrue(compare(d,'winners_only_test_fixture',m)['keep'])
        self.assertFalse(compare(d.iloc[:40],'small',m.iloc[:40])['keep'])

    def test_checkpoint_and_secid(self):
        self.assertEqual(secid('600519'),'1.600519')
        self.assertEqual(secid('300001'),'0.300001')
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'state.json'
            atomic_json(p,{'done':['a']}); atomic_json(p,{'done':['a','b']})
            self.assertEqual(json.loads(p.read_text())['done'],['a','b'])
            self.assertFalse(p.with_suffix('.json.tmp').exists())

    def test_old_screen_wrapper_stays_flat(self):
        d=bars(120)
        c=10*1.01**np.arange(120)
        for key in ('open','close'):
            d[key]=c
        d['high']=c+.1;d['low']=c-.1
        d.loc[117:,'volume']=3000
        data=d.to_dict('records')
        expected=check_breakout_bars('600000','样本',data)
        self.assertIsNotNone(expected)
        with patch.object(screener,'get_block_stocks',return_value={'600000':('样本','测试板块')}), patch.object(screener,'fetch_kline_bars',return_value=data), patch.object(screener.time,'sleep'):
            actual=screener.run_screen()
        self.assertIsInstance(actual,list)
        self.assertEqual(actual[0]['code'],'600000')
        self.assertEqual(actual[0]['price'],expected['price'])
        self.assertEqual(actual[0]['sector'],'测试板块')

    def test_short_history_breakout_and_no_future_score(self):
        self.assertIsNone(check_breakout_bars('600000','普通股',self.d.iloc[:20].to_dict('records')))
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'policy.json'; p.write_text(json.dumps(self.policy))
            with self.assertRaisesRegex(ValueError,'禁止回看未来'):
                run_shortterm('2025-01-01',history_dir=temp,policy_path=p)


if __name__=='__main__':
    unittest.main()
