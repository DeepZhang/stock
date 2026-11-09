"""明日展望只读 API：缺失/损坏返回空结构，正常返回产物原样。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app as site


class PredictionApiTests(unittest.TestCase):
    def test_read_only_api_missing_and_present(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(
                site, "PREDICTION_RESULT", str(Path(temp) / "prediction.json")):
            with site.app.test_client() as client:
                response = client.get("/api/prediction")
                body = response.get_json()
                self.assertEqual(response.status_code, 200)
                self.assertEqual(body["date"], "")
                self.assertEqual(body["signals"], [])
                fixture = {**body, "date": "2026-11-09",
                           "next_trading_day": "2026-11-10", "total": 5211,
                           "verdict": "neutral", "verdict_cn": "中性，无方向性信号",
                           "reason": "各指标均落在中性区间",
                           "signals": [{"metric": "limit_up", "name": "涨停占比",
                                        "value_pct": 1.4, "bucket": "0.5-1.5%",
                                        "fwd_1_test": -0.042, "fwd_1_test_win": 54.9}]}
                Path(site.PREDICTION_RESULT).write_text(json.dumps(fixture))
                self.assertEqual(client.get("/api/prediction").get_json(), fixture)
                with client.get("/market") as page:
                    self.assertEqual(page.status_code, 200)
                    self.assertIn("明日展望", page.get_data(as_text=True))
                Path(site.PREDICTION_RESULT).write_text("{")
                self.assertEqual(client.get("/api/prediction").get_json()["date"], "")


if __name__ == "__main__":
    unittest.main()
