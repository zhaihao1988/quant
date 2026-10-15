# quant_platform 代码评估

检查文件：scripts/sync_daily_data.py、db/models.py、multi_strategy_screener.py、strategies/trend_strategies.py、strategies/breakout_strategy.py。

保留的业务意图：证券列表、每日OHLCVA、换手率、历史截止日、MA趋势、突破与量能筛选、命中解释。

本次重写：

1. 旧AkShare入口`adjust='qfq'`直接存行情；新项目保留不复权原始行情与独立权息，查询时计算并注明基准日。
2. 旧StockDaily volume为Integer且注明手；新项目bigint股，与通达信原始单位一致，金额为元。
3. 旧同步按最后日期加1追加；新项目哈希检测源文件，变化后对历史行upsert，避免遗漏修订及重复。
4. 旧换手率依赖供应商直接返回；新项目从可追溯股本观察计算，缺失不伪补。
5. 旧策略继承和日周月表耦合；新项目纯领域函数，API/MCP共用服务。复杂缠论与旧基本面大模型流程暂不迁入。
6. 突破比较此前N日最高价，排除当日；放量比较此前5日均量，排除当日。
7. 新闻与产业图谱预留独立表，第一版仅提供本地分类证据和手动新闻匹配；后续逐步扩展。

未迁移外部供应商认证信息、旧数据库凭据、嵌入模型和收费数据依赖。旧仓库仅在只读参考目录检查，新工程独立Git仓库。
