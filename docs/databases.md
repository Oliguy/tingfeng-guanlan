# 数据库标准 v1

数据文件在代码、虚拟环境和安装包之外。配置版本为 `guanlan.config.v1`；消费合同为 `guanlan.database-contract.v1`，完整表/字段/类型/主键元数据位于 `src/guanlan_data/contracts/databases.v1.json`。

| 数据角色 | 默认位置 | 用途 |
|---|---|---|
| equity | `market_root/market/equity_daily_raw.sqlite` | 股票身份、日线、复权、交易日历与来源修订 |
| etf | `market_root/market_etf/industry_etf_observer.sqlite` | ETF、分组、代表基金、日线与质量状态 |
| status | `market_root/market_facts/equity_status_daily.sqlite` | 训练所需逐日 ST、退市状态与历史名称 |
| business | `business_db` | 分类、规则、档案与必要证据关联 |
| industry | `industry_db` | 既有行业指数结果 |
| support | `support_db` | 限价证据、名称与补充回执 |
| catalog | `collection_root/catalog.sqlite` | 题材版本、命令幂等回执 |
| results | `collection_root/results.sqlite` | 已发布行业/题材、共享周线、指标与来源引用 |
| kph | `market_root/market_events/kph_limit_up.sqlite` | 可选收盘涨停证据 |

`training_db` 默认 `state_root/training.sqlite`，保存训练会话、操作、请求编号与笔记。`jobs_root` 默认 `state_root/jobs`；外部中央任务仍以提供者的原任务编号为准。`calendar_root`、`analysis_root` 可以显式指定，均视为只读输入。

## 权限与初始化

市场、分类、行业、支持证据、日历、分析源一律用 SQLite `mode=ro` 打开，并启用连接内 `query_only`。只读连接拒绝关闭该限制、附加其他数据库和写入 PRAGMA。不存在的源库不会被偷偷建成空文件。

可写连接只允许位于 `state_root`，该目录不能与 `market_root` 相互包含，配置的其他保护源也不能位于其中。训练库在第一次训练操作时初始化。题材库在明确保存时初始化。若 `collection_root` 指向状态目录外的既有原库，它只读；题材修改必须通过该原库的更新提供者。

程序不自动迁移或修复源库。`doctor` 只读取结构元数据，报告缺库、缺表、缺字段、字段类型亲和性、主键及版本错误。ETF 消费支持 schema 3 和 4；版本更高或过低均拒绝，不做“尽量兼容”的静默计算。结构检查通过不代表内容完整、最新或可靠。

## 日期、代码、单位和空值

- 股票代码采用 `600001.SH`、`000001.SZ`、`300001.SZ` 等规范；ETF 自身代码按现有合同为六位字符串，保留前导零。
- 公开 API 日期用 `YYYY-MM-DD`；原 KPH 来源日期保留 `YYYYMMDD` 并在适配层转换。日历必须来自数据源，不能用“周一到周五”替代真实交易日；演示日历明确例外。
- 股票价格单位元/股；`vol_lot` 是手（100 股），`amount_thousand_cny` 是千元。界面适配后股票/ETF `volume` 为股、`amount` 为元。
- `pct_chg` 是百分数，`group_return`、`daily_return` 等收益是小数比例。不能把 `1.2` 和 `0.012` 混用。
- 复权原日线、累计因子、显示调整和连续指数各保留其原口径。来源缺失、停牌、日历未知、未生成指标各有状态，不能填零或用未来资料补历史。
- 原始事实归数据生产者；指标/集合采用已有版本、哈希与来源引用。涉及 `classification_working` 和官方证据文件的存量数据，还需要其原来的相对资源与指针；本程序不会复制它们。

## 新数据接入与已有记录

新数据提供者应按上述消费合同生成自己拥有的数据，再用 `doctor` 和代表查询验收；不要仅凭表名一致就声称兼容。禁止把 SQL 整库转储、真实行情或用户训练库提交到本仓库。

已有训练状态如需接入，应把服务的 `state_root` 和 `training_db` 指向它的既有独立归属目录，并确认只保留一个写入服务。若原训练库与受保护市场根混放，应继续由原服务通过 API 使用；本版不会为接入自动复制或搬迁原库。多个客户端连接同一服务共享这一份用户状态，本版不是多租户账户系统。

代码升级只替换程序。保留配置和状态路径；先检查数据合同，再启动。发生来源漂移时按提示暂停/核对，不用旧状态覆盖新记录，不删除锁文件来绕过任务归属。
