"""每日产物、只读API、报告数字和增量刷新边界。"""
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app as site
from scripts import run_shortterm as task
import pandas as pd


class Phase3Tests(unittest.TestCase):
    def test_self_test_and_calendar(self):
        task.self_test()
        self.assertEqual(task.latest_trading_date(dt.date(2026,11,8)), '2026-11-06')

    def test_read_only_api_missing_and_present(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(site, 'SHORTTERM_RESULT', str(Path(temp)/'result.json')), patch('requests.get', side_effect=AssertionError('network forbidden')), patch('requests.post', side_effect=AssertionError('send forbidden')), patch('stock_screener.run_shortterm', side_effect=AssertionError('scan forbidden')):
            with site.app.test_client() as client:
                response=client.get('/api/shortterm'); body=response.get_json()
                self.assertEqual(response.status_code,200)
                self.assertEqual(body['generated_at'],'')
                self.assertEqual(body['limit_up'],[])
                self.assertEqual(body['counts'],{'limit_up':0,'oversold':0})
                fixture={**body,'as_of':'2026-11-06','generated_at':'actual-task-time','summary':'摘要'}
                Path(site.SHORTTERM_RESULT).write_text(json.dumps(fixture))
                self.assertEqual(client.get('/api/shortterm').get_json(),fixture)
                with client.get('/shortterm') as page:
                    self.assertEqual(page.status_code,200)
                Path(site.SHORTTERM_RESULT).write_text('{')
                self.assertEqual(client.get('/api/shortterm').get_json()['generated_at'],'')

    def test_history_rows_match_report_and_arithmetic(self):
        report=(ROOT/'stock-analysis/shortterm_backtest.md').read_text()
        for row in task.backtest_snapshot()['rows']:
            self.assertAlmostEqual(row['win_rate'],100*row['wins']/row['n'])
            expected=f"{row['n']} / {row['wins']} / {row['win_rate']:.3f}% / {row['mean_pct']:.3f}% / {row['payoff_ratio']:.3f}"
            self.assertIn(expected,report)

    def test_generate_offline_has_summary_and_never_sends(self):
        with tempfile.TemporaryDirectory() as temp, patch('requests.get',side_effect=AssertionError('network')), patch('requests.post',side_effect=AssertionError('send')):
            cache=Path(temp)
            (cache/'universe.json').write_text(json.dumps({'stocks':[{'code':'600000','name':'普通股'}]}))
            dates=pd.bdate_range(end='2026-11-06',periods=30).strftime('%Y-%m-%d')
            data=pd.DataFrame({'date':dates,'open':10.,'close':10.,'high':11.,'low':9.,'volume':100.,'amount':1e8,'turnover':5.,'pct_chg':0.})
            data.loc[29,'pct_chg']=10
            data.to_parquet(cache/'600000.parquet')
            output=cache/'result.json'
            result=task.generate('2026-11-06',cache,output,offline=True)
            self.assertEqual(result['counts']['limit_up'],1)
            self.assertEqual(json.loads(output.read_text()),result)
            self.assertIn('普通股',result['summary'])
            self.assertIn('2026-11-06',result['summary'])
            self.assertTrue(result['generated_at'])
            with self.assertRaisesRegex(ValueError,'未来日期'):
                task.generate('2099-01-01',cache,output,offline=True)
            with self.assertRaisesRegex(ValueError,'休市'):
                task.generate('2026-11-08',cache,output,offline=True)
            (cache/'600000.parquet').unlink()
            saved=output.read_bytes()
            with self.assertRaisesRegex(RuntimeError,'结果未发布'):
                task.generate('2026-11-06',cache,output,offline=True)
            self.assertEqual(output.read_bytes(),saved)

    def test_refresh_coverage_no_network_and_new_vintage(self):
        with tempfile.TemporaryDirectory() as temp:
            cache=Path(temp)
            (cache/'universe.json').write_text(json.dumps({'stocks':[{'code':'600000'}]}))
            state=cache/'checkpoint.json'
            state.write_text(json.dumps({'range':['20231101','20261106']}))
            with patch.object(task,'fetch_one') as fetch:
                self.assertFalse(task.refresh_history('2026-11-06',cache)['network'])
                fetch.assert_not_called()
            old_dir=task.pull_history.HIST_DIR
            with patch.object(task,'fetch_one',return_value=('600000',32,None)) as fetch:
                result=task.refresh_history('2026-11-09',cache)
                self.assertEqual(result['updated'],1)
                fetch.assert_called_once_with({'code':'600000'},beg='20231101',end='20261109')
            self.assertEqual(task.pull_history.HIST_DIR,old_dir)
            with patch.object(task,'fetch_one',return_value=('600000',0,'timeout')):
                with self.assertRaisesRegex(RuntimeError,'结果未发布'):
                    task.refresh_history('2026-11-10',cache)
            self.assertEqual(json.loads(state.read_text())['range'][1],'20261109')


if __name__=='__main__':
    unittest.main()
