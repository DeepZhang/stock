# 短线选股（stock）

## 项目介绍
本仓库是一个 A 股选股研究项目：以旧版突破策略为起点，新增 Phase 1 短线选股引擎（涨停次日主策略 + 超跌反弹副策略），并用历史日线做回测验证。仅做选股研究，不做交易。

## 短线选股功能
- 旧突破策略（保留）：`modules/stock_screener.py` 的 `run_screen`，网站 `/api/screen` 仍返回扁平 list 并写 `data/stock_picks.json`。
- 涨停次日（主策略）：涨停信号（主板 9.5%、创业板/科创板 19%），剔除一字板与 ST/退市等不可交易情形；保留过滤为换手 1%-20%、量比≤3；每只票给出卖出计划，候选按成交额取 Top10，标记 `research_only`。
- 超跌反弹（副策略）：双信号（RSI≤20 且 5 日跌幅≥8%），持有 3-5 天口径回测。
- 回测与报告：`stock-analysis/backtest_shortterm.py` 输出 `stock-analysis/shortterm_backtest.md` 与 `stock-analysis/shortterm_results.json`；过滤器逐个报告前后胜率，胜率未提升或 n<50 的过滤器已砍掉。

## 怎么运行
- 准备环境：复用仓库已有依赖（pandas、flask 等），使用仓库虚拟环境 `.venv`。
- 拉历史数据（东财前复权日线，断点续跑，存入 gitignore 的 `data/history/`，不提交）：`.venv/bin/python stock-analysis/pull_history.py --batch-size 100 --batches 1`；全市场补齐加 `--all-stocks`，可重复运行续跑。
- 跑回测：`.venv/bin/python stock-analysis/backtest_shortterm.py`
- 固定日期回归（2026-11-06）：`.venv/bin/python stock-analysis/backtest_shortterm.py --regression`
- 单元测试：`.venv/bin/python -m unittest discover -s tests`
- 网站回归：`.venv/bin/python tests/check_existing_site.py`（本地 Flask，42 个现有路由与基线一致）

## 回测结果摘要
样本：东财名单 5563 只中按抓取时成交额降序冻结前 1500 只，历史区间 2023-11-01~2026-11-06，统计信号期 2024-01-01~2026-11-06，前复权收盘价。统计单位为股票×信号日，未扣费用与滑点。
- 涨停次日组合：n=10762，胜率 63.03%，平均收益 +2.35%，盈亏比 1.58；原始信号 62.17%（n=16659）。2026 年单期：62.02%（n=4808）。
- 超跌反弹组合：n=3213，胜率 69.03%，平均收益 +7.64%，盈亏比 1.89；原始信号 50.69%（n=101174）。2026 年单期：53.77%（n=385）。
- 固定日期 2026-11-06 全市场回归：涨停 16、超跌 3、旧突破 19；与 v2 参考（33/14/30）的差异源于数据源、股票池与规则不同，详见回测报告，未改数字凑数。
- 重要限制：上述胜率是信号收盘至未来收盘的条件收益，不能当作交易胜率；按次日开盘买入并遵守 T+1 的可执行退出情景，平均收益为 -0.083%，尚未证明可交易盈利。封单与历史公告过滤因数据不足未启用；2026 年参与了过滤取舍，属探索性检验，需新数据复核。详见 `stock-analysis/shortterm_backtest.md`。

## 免责声明
本项目仅供选股研究参考，不构成任何买卖建议，不自动交易，不保证收益。历史回测不代表未来表现，投资有风险，决策请自行负责。

## 未做的事（Phase 3）
`/api/shortterm` 与短线选股页面尚未实现；`/api/prediction` 不存在。本分支不合主分支、不部署、不碰线上网站。
