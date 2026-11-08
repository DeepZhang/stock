import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'stock-analysis'))
import pull_history as pull


class HistoryTests(unittest.TestCase):
    def test_reuses_session_per_thread(self):
        import threading
        with patch.object(pull,'_LOCAL',threading.local()), patch.object(pull.requests,'Session') as factory:
            pull.http_get('https://example.invalid/one')
            pull.http_get('https://example.invalid/two')
            self.assertEqual(factory.call_count,1)
            self.assertEqual(factory.return_value.get.call_count,2)

    def test_qfq_download_and_atomic_cache(self):
        response=Mock()
        response.json.return_value={'data':{'klines':[
            '2026-11-06,10,11,11,10,1000,100000000,10,5',
            '2026-11-05,10,10,10,10,900,90000000,0,4']}}
        with tempfile.TemporaryDirectory() as temp, patch.object(pull,'HIST_DIR',temp), patch.object(pull,'http_get',return_value=response) as get:
            code,n,error=pull.fetch_one({'code':'600000'},'20240101','20261106')
            self.assertEqual((code,n,error),('600000',2,None))
            self.assertEqual(get.call_args.kwargs['params']['fqt'],'1')
            d=pd.read_parquet(Path(temp)/'600000.parquet')
            self.assertEqual(d.iloc[0]['date'],'2026-11-05')
            self.assertEqual(d.iloc[1]['turnover'],5)
            self.assertFalse(list(Path(temp).glob('*.tmp')))

    def test_empty_response_never_completed_or_overwrites(self):
        response=Mock(); response.json.return_value={'data':None}
        with tempfile.TemporaryDirectory() as temp, patch.object(pull,'HIST_DIR',temp), patch.object(pull,'http_get',return_value=response), patch.object(pull.time,'sleep'):
            p=Path(temp)/'600000.parquet'; p.write_bytes(b'existing')
            self.assertEqual(pull.fetch_one({'code':'600000'})[1],0)
            self.assertIsNotNone(pull.fetch_one({'code':'600000'})[2])
            self.assertEqual(p.read_bytes(),b'existing')

    def test_confirmed_empty_is_separate_and_retryable(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(pull,'HIST_DIR',temp), patch.object(pull,'fetch_stock_list',return_value=[{'code':'000003'}]), patch.object(pull,'fetch_one',return_value=('000003',0,'empty history confirmed (3 HTTP 200 responses)')) as fetch:
            with patch.object(sys,'argv',['pull']):
                pull.main(); pull.main()
            self.assertEqual(fetch.call_count,1)
            state=json.loads((Path(temp)/'checkpoint.json').read_text())
            self.assertEqual(state['done'],{})
            self.assertEqual(state['errors'],{})
            self.assertIn('000003',state['unavailable'])
            with patch.object(sys,'argv',['pull','--retry-unavailable']):
                pull.main()
            self.assertEqual(fetch.call_count,2)

    def test_bounded_batches_checkpoint_and_resume(self):
        stocks=[{'code':str(i).zfill(6)} for i in range(4)]
        with tempfile.TemporaryDirectory() as temp, patch.object(pull,'HIST_DIR',temp), patch.object(pull,'fetch_stock_list',return_value=stocks), patch.object(sys,'argv',['pull','--batch-size','2','--batches','1']):
            def fake(s,*args):
                (Path(temp)/(s['code']+'.parquet')).touch()
                return s['code'],100,None
            with patch.object(pull,'fetch_one',side_effect=fake) as fetch:
                pull.main()
                self.assertEqual(fetch.call_count,2)
                state=json.loads((Path(temp)/'checkpoint.json').read_text())
                self.assertEqual(len(state['done']),2)
                pull.main()
                self.assertEqual(fetch.call_count,4)
                pull.main()
                self.assertEqual(fetch.call_count,4)
                # 删除实际文件后，即使done已标记也必须重拉。
                (Path(temp)/'000000.parquet').unlink()
                pull.main()
                self.assertEqual(fetch.call_count,5)


if __name__=='__main__':
    unittest.main()
