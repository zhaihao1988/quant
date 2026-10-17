# Quant · 本地选股工作台

独立 Python 工程，通达信本地文件 → PostgreSQL → 业务服务 → 浏览器界面 / MCP。

## 当前能力

每日盘后使用：在通达信下载完成后，进入“多策略选股”点击“开始选股”。自动检查更新、复用相同输入结果、计算并归档；支持最新财务/当次归档、失败项补算、历史分页和导出。详见[每日工作流](docs/daily-workflow.md)。

- 沪深北 A 股不复权日线、成交量（股）、成交额（元）；文件哈希去重、历史修订 upsert、逐文件事务与任务进度。
- 权息原始事件；类别1普通仿射前复权，基准日为查询/筛选截止交易日。扩缩股类别11/12暂拒绝前复权，避免默算错误。
- 基础股本快照与已核对的类别5股本事件；换手率使用最近可用历史依据估算，缺失保留空，不把当前股本回填整个历史。
- 技术组合筛选：成交额、此前5日均量倍数、换手率、MA多头排列、突破此前N日最高价。输出条件解释、价格口径和日期。
- 行业与大型指数股票池快照、带来源的分类关系图；手动新闻名称匹配及候选技术指标。
- 本机界面及官方 MCP Python SDK stdio / Streamable HTTP 服务。
- A股股本原始快照、专业财务报告版本与全市场指标缓存；多策略结果始终附加最新采集数据，网页“更新股本及财务”独立更新，见[财务说明](docs/financial-data.md)。
- 个股行情默认展示全部历史，支持起止日期、120/250/750日快捷范围及50条明细分页；接口返回本地覆盖和截断标记。
- 日/周/月K线、均线、MACD和成交量联动看盘，以及逐股同步对比与失败重试，详见[看盘与同步说明](docs/workstation-and-sync.md)。

## Windows 本机启动

已配置环境可运行：

```powershell
cd D:\projects\quant
.\start.ps1
# 浏览器：http://127.0.0.1:8765
```

独立命令：

```powershell
.\.venv\Scripts\quant.exe init-db
.\.venv\Scripts\quant.exe import-tdx --symbols 300138.SZ 600036.SH 601318.SH
.\.venv\Scripts\quant.exe import-tdx   # 全部本地A股；不导入ETF/指数/港美股
.\.venv\Scripts\quant.exe status
.\.venv\Scripts\quant.exe refresh-fundamentals  # 更新本地股本、专业财务包及全市场指标
.\.venv\Scripts\quant.exe serve
.\.venv\Scripts\quant.exe mcp          # stdio
.\.venv\Scripts\quant.exe mcp --transport streamable-http
# HTTP MCP：http://127.0.0.1:8766/mcp
```

首次在其他电脑安装：Python 3.12+，`python -m venv .venv`，`.venv\Scripts\python -m pip install -e ".[dev]"`，复制 `.env.example` 为 `.env`，配置自己的 PostgreSQL，再执行 `quant init-db`。

此电脑的 PostgreSQL 为官方 EDB Windows 独立二进制，目录 `.runtime/pgsql`、数据目录 `.runtime/pgdata`、端口55432，仅监听127.0.0.1。数据库quant，应用账号quant_app非超级用户。运行时密码与 `.env` 均不进入Git。`scripts/bootstrap_db.py`启动已有集群；不注册系统自启动服务。备份使用 `pg_dump`，不要在服务运行时直接复制pgdata。

## MCP 客户端配置

```json
{
  "mcpServers": {
    "quant": {
      "command": "D:\\projects\\quant\\.venv\\Scripts\\python.exe",
      "args": ["-m", "quant.cli", "mcp"],
      "cwd": "D:\\projects\\quant"
    }
  }
}
```

需先启动数据库。stdio工具清单：`data_status`、`search_securities`、`daily_bars`、`technical_screen`、`list_stock_pools`、`industry_graph`、`analyze_news`、`multi_strategy_catalog`、`start_multi_screen`、`multi_screen_status`、`financial_data_status`、`financial_reports`、`stock_fundamentals`。不提供任意SQL执行或交易下单工具。

## 数据与计算口径

详见 [docs/data-contract.md](docs/data-contract.md)。通达信下载与本项目导入为两个步骤。项目不绕过客户端权限、不后台请求行情供应商。

股本事件并非全部类别已验证：当前仅类别5提供历史分母，其他事件保留原文数值。历史换手率可能因事件覆盖不全而失真，所有结果返回来源。新增版本化股本保留源文件更新时间和实际采集时间，不将GXRQ认作股本生效日；结果表“参考换手率”使用最新流通A股本，不等同于历史当时换手率。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.runtime/pytest
```

集成测试在已配置数据库中新建唯一测试schema，结束后仅移除该schema，不改动真实行情。覆盖重复导入、历史修订、截断文件拒绝、未来股本/权息隔离、接口和MCP工具注册。

## 与旧项目的关系

本地行情接入、持久化与接口独立实现。原有组合条件选股继续保留；另按用户要求原样移植 `zhaihao1988/quant_platform` 的 MultiStrategyScreener 七个技术策略，公共接口作最小适配，不引入旧ORM和外部数据供应商。多策略使用全部历史、通达信前复权及成交量手，周月输入按旧聚合规则重建；基本面已接入通达信专业财务包，见 [财务数据说明](docs/financial-data.md)。网页“多策略选股”可选择策略、股票池和指定股票，日期自动采用同步后的最新可用交易日，默认4进程并行，显示进度和错误，停止后可保留已完成结果并继续剩余股票。结果表按旧项目列出基本面字段，缺失显示具体原因，支持排序，技术详情仍保存在数据库但不在结果表展开。规则、测速、验证和MCP接口见 [docs/multiscreener-migration-review.md](docs/multiscreener-migration-review.md)；最初的旧代码分析见 [docs/migration-review.md](docs/migration-review.md)。

## 调研问答

股票资料库可采集全部A股近一年巨潮调研记录，使用硅基流动Qwen3-8B校验、解析并保存原始PDF、全文和问答。个股详情按公告时间展示，可展开回答和查看原文；后台支持暂停、继续及失败重试，不统计模型用量。配置与存储见 [调研问答说明](docs/research-documents.md)。

## 尚未完成

- 港美股、分钟线导入；它们不因目录存在就自动视为可用。
- 自动新闻源、新闻大模型分析、产品及上下游关系建设、配套Skill。
- 历史指数成分回补、首发版财务数据、跨年度交易日历维护、总回报回测、交易执行。
- 图谱当前仅分类归属；手动新闻为名称规则匹配，不能解读成真实产业受益结论。


### 新闻、事件与资料检索

新闻页提供三路公开快讯增量更新、原文搜索、BGE语义检索和Qwen事件分析；个股详情展示相关事件及可能催化。后台开关和进度存入数据库，启动本地服务后继续；暂停不删除资料。首次资料索引较多，进度和失败原因在新闻页显示。新闻缺口单独记录，无法保证来源未提供的历史完整。相似度不是受益证据，分析不代表已证明股价上涨因果；原技术策略不变。

部署前需在当前数据库以管理员启用 `CREATE EXTENSION vector WITH SCHEMA public;`，再运行迁移。当前Windows便携PostgreSQL已安装pgvector 0.8.7；新机器须先安装匹配PG主版本的扩展。BGE仅本地加载分词器，远程API密钥沿用忽略文件中的QUANT_SILICONFLOW_API_KEY，不记录token与费用统计。索引和分析快照、原文、队列均包含在数据库备份中。

详细更新契约和边界：[增量新闻与证据实施方案](docs/增量新闻与证据实施方案.md)。
