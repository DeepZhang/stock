"""行情缓存：慢上游、并发单例、原子发布、失败保留和冷启动。"""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as site


class MarketOverviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'market_overview.json'
        self.fixture = {'update_time': '2026-11-06 15:30:00',
                        'indices': [{'code': '000001', 'price': 3600}],
                        'limit_stats': {'total': 5220}, 'flow_analysis': {}, 'fund_flow': None}
        self.path.write_text(json.dumps(self.fixture))
        self.cache = patch.object(site, 'MARKET_OVERVIEW_CACHE', str(self.path))
        self.cache.start()
        self.release = threading.Event()

    def wait_worker(self):
        deadline = time.monotonic() + 3
        while site._market_overview_refreshing and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(site._market_overview_refreshing)

    def tearDown(self):
        self.release.set()
        self.wait_worker()
        self.cache.stop()
        self.temp.cleanup()

    def get(self):
        start = time.perf_counter()
        with site.app.test_client() as client:
            response = client.get('/api/market-overview')
        self.assertLess(time.perf_counter() - start, 1)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'application/json')
        return json.loads(response.data, parse_constant=lambda value: self.fail(value))

    def slow_upstream(self):
        self.release.wait(5)
        return {**self.fixture, 'update_time': '2026-11-08 10:00:00'}

    def test_fresh_cache_skips_upstream(self):
        with patch.object(site.market_data, 'get_market_overview') as fetch:
            self.assertEqual(self.get(), self.fixture)
            fetch.assert_not_called()

    def test_stale_concurrent_returns_old_cache_and_refreshes_once(self):
        os.utime(self.path, (0, 0))
        with patch.object(site.market_data, 'get_market_overview', side_effect=self.slow_upstream) as fetch:
            with ThreadPoolExecutor(8) as pool:
                bodies = list(pool.map(lambda _: self.get(), range(8)))
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(bodies, [self.fixture] * 8)
            self.assertEqual(json.loads(self.path.read_text()), self.fixture)
            self.release.set()
            self.wait_worker()
            self.assertEqual(self.get()['update_time'], '2026-11-08 10:00:00')
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_missing_corrupt_and_invalid_cache_return_original_fields(self):
        for content in (None, '<html>502</html>', '[]', '{"indices": []}',
                        json.dumps({**self.fixture, 'fund_flow': {'main_net': float('nan')}})):
            with self.subTest(content=content):
                if content is None:
                    self.path.unlink(missing_ok=True)
                else:
                    self.path.write_text(content)
                self.release.clear()
                with patch.object(site.market_data, 'get_market_overview', side_effect=self.slow_upstream) as fetch:
                    self.assertEqual(self.get(), site._empty_market_overview())
                    self.release.set()
                    self.wait_worker()
                    self.assertEqual(fetch.call_count, 1)
                    self.assertTrue(self.get()['indices'])

    def test_failure_and_invalid_payload_keep_cache_and_allow_retry(self):
        for value in (RuntimeError('upstream failed'), [],
                      {**self.fixture, 'fund_flow': {'main_net': float('nan')}}):
            os.utime(self.path, (0, 0))
            options = {'side_effect': value} if isinstance(value, Exception) else {'return_value': value}
            with patch.object(site.market_data, 'get_market_overview', **options):
                with self.assertLogs(site.app.logger, level='ERROR'):
                    self.assertEqual(self.get(), self.fixture)
                    self.wait_worker()
            self.assertEqual(json.loads(self.path.read_text()), self.fixture)
        with patch.object(site.market_data, 'get_market_overview', return_value=self.fixture) as fetch:
            self.get()
            self.wait_worker()
            self.assertEqual(fetch.call_count, 1)

    def test_thread_start_failure_still_returns_cache_and_unlocks(self):
        os.utime(self.path, (0, 0))
        with patch('threading.Thread.start', side_effect=RuntimeError('no thread')):
            with self.assertLogs(site.app.logger, level='ERROR'):
                self.assertEqual(self.get(), self.fixture)
        self.assertFalse(site._market_overview_refreshing)

    def test_publish_failure_cleans_temp_and_keeps_cache(self):
        os.utime(self.path, (0, 0))
        with patch.object(site.market_data, 'get_market_overview', return_value=self.fixture):
            with patch.object(site.os, 'replace', side_effect=OSError('disk error')):
                with self.assertLogs(site.app.logger, level='ERROR'):
                    self.get()
                    self.wait_worker()
        self.assertEqual(json.loads(self.path.read_text()), self.fixture)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])


if __name__ == '__main__':
    unittest.main()
