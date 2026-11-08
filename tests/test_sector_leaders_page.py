"""板块龙头页面 JS 渲染测试。"""
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "static" / "sector-leaders.html"

# 页面脚本顶层只用到 document 与 fetch：桩住它们，脚本可离线在 node 里加载，
# 不发任何真实请求；随后直接调用 pickCard 校验各字段的显示单位。
STUB = """
const __el = () => ({textContent: "", innerHTML: "", insertAdjacentHTML: function () {}});
globalThis.document = {getElementById: __el};
globalThis.fetch = () => Promise.resolve({json: () => Promise.resolve({source: "none", sectors: {}, counts: {sectors: 0, picks: 0}, summary: ""})});
"""

SNIPPET = """
const daily = {code: "300709", name: "精研科技", signal: "5日涨幅 18.74%", ret_5d: 0.1873501199040768, vol_ratio: 2.0566975212245846, turnover: 15.86, close: 39.61, win_rate: 0.4765106711409394, n: 149, wins: 71, mean_ret: 0.007793873695113778, payoff: 1.359928901369309, last_signal: "2026-10-28", signals_2026: 54, excluded_live: true};
const backtest = {code: "688807", name: "优迅股份", signal: "回测 Top 3", ret_5d: null, vol_ratio: null, turnover: null, close: null, win_rate: 0.64, n: 50, wins: 32, mean_ret: 0.046638761300514205, payoff: 1.1979548022183435, last_signal: "2026-09-23", signals_2026: 50, excluded_live: true};
console.log(JSON.stringify({daily: pickCard(daily), backtest: pickCard(backtest)}));
"""


def render_pick_cards():
    html = PAGE.read_text(encoding="utf-8")
    m = re.search(r"<script>(.*?)</script>", html, re.S)
    assert m is not None, "sector-leaders.html 缺少脚本块"
    with tempfile.TemporaryDirectory() as tmp:
        js = Path(tmp) / "harness.js"
        js.write_text(STUB + m.group(1) + SNIPPET, encoding="utf-8")
        out = subprocess.run(["node", str(js)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


class SectorLeadersPageRenderTest(unittest.TestCase):
    def test_daily_turnover_is_percent_points(self):
        cards = render_pick_cards()
        daily = cards["daily"]
        self.assertIn("5日涨幅 18.74%", daily)
        self.assertEqual(daily.count("5日涨幅"), 1)
        self.assertNotIn("5 日涨幅", daily)
        self.assertIn("量比 2.06", daily)
        self.assertIn("换手率 15.86%", daily)
        self.assertNotIn("1586", daily)
        self.assertIn("47.65%", daily)
        self.assertIn("n=149", daily)

    def test_backtest_fallback_card_has_no_daily_fields(self):
        cards = render_pick_cards()
        backtest = cards["backtest"]
        self.assertIn("回测 Top 3", backtest)
        self.assertNotIn("量比", backtest)
        self.assertNotIn("换手率", backtest)
        self.assertIn("64.00%", backtest)


if __name__ == "__main__":
    unittest.main()
