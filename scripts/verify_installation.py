"""Create a local acceptance report without including credentials or raw data."""
import asyncio
from datetime import date
import json
from pathlib import Path
import time
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from quant.config import settings
from quant.db import connect
from quant.domain import ScreenRequest
from quant.services import bars,screen,status,clean


async def check_mcp():
    url=f'http://127.0.0.1:{settings().mcp_port}/mcp'
    async with streamablehttp_client(url) as (reader,writer,_):
        async with ClientSession(reader,writer) as session:
            await session.initialize()
            tools=await session.list_tools()
            result=await session.call_tool('search_securities',{'query':'晨光生物'})
            text='\n'.join(item.text for item in result.content if hasattr(item,'text'))
            assert not result.isError and '300138.SZ' in text
            return {'transport':'streamable-http','tools':[t.name for t in tools.tools],'actual_search_passed':True}


def main():
    samples=[('601318.SH','2026-06-29',45.92),('300176.SZ','2026-08-12',4.66),
             ('300980.SZ','2026-09-29',16.25),('300803.SZ','2026-09-21',56.23),
             ('002961.SZ','2026-09-22',14.03)]
    checks=[]
    for symbol,target,expected in samples:
        data=bars(symbol,date(2026,10,9),200,'qfq')
        actual=next(row['low'] for row in data['bars'] if row['trade_date']==target)
        checks.append({'symbol':symbol,'date':target,'anchor':'2026-10-09','qfq_low':actual,'expected':expected,'passed':actual==expected})
    assert all(r['passed'] for r in checks)
    with connect() as conn:
        coverage=conn.execute('''SELECT s.market,count(DISTINCT b.symbol) AS securities,count(*) AS records,
            min(b.trade_date) AS earliest,max(b.trade_date) AS latest
            FROM daily_bar b JOIN security s USING(symbol) GROUP BY s.market ORDER BY s.market''').fetchall()
    begin=time.perf_counter()
    selection=screen(ScreenRequest(min_amount=1e8,min_volume_ratio=1.5,require_breakout=True))
    elapsed=round(time.perf_counter()-begin,2)
    api=httpx.get(f'http://127.0.0.1:{settings().port}/api/status',timeout=30)
    api.raise_for_status()
    assert api.json()['latest_trade_date']==selection['effective_date']
    result=clean({'status':status(),'coverage':coverage,'qfq_samples':checks,
                  'full_market_screen':{'effective_date':selection['effective_date'],'scanned':selection['scanned'],
                    'matched':selection['matched'],'seconds':elapsed},'http_api':True,
                  'mcp':asyncio.run(check_mcp()),'notes':['缺少的17份源文件已记录，不伪造行情。','盘中或覆盖不足日期不自动用于默认盘后选股。']})
    output=Path('artifacts/acceptance.json');output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('status',)},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
