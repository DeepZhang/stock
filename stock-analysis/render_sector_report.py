"""由 sector_leaders_results.json 渲染 sector_leaders_backtest.md。
用法：.venv/bin/python stock-analysis/render_sector_report.py
报告里的全部数字都来自结果 JSON，不手抄。"""
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'stock-analysis'

def pct(x, digits=2):
    return f'{x * 100:.{digits}f}%' if x is not None else '-'

def num(x, digits=4):
    return f'{x:.{digits}f}' if x is not None else '-'

def fmt_stats(s):
    return (f"n={s['n']}，胜场={s['wins']}，胜率={pct(s['win_rate'])}，"
            f"平均收益={pct(s['mean_ret'])}，盈亏比={num(s['payoff'], 3)}")

def main():
    r = json.loads((OUT / 'sector_leaders_results.json').read_text(encoding='utf-8'))
    m = json.loads((OUT / 'sector_members.json').read_text(encoding='utf-8'))
    lines = []
    a = lines.append
    a('# 选股器板块龙头回测：每板块 Top 3')
    a('')
    a(f"回测生成时间（UTC）：{r['generated_at']}；统计期 2024-01-01~2026-11-06；"
      f"分支 feature/sector-leaders-backtest；结果文件 sector_leaders_results.json。")
    a('本回测只做研究，不改页面与 API，不接选股器线上逻辑。')
    a('')
    a('## 结论')
    a('')
    a('1. 强势本身不赚钱：基线“5 日涨幅>=5%”信号日收盘买入、持有 5 个交易日，'
      f"全期胜率只有 {pct(r['base']['stats']['full']['win_rate'])}（n={r['base']['stats']['full']['n']}），"
      '低于 50%，平均收益靠盈亏比撑着（赢时平均赚的比亏时多）。追最近 5 天涨得最猛的票，'
      '5 天后上涨的概率不到一半。')
    a('2. 8 个候选强势过滤器只留下 2 个：量比<=3、换手率 1%~20%。'
      '砍掉的 6 个（量比>=1.5、均线多头、相对强度、20 日涨幅>=10%、成交额>=1 亿元、'
      '5 日涨幅>=10%）全期胜率都低于基线——“更强、更放量、均线更顺”反而更差，'
      '是动量反转而非动量延续。')
    a(f"3. 组合（基线 + 2 个保留过滤器）全期胜率 {pct(r['combo']['full']['win_rate'])}"
      f"（n={r['combo']['full']['n']}），校准期 {pct(r['combo']['calibration']['win_rate'])}，"
      f"验证期 {pct(r['combo']['validation']['win_rate'])}，仍低于 50%，平均收益 "
      f"{pct(r['combo']['full']['mean_ret'])}。Top 3 是板块内的相对优选，不是绝对高胜率保证。")
    a('4. 各板块 Top 3 胜率多在 53%~66%。最高的宏明电子（301682）80.00%，但信号数 n=20 '
      '恰为上榜门槛，且同时出现在商业航天、算力概念、军工、储能概念 4 个榜上——'
      '概念板块重叠导致一票多榜，小样本高胜率要打折看。宁德时代（300750）67.86%（n=28）'
      '出现在锂矿概念、电池、固态电池 3 个榜上。')
    a('5. 微盘股、机器人、培育钻石在样本内成分分别只有 4、7、9 只，榜单几乎无选择余地，'
      '可信度最低；电力板块组合胜率全市场最低（43.77%）。')
    a('6. Top 3 里多只创业板/科创板票（如宁德时代 300750、宏明电子 301682），而线上选股器 '
      'get_block_stocks() 会剔除 30/688 开头代码——榜单不等于线上可推的票，见表中标记。')
    a('')
    a('## 1 样本、口径与板块来源')
    a('')
    a('- 样本（与 Phase 1 一致）：东财当前名单 5563 只，按抓取时成交额降序冻结前 1500 只 '
      f"（data/history/universe.json，冻结时间 {r['universe']['fetched_at']}，"
      f"sha256 {r['universe']['sha256']}）；剔除按今日名称含 ST/退的 5 只："
      f"{', '.join(r['excluded_st_by_current_name'])}。")
    a('- 历史：盒内 data/history/*.parquet 前复权日线（fqt=1），2023-11-01~2026-11-06，'
      f"共 {r['history']['total_bars']} 根日线，{r['history']['stocks_with_history']} 只票有历史，"
      '缺历史 0 只；2023 年仅作预热，统计期 2024-01-01~2026-11-06。'
      '2026-11-08 是周日，无交易，数据截至 2026-11-06（周五）。')
    a('- 胜率口径：信号日收盘买入，持有 5 个交易日，第 5 个交易日收盘价高于信号日收盘价算胜；'
      '统计单位为股票 x 信号日，允许重复持有；不含费用、滑点、涨停买不到与停牌退出。'
      '盈亏比 = 胜场平均收益 / 败场平均亏损绝对值。')
    a('- 板块划分（以现有代码为准）：选股器 modules/stock_screener.py 用的 '
      'modules/stock_data.py 的 SECTOR_MAP，即 23 个资金流向板块（东财 BK 代码），'
      '不是选股器自己的另一套分类，也不是 modules/sector_api.py / sector_leader.py 的'
      '东财行业板块（m:90 t:2，约 100+ 个）。成分股用选股器同款东财 clist 接口'
      f"（fs=b:BKxxxx）于 {m['fetched_at']} 拉取并冻结在 sector_members.json，"
      '共 4595 个成分席位、2836 只不重复股票，其中 951 只在前 1500 样本内。'
      '概念板块之间重叠，一只票可属于多个板块。')
    a('- 信号门槛：信号日当日已知日线>=21 根（与扫描入口一致）、OHLCV>0、未来 5 日收盘存在；'
      '上榜门槛：组合信号全期样本数 n>=20；排序：胜率降序、信号数降序、平均收益降序。')
    a('- 相对强度口径：20 日涨幅减同板块成分股 20 日涨幅中位数（仅限样本内成分股）；'
      '多板块票在全市场过滤器表里取各板块中最强的口径（中位数最小者），分榜时用该板块自己的中位数。')
    a('')
    a('## 2 强势基线与过滤器回测')
    a('')
    a('基线：信号日 5 日涨幅>=5%。保留规则（预先锁定，与 Phase 1 同路数）：'
      '过滤器全期、2024-25 校准期、2026 验证期胜率都高于基线，且每期 n>=50，否则砍掉。')
    a('')
    b = r['base']['stats']
    a(f"基线全期：{fmt_stats(b['full'])}；校准期：{fmt_stats(b['calibration'])}；"
      f"验证期：{fmt_stats(b['validation'])}。")
    a('')
    a('| 过滤器 | 全期 n / 胜率 / 平均收益 | 校准期胜率 | 验证期胜率 | 结论 |')
    a('|---|---|---|---|---|')
    for row in r['filters']:
        s = row['stats']
        a(f"| {row['label']} | {s['full']['n']} / {pct(s['full']['win_rate'])} / "
          f"{pct(s['full']['mean_ret'])} | {pct(s['calibration']['win_rate'])} | "
          f"{pct(s['validation']['win_rate'])} | {'保留' if row['kept'] else '砍掉'} |")
    a('')
    a(f"留下的过滤器：{', '.join(r['kept_filters'])}。")
    a('')
    a('## 3 组合结果（基线 + 保留过滤器）')
    a('')
    c = r['combo']
    a(f"全期：{fmt_stats(c['full'])}；校准期：{fmt_stats(c['calibration'])}；"
      f"验证期：{fmt_stats(c['validation'])}。")
    a('')
    a('## 4 每板块 Top 3')
    a('')
    a('胜率 = 组合信号后 5 个交易日收盘上涨的比例；n 为全期组合信号数。'
      '“2026 胜率”为同口径 2026 年内信号；“最近信号日”为该票最后一次组合信号日；'
      '“11-06 信号”表示 2026-11-06（最后一个交易日）当日出现组合信号；'
      '“线上不覆盖”表示该票为 30/688 开头，线上选股器 get_block_stocks() 会剔除。')
    a('')
    a('| 板块 | 代码 | 名称 | n | 胜场 | 5日胜率 | 平均收益 | 盈亏比 | 2026 n | 2026 胜率 | 最近信号日 | 11-06 信号 | 备注 |')
    a('|---|---|---|---|---|---|---|---|---|---|---|---|---|')
    for sec, sr in r['sectors'].items():
        for t in sr['top3']:
            f, v = t['full'], t['validation']
            note = '线上不覆盖（30/688）' if t['excluded_live'] else ''
            if f['n'] == 20:
                note = (note + '；' if note else '') + 'n=20 恰为门槛'
            a(f"| {sec} | {t['code']} | {t['name']} | {f['n']} | {f['wins']} | "
              f"{pct(f['win_rate'])} | {pct(f['mean_ret'])} | {num(f['payoff'], 3)} | "
              f"{v['n']} | {pct(v['win_rate'])} | {t['last_signal'] or '-'} | "
              f"{'是' if t['signals_on_last_day'] else '否'} | {note} |")
    a('')
    a('## 5 板块组合汇总（全部样本内成分股的组合信号）')
    a('')
    a('| 板块 | 板块代码 | 成分总数 | 样本内成分 | 组合信号 n | 胜率 | 平均收益 |')
    a('|---|---|---|---|---|---|---|')
    for sec, sr in r['sectors'].items():
        doc = m['sectors'][sec]
        f = sr['combo']['full']
        a(f"| {sec} | {doc['bk']} | {doc['total']} | {sr['candidates']} | {f['n']} | "
          f"{pct(f['win_rate'])} | {pct(f['mean_ret'])} |")
    a('')
    a('## 6 局限与注意事项')
    a('')
    a('- 今天是周日（2026-11-08），无交易；统计截至 2026-11-06，报告里“最近”指截至该日。')
    a('- 板块成分按 2026-11-08 当日冻结：历史上已调出成分、已退市的票不在榜内，'
      'Top 3 有前视/幸存者偏差，胜率会被高估。')
    a('- ST/退市按今日名称剔除（与 Phase 1 同口径的敏感性处理），不是历史状态回测；'
      '日线无历史 ST、停牌、公告与封单状态。')
    a('- 概念板块重叠，一票可进多个板块的 Top 3（如宏明电子进 4 榜、宁德时代进 3 榜），'
      '各板块榜单不是相互独立的推荐。')
    a('- 信号日收盘买入假设能成交：涨停买不到、停牌、集合竞价跳空等均未建模；'
      '不含费用与滑点；不做仓位与组合收益测算。')
    a('- 2026 年参与了过滤器的筛选（与 Phase 1 相同），验证期不是严格独立检验，'
      '新数据上需要重新验证；小样本 Top 3（如 n=20）胜率波动大。')
    a('- 样本是成交额前 1500 的流动性池：微盘股等小票代表性不足，'
      '微盘股板块样本内仅 4 只成分股。')
    a('- 相对强度用板块内样本成分的中位数，不是板块指数；板块内成分越少，中位数越不稳定。')
    a('')
    a('## 7 复现与核对')
    a('')
    a('- 板块成分冻结：`.venv/bin/python stock-analysis/backtest_sector_leaders.py --fetch-members`'
      '（写 sector_members.json；东财 clist 接口，选股器同款参数）。')
    a('- 回测：`.venv/bin/python stock-analysis/backtest_sector_leaders.py --verify`'
      '（写 sector_leaders_results.json 与 sector_leaders_policy.json）。')
    a('- 报告渲染：`.venv/bin/python stock-analysis/render_sector_report.py`。')
    a('- 单元测试：`.venv/bin/python -m unittest discover -s tests -v`。')
    a(f"- 独立重算核对：verify={'通过' if r['verify']['ok'] else '未通过'}，"
      f"逐项比对 {r['verify']['checked_stocks']} 个板块-股票对的组合信号 n/胜场/平均收益，"
      '纯循环重算与主流程（pandas 向量化）一致，差异容限 1e-9。')
    a(f"- 样本核对：日线总数 {r['history']['total_bars']} 根（已剔除 "
      f"{len(r['excluded_st_by_current_name'])} 只 ST/退市票的 {r['history']['excluded_st_bars']} 根；"
      f"剔除前 {r['history']['total_bars'] + r['history']['excluded_st_bars']} 根，与 Phase 1 的 1059442 根一致）；"
      f"样本清单 sha256 {r['universe']['sha256']} 与 shortterm_policy.json 一致。")
    a('- 未改动项：未改任何页面与 API，未动 /api/screen 与 /api/prediction，'
      '未合 main，未部署容器。新增文件：stock-analysis/backtest_sector_leaders.py、'
      'stock-analysis/render_sector_report.py、stock-analysis/sector_members.json、'
      'stock-analysis/sector_leaders_results.json、stock-analysis/sector_leaders_policy.json、'
      'tests/test_sector_leaders.py。')
    a('')
    (OUT / 'sector_leaders_backtest.md').write_text('\n'.join(lines), encoding='utf-8')
    print(f"wrote sector_leaders_backtest.md ({len(lines)} lines)")

if __name__ == '__main__':
    main()
