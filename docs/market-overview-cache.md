# 行情总览缓存修复验证（2026-11-08）

分支 `fix/market-overview-cache`，起点 `main eb195bf`；参照 `559b9e7` 的选股缓存模式。

## 修改与边界

- `/api/market-overview` 立即返回 `data/market_overview.json`，保留原五个顶层字段。缓存缺失/损坏时返回空状态，首次没有数据不填写行情或更新时间。
- 缓存有效期 30 秒；过期后请求触发后台 daemon 线程，同一 Flask 进程共享锁和刷新状态，刷新期间继续读旧缓存。临时文件写完后原子替换，刷新异常、无效 JSON 数值或写入失败保留旧缓存并释放状态。
- 与 `/api/screen` 一样，单例范围是单个进程；多进程各自有锁。
- 两个 market 页面处理 HTTP 失败、非 JSON、请求异常和空数据，统一显示“数据加载中，稍后重试”，沿用现有 60 秒轮询。修正 `market2.html` 原有 Python 风格条件表达式，避免整段 JS 无法解析。
- 上游涨跌停统计的耗时与 `NoneType` 报错仍可能出现，但已移出请求线程；本次不改变行情统计规则。

## 重新计时

使用 `time.perf_counter()`，按次输出 JSON；每个分项调用前清空模块缓存：

```sh
.venv/bin/python scripts/benchmark_market_overview.py --runs 2
.venv/bin/python scripts/benchmark_market_overview.py --url http://127.0.0.1:5000/api/market-overview --runs 2
```

5000 为原 main 预览，仅作只读访问，未重启或结束进程。

| 原实现/数据源 | 第一次（秒） | 第二次（秒） |
| --- | ---: | ---: |
| 5000 `/api/market-overview`，均 HTTP 200 JSON | 30.093209 | 1.024730 |
| `get_index_data` | 2.973031 | 0.968407 |
| `get_limit_stats` | 20.500298 | 19.833225 |
| `get_market_fund_flow` | 0.994566 | 1.043275 |
| `get_sector_flow_analysis` | 0.000092 | 0.000282 |

两次涨跌停统计均打印 `'NoneType' object has no attribute 'get'`。真实行情稀疏与数据源缓存会影响耗时，不能将一次快响应当作稳定延迟。

修改后用 `werkzeug.make_server` 在 127.0.0.1:5001 启动当前 app，缓存指向临时目录，不 mock 行情上游：

- 冷启动/刷新中三次：0.008653、0.001032、0.000544 秒，均 200、`application/json`，严格 JSON 可解析。
- 后台完成后真实缓存三次：0.001652、0.001210、0.000876 秒，均 200，包含真实三项指数和更新时间。
- `/market`、`/shortterm`、`/api/shortterm`、`/api/screen` 均 200；验证后关闭自建 5001 服务。
- 原始响应明细保存于不入库的 `data/history/market_overview_verification.json`。

## 提交前自测

- `.venv/bin/python -m unittest discover -s tests`：35 项通过，包含 6 项新增行情缓存测试，覆盖冷启动、损坏缓存、8 请求并发单例、慢上游不阻塞、发布、异常保留/重试、线程启动失败及磁盘写入失败。
- `node tests/check_market_overview_pages.cjs`：两个页面的 HTTP 错误、JSON 解析错误、请求失败、空数据和正常渲染通过。
- `.venv/bin/python tests/check_existing_site.py`：44 路由本地 HTTP 回归通过，既有路由与脚本固定基线的状态/JSON 顶层类型一致。既有 `/api/review` 缺模块导致 500，两版一致，未修复该无关问题。
- `.venv/bin/python stock-analysis/backtest_shortterm.py --regression`：2026-11-06 全量冻结名单 5563 项，5196 只有足够当日日线；超跌 3、涨停 16、旧突破 19，与已有报告一致。任务书的 v2 参考 33/14/30 与本库规则、数据源和股票池不同，已有报告记录归因，本次未修改阈值或数字。
- `.venv/bin/python stock-analysis/backtest_shortterm.py` 生成报告并复现原统计；`.venv/bin/python tests/check_backtest_results.py`：146 组统计、119658 条事件独立重算通过。生成结果与原报告相同，保留原报告及历史验证记录。
- `.venv/bin/python scripts/run_shortterm.py --date 2026-11-06 --offline`：16/3 候选，页面展示 10/3；`node tests/check_shortterm_pages.cjs` 通过。
- Python 编译、`git diff --check`、敏感配置追踪检查通过。

不合 main，不部署 scnet，不改 sing-box，不结束 5000 预览进程。
