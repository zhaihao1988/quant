# Quant · 本地选股工作台

独立 Python 工程，通达信本地文件 → PostgreSQL → 业务服务 → 浏览器界面 / MCP。

## 当前能力

- 沪深北 A 股不复权日线、成交量（股）、成交额（元）；文件哈希去重、历史修订 upsert、逐文件事务与任务进度。
- 权息原始事件；类别1普通仿射前复权，基准日为查询/筛选截止交易日。扩缩股类别11/12暂拒绝前复权，避免默算错误。
- 基础股本快照与已核对的类别5股本事件；换手率使用最近可用历史依据估算，缺失保留空，不把当前股本回填整个历史。
- 技术组合筛选：成交额、此前5日均量倍数、换手率、MA多头排列、突破此前N日最高价。输出条件解释、价格口径和日期。
- 行业与大型指数股票池快照、带来源的分类关系图；手动新闻名称匹配及候选技术指标。
- 本机界面及官方 MCP Python SDK stdio / Streamable HTTP 服务。

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

需先启动数据库。stdio工具清单：`data_status`、`search_securities`、`daily_bars`、`technical_screen`、`list_stock_pools`、`industry_graph`、`analyze_news`。不提供任意SQL执行或交易下单工具。

## 数据与计算口径

详见 [docs/data-contract.md](docs/data-contract.md)。通达信下载与本项目导入为两个步骤。项目不绕过客户端权限、不后台请求行情供应商。

股本事件并非全部类别已验证：当前仅类别5提供历史分母，其他事件保留原文数值。历史换手率可能因事件覆盖不全而失真，所有结果返回来源，严谨研究仍需抽样核验。快照的业务日采用源文件更新时间，采集时间单独保存，不把“更新文件”当作“该日刚公告”。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.runtime/pytest
```

集成测试在已配置数据库中新建唯一测试schema，结束后仅移除该schema，不改动真实行情。覆盖重复导入、历史修订、截断文件拒绝、未来股本/权息隔离、接口和MCP工具注册。

## 与旧项目的关系

参考 `zhaihao1988/quant_platform` 中的行情字段、MA趋势和突破思路，重新实现本地接入、持久化和接口；未复制其ORM、策略继承结构或外部供应商依赖。旧代码分析见 [docs/migration-review.md](docs/migration-review.md)。

## 尚未完成

- 港美股、分钟线、专业财务包导入；它们不因目录存在就自动视为可用。
- 自动新闻源、大模型分析、产品及上下游关系建设、配套Skill。
- 历史指数成分回补、首发版财务数据、完整交易日历、总回报回测、交易执行。
- 图谱当前仅分类归属；手动新闻为名称规则匹配，不能解读成真实产业受益结论。
