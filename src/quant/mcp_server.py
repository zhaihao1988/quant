from datetime import date
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from .config import settings
from . import services
from .domain import ScreenRequest


def create_server():
    cfg=settings()
    mcp=FastMCP('quant',instructions='本地通达信研究工具。价格注明复权口径；日线成交量倍数不是盘中量比；候选股票不等于投资建议。',host='127.0.0.1',port=cfg.mcp_port)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def data_status() -> dict:
        """查询本地数据覆盖、最近交易日及导入任务状态。"""
        return services.status()

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def search_securities(query:str) -> list:
        """按A股代码或名称检索本地证券。"""
        return services.securities(query)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def daily_bars(symbol:str,end:str|None=None,limit:int=200,adjustment:str='qfq',start:str|None=None,
                   timeframe:str='daily',with_indicators:bool=False) -> dict:
        """查询日/周/月K线。timeframe为daily/weekly/monthly，with_indicators返回MA、MACD和量均线。日期YYYY-MM-DD。成交量股、金额元，周月换手率按日累加。"""
        if not 1<=limit<=20000:raise ValueError('limit须为1至20000')
        if with_indicators or timeframe!='daily':
            from .charting import market_data
            return market_data(symbol,date.fromisoformat(end) if end else None,date.fromisoformat(start) if start else None,limit,adjustment,timeframe)
        return services.bars(symbol,date.fromisoformat(end) if end else None,limit,adjustment,date.fromisoformat(start) if start else None)

    @mcp.tool()
    def technical_screen(parameters:dict) -> dict:
        """组合技术筛选：as_of、symbols、taxonomy_id、min_amount、min_volume_ratio、min_turnover、max_turnover、require_uptrend、require_breakout、adjustment。金额元、换手率百分数。"""
        return services.screen(ScreenRequest.model_validate(parameters))

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def list_stock_pools() -> list:
        """列出本地行业和股票池，历史成分仅覆盖已采集快照。"""
        return services.taxonomies()

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def multi_strategy_catalog() -> dict:
        """查看旧项目原7个技术策略及默认参数。算法保留，前复权、量单位手、全部历史。"""
        from .multiscreener import strategy_catalog
        return strategy_catalog()

    @mcp.tool()
    def start_multi_screen(parameters:dict) -> dict:
        """后台运行原多策略选股；as_of、symbols(如300138.SZ)、taxonomy_id、strategies(类名列表)。返回任务ID，用multi_screen_status查询。各策略独立汇总，不取交集；基本面未接入。"""
        from threading import Thread
        from .multiscreener import MultiScreenRequest
        from .multi_jobs import reserve,run_job
        run_id,reused=reserve(MultiScreenRequest.model_validate(parameters))
        if not reused:Thread(target=run_job,args=(run_id,),daemon=True).start()
        return {'id':run_id,'reused':reused}

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def multi_screen_status(run_id:str) -> dict:
        """查询多策略任务进度、技术信号、数据覆盖和错误。"""
        from uuid import UUID
        from .multi_jobs import snapshot
        return snapshot(UUID(run_id))

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def industry_graph(query:str) -> dict:
        """查询公司与行业分类关系及证据；不将分类归属推断为上下游。"""
        return services.graph(query)

    @mcp.tool()
    def analyze_news(title:str,content:str='',source_url:str|None=None) -> dict:
        """保存手动新闻并按本地实体名称匹配股票，输出技术指标；当前为规则关联，不调用外部AI。"""
        if not title.strip() or len(title)>500 or len(content)>100000:raise ValueError('新闻长度无效')
        return services.analyze_news(title,content,source_url)

    return mcp
