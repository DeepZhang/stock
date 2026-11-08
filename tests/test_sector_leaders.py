"""板块龙头回测模块的单元测试。
运行：.venv/bin/python -m unittest discover -s tests -v
只测研究模块的纯函数与合成数据，不碰页面与 API。"""
import sys, unittest
from pathlib import Path
import pandas as pd
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'modules'))
sys.path.insert(0, str(ROOT / 'stock-analysis'))
from stock_data import SECTOR_MAP
from backtest_sector_leaders import (
    add_features, base_mask, combo_mask, decide_kept, filter_masks,
    rank_top3, sector_median_ret20, summarise, verify, rank_sectors, prepare_rs,
)

def bars(closes, volumes=None):
    n = len(closes)
    dates = pd.bdate_range('2024-01-02', periods=n)
    vol = volumes if volumes is not None else [1000.0] * n
    return pd.DataFrame({'date': dates, 'open': closes, 'high': closes,
                         'low': closes, 'close': closes, 'volume': vol,
                         'amount': [c * v for c, v in zip(closes, vol)],
                         'turnover': [5.0] * n})

def stats(n, wins, mean=0.01):
    return {'full': {'n': n, 'wins': wins, 'win_rate': wins / n if n else None, 'mean_ret': mean, 'payoff': 1.0},
            'calibration': {'n': n, 'wins': wins, 'win_rate': wins / n if n else None, 'mean_ret': mean, 'payoff': 1.0},
            'validation': {'n': n, 'wins': wins, 'win_rate': wins / n if n else None, 'mean_ret': mean, 'payoff': 1.0}}

class TestFeatures(unittest.TestCase):
    def test_ret5_and_fwd5(self):
        closes = [100.0 * (1.01 ** i) for i in range(80)]
        d = add_features(bars(closes))
        self.assertAlmostEqual(d.loc[5, 'ret5'], closes[5] / closes[0] - 1, places=9)
        self.assertAlmostEqual(d.loc[0, 'ret_fwd5'], closes[5] / closes[0] - 1, places=9)
        self.assertTrue(d.loc[74, 'has_fwd5'])
        self.assertFalse(d.loc[75, 'has_fwd5'])

    def test_base_mask_needs_21_bars(self):
        closes = [100.0 * (1.02 ** i) for i in range(30)]
        d = add_features(bars(closes))
        m = base_mask(d)
        self.assertFalse(m.iloc[:20].any())
        self.assertTrue(m.iloc[20:25].all())

    def test_summarise(self):
        s = summarise([0.10, -0.05, 0.02])
        self.assertEqual((s['n'], s['wins']), (3, 2))
        self.assertAlmostEqual(s['win_rate'], 2 / 3)
        self.assertAlmostEqual(s['mean_ret'], 0.07 / 3)
        self.assertAlmostEqual(s['payoff'], 0.06 / 0.05)
        self.assertEqual(summarise([])['n'], 0)

class TestDecideKept(unittest.TestCase):
    def test_kept_only_when_all_periods_improve(self):
        base = stats(1000, 480)  # 三期胜率均为 0.48
        good = {'vol_ratio_le_3': stats(800, 400)}  # 0.50，三期都高于基线
        bad = {'vol_ratio_le_3': stats(800, 400),
               'ma_bull': {'full': {'n': 800, 'wins': 400, 'win_rate': 0.50, 'mean_ret': 0.01, 'payoff': 1.0},
                           'calibration': {'n': 800, 'wins': 400, 'win_rate': 0.50, 'mean_ret': 0.01, 'payoff': 1.0},
                           'validation': {'n': 800, 'wins': 360, 'win_rate': 0.45, 'mean_ret': 0.01, 'payoff': 1.0}}}
        kept, _ = decide_kept(base, good)
        self.assertEqual(kept, ['vol_ratio_le_3'])
        kept2, _ = decide_kept(base, bad)
        self.assertEqual(kept2, ['vol_ratio_le_3'])

    def test_small_sample_not_kept(self):
        base = stats(1000, 480)
        tiny = {'vol_ratio_le_3': stats(40, 30)}  # 胜率高但 n<50
        kept, _ = decide_kept(base, tiny)
        self.assertEqual(kept, [])

class TestRankTop3(unittest.TestCase):
    def _row(self, code, n, wins, mean=0.02):
        return {'code': code, 'name': code,
                'full': {'n': n, 'wins': wins, 'win_rate': wins / n if n else None,
                         'mean_ret': mean, 'payoff': 1.0},
                'validation': {'n': 0, 'wins': 0, 'win_rate': None, 'mean_ret': None, 'payoff': None}}

    def test_min_signals_and_order(self):
        rows = [self._row('A', 19, 19),  # n<20 不进榜，哪怕胜率 100%
                self._row('B', 100, 60), self._row('C', 50, 30),
                self._row('D', 200, 100), self._row('E', 80, 40)]
        top = rank_top3(rows)
        self.assertEqual([r['code'] for r in top], ['B', 'C', 'D'])

    def test_tie_break_by_n_then_mean(self):
        rows = [self._row('A', 50, 30, mean=0.01), self._row('B', 80, 48, mean=0.01),
                self._row('C', 80, 48, mean=0.03)]
        top = rank_top3(rows)
        self.assertEqual([r['code'] for r in top], ['C', 'B', 'A'])

class TestComboAndVerify(unittest.TestCase):
    def test_combo_is_subset_of_base(self):
        closes = [100.0 * (1.01 ** i) for i in range(80)]
        d = add_features(bars(closes))
        d['rs_sector_median'] = 0.0
        base, fmask = filter_masks(d)
        combo = combo_mask(d, ['ret5_ge_10'])
        self.assertTrue((combo <= base).all())
        self.assertTrue((combo <= fmask['ret5_ge_10']).all())
        self.assertEqual(combo.sum(), 0)  # 1% 日涨幅下 5 日涨幅约 5.1%，达不到 10%

    def test_sector_map_is_23_fundflow_sectors(self):
        self.assertEqual(len(SECTOR_MAP), 23)
        self.assertTrue(all(v.startswith('BK') for v in SECTOR_MAP.values()))
        self.assertIn('半导体', SECTOR_MAP)

    def test_verify_matches_main_on_synthetic(self):
        up = [100.0 * (1.01 ** i) for i in range(80)]
        flat = [100.0] * 80
        feats = {'000001': add_features(bars(up)), '000002': add_features(bars(flat))}
        members = {'测试板块': ['000001', '000002']}
        medians = sector_median_ret20(feats, members)
        prepare_rs(feats, medians, {'000001': ['测试板块'], '000002': ['测试板块']})
        names = {'000001': '上涨票', '000002': '横盘票'}
        res = rank_sectors(feats, medians, members, [], names)
        top = res['测试板块']['top3']
        self.assertEqual(len(top), 1)
        self.assertEqual(top[0]['code'], '000001')
        self.assertAlmostEqual(top[0]['full']['win_rate'], 1.0)
        v = verify(feats, members, [], res)
        self.assertTrue(v['ok'], v['mismatches'])
        self.assertEqual(v['checked_stocks'], 2)

if __name__ == '__main__':
    unittest.main()
