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
    def daily_bars(symbol:str,end:str|None=None,limit:int=200,adjustment:str='qfq') -> dict:
        """查询日线、成交量（股）、成交额（元）与有依据的估算换手率；end为YYYY-MM-DD。"""
        if not 1<=limit<=5000:raise ValueError('limit须为1至5000')
        return services.bars(symbol,date.fromisoformat(end) if end else None,limit,adjustment)

    @mcp.tool()
    def technical_screen(parameters:dict) -> dict:
        """组合技术筛选：as_of、symbols、taxonomy_id、min_amount、min_volume_ratio、min_turnover、max_turnover、require_uptrend、require_breakout、adjustment。金额元、换手率百分数。"""
        return services.screen(ScreenRequest.model_validate(parameters))

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def list_stock_pools() -> list:
        """列出本地行业和股票池，历史成分仅覆盖已采集快照。"""
        return services.taxonomies()

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
