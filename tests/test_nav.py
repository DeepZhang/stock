import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXPECTED = [("/", "资金流向"), ("/market", "行情总览/每日复盘"), ("/rank", "排行榜"), ("/kline", "个股K线"), ("/screener", "选股器"), ("/shortterm", "短线选股"), ("/precious", "贵金属")]
PAGES = ["index.html", "kline.html", "market.html", "market2.html", "precious.html", "rank.html", "screener.html", "sentiment.html", "shortterm.html"]

class NavConsistencyTest(unittest.TestCase):
    def test_nav_links_same_text_and_order(self):
        for name in PAGES:
            html = (ROOT / "static" / name).read_text(encoding="utf-8")
            m = re.search("<nav.*?</nav>", html, re.S)
            self.assertIsNotNone(m, name + " 缺少导航栏")
            links = re.findall("<a href=\"([^\"]+)\"[^>]*>([^<]+)</a>", m.group(0))
            self.assertEqual(links, EXPECTED, name + " 导航与标准不一致")

if __name__ == "__main__":
    unittest.main()
