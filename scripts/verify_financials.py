"""Read-only production acceptance of financial sources, indicators and saved technical results."""
import asyncio,json,time
from datetime import date
from pathlib import Path
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from quant.db import connect


def unpack(r):
    assert not r.isError,r
    return r.structuredContent or json.loads(next(c.text for c in r.content if hasattr(c,'text')))


async def mcp_check():
    async with streamable_http_client('http://127.0.0.1:8766/mcp') as (reader,writer,_):
        async with ClientSession(reader,writer) as session:
            await session.initialize()
            tools=await session.list_tools()
            assert len(tools.tools)==13
            report=unpack(await session.call_tool('financial_reports',{'symbol':'600036.SH','limit':1,'field_ids':[96,502]}))
            assert report['profile']['type']=='bank'
            metric=unpack(await session.call_tool('stock_fundamentals',{'symbol':'300138.SZ','as_of':'2026-10-16'}))
            assert metric['pe'] is not None and metric['availability_policy']=='actual_collection_only'
            return {'tools':len(tools.tools),'bank_profile':report['profile']['type'],'sample_pe':metric['pe']}


def main():
    base='http://127.0.0.1:8765';start=time.perf_counter()
    with httpx.Client(base_url=base,timeout=180) as client:
        status=client.get('/api/financials/status');status.raise_for_status();status=status.json()
        assert status['stored_report_versions']==303561 and status['quarantined_source_records']==72
        samples={}
        for symbol,kind in [('300138.SZ','general'),('600036.SH','bank'),('600030.SH','securities'),('601318.SH','insurance')]:
            report=client.get('/api/financials/'+symbol,params={'limit':1,'fields':'FN96,FN502,FN271'});report.raise_for_status()
            assert report.json()['profile']['type']==kind
            metric=client.get('/api/fundamentals/'+symbol,params={'as_of':'2026-10-16'});metric.raise_for_status();metric=metric.json()
            assert metric['market_cap']>0 and metric['pe']>0
            samples[symbol]={k:metric[k] for k in ('market_cap','pe','pb','revenue_growth_yoy','profit_growth_yoy','price_date')}
        early=client.get('/api/fundamentals/300138.SZ?as_of=2026-10-14').json()
        assert early['pe'] is None and early['fundamental_states']['pe']=='not_collected_as_of'
        assert client.get('/api/fundamentals/INVALID').status_code==400
        start_snapshot=time.perf_counter()
        job=client.get('/api/multi-screens/c55ce989-a78e-4eff-a6dd-a745aab9b835');job.raise_for_status();job=job.json()
        snapshot_seconds=round(time.perf_counter()-start_snapshot,3)
        signals=job['result']['signals']
        assert job['processed']==5594 and all(s['pe'] is None for s in signals)
    with connect() as conn:
        fingerprint=conn.execute('SELECT count(*) AS n,md5(string_agg(md5(result::text),%s ORDER BY ordinal)) AS digest FROM multi_screen_item WHERE run_id=%s',('', 'c55ce989-a78e-4eff-a6dd-a745aab9b835')).fetchone()
        assert fingerprint==json.loads(Path('artifacts/finance-upgrade-checkpoint.json').read_text())
        assert conn.execute("SELECT count(*) AS n FROM financial_report WHERE review_state='pending_conflict' AND standard<>'{}'::jsonb").fetchone()['n']==0
    result={'coverage':{k:status[k] for k in ('stored_report_versions','covered_securities','latest_report_date','quarantined_source_records')},'samples':samples,'old_task':{'processed':job['processed'],'signals':len(signals),'all_pe_unavailable_before_collection':True,'fingerprint_unchanged':fingerprint,'snapshot_seconds':snapshot_seconds,'errors':len(job['result']['errors'])},'mcp':asyncio.run(mcp_check()),'seconds':round(time.perf_counter()-start,3)}
    Path('artifacts/financial-acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
