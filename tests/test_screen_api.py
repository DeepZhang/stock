"""缓存响应、后台单例及失败保留；全部隔离扫描，不访问行情。"""
import json
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


class ScreenTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'stock_picks.json'
        self.fixture = {'time': '2026-11-06 15:30', 'list': [{'code': '600000'}]}
        self.path.write_text(json.dumps(self.fixture))
        self.cache = patch.object(site, 'SCREEN_CACHE', str(self.path)); self.cache.start()
        self.release = threading.Event()
        self.finished = threading.Event()

    def tearDown(self):
        self.release.set()
        deadline = time.monotonic()+3
        while site._screen_scanning and time.monotonic()<deadline:
            time.sleep(.01)
        self.assertFalse(site._screen_scanning)
        self.cache.stop(); self.temp.cleanup()

    def get(self, url='/api/screen'):
        with site.app.test_client() as client:
            r = client.get(url)
            self.assertEqual(r.status_code, 200)
            return r.get_json()

    def scan(self):
        self.release.wait(3)
        return [{'code': '600001'}]

    def test_cached_returns_under_five_seconds(self):
        with patch('stock_screener.run_screen') as scan:
            start = time.monotonic(); body = self.get()
            self.assertLess(time.monotonic()-start, 5)
            self.assertEqual(body, {**self.fixture, 'count': 1, 'scanning': False})
            scan.assert_not_called()

    def test_concurrent_force_starts_once_and_publishes(self):
        with patch('stock_screener.run_screen', side_effect=self.scan) as scan:
            with ThreadPoolExecutor(2) as pool:
                bodies = list(pool.map(self.get, ['/api/screen?force=1']*2))
            self.assertEqual(scan.call_count, 1)
            self.assertTrue(all(b['scanning'] and b['list']==self.fixture['list'] for b in bodies))
            self.assertEqual(self.get('/api/screen_cached'), self.fixture)
            self.release.set()
            deadline=time.monotonic()+3
            while site._screen_scanning and time.monotonic()<deadline: time.sleep(.01)
            body=self.get('/api/screen_cached')
            self.assertNotEqual(body['time'], self.fixture['time'])
            self.assertEqual(body['list'], [{'code':'600001'}])

    def test_missing_and_expired_trigger(self):
        for missing in (False, True):
            if missing: self.path.unlink()
            else:
                import os
                os.utime(self.path, (0, 0))
            with patch('stock_screener.run_screen', side_effect=self.scan) as scan:
                started=time.monotonic()
                body=self.get()
                self.assertLess(time.monotonic()-started,5)
                self.assertTrue(body['scanning'])
                self.assertEqual(body['list'], [] if missing else self.fixture['list'])
                self.release.set()
                deadline=time.monotonic()+3
                while site._screen_scanning and time.monotonic()<deadline: time.sleep(.01)
                self.assertEqual(scan.call_count,1)
                self.release.clear()

    def test_failure_keeps_cache_and_allows_retry(self):
        with patch('stock_screener.run_screen', side_effect=RuntimeError('test failure')):
            with self.assertLogs(site.app.logger, level='ERROR'):
                self.get('/api/screen?force=1')
                deadline=time.monotonic()+3
                while site._screen_scanning and time.monotonic()<deadline: time.sleep(.01)
        self.assertFalse(site._screen_scanning)
        self.assertEqual(self.get('/api/screen_cached'), self.fixture)


if __name__ == '__main__':
    unittest.main()
