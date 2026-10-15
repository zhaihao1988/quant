"""Run against a temporary schema in the configured local database."""
import os
from datetime import date
import struct
import uuid
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode,quote
import pytest
import psycopg
from psycopg import sql
from fastapi.testclient import TestClient


@pytest.fixture()
def isolated_db(monkeypatch):
    from quant.config import settings
    from quant.db import migrate,connect
    dsn=settings().database_url
    schema='quant_test_'+uuid.uuid4().hex
    with psycopg.connect(dsn,autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    parsed=urlsplit(dsn)
    query=dict(parse_qsl(parsed.query));query['options']='-c search_path='+schema
    temporary=urlunsplit(parsed._replace(query=urlencode(query,quote_via=quote)))
    monkeypatch.setenv('QUANT_DATABASE_URL',temporary)
    try:
        migrate()
        with connect() as conn:
            conn.execute("INSERT INTO security(symbol,code,market,name) VALUES ('300138.SZ','300138','SZ','测试晨光')")
        yield
    finally:
        with psycopg.connect(dsn,autocommit=True) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def test_idempotence_and_historical_revision(isolated_db,tmp_path):
    from quant.ingest import import_day
    from quant.db import connect
    p=tmp_path/'sz300138.day'
    def record(close):return struct.pack('<IIIIIfII',20261008,1000,1200,900,close,100000,10000,0)
    p.write_bytes(record(1100))
    assert import_day(p,'300138.SZ')==(1,False)
    assert import_day(p,'300138.SZ')==(0,True)
    p.write_bytes(record(1150))
    assert import_day(p,'300138.SZ')==(1,False)
    with connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM daily_bar').fetchone()['n']==1
        assert float(conn.execute('SELECT close FROM daily_bar').fetchone()['close'])==11.5
    p.write_bytes(b'')
    with pytest.raises(ValueError,match='减少'):import_day(p,'300138.SZ')


def test_no_future_share_snapshot_or_action(isolated_db):
    from quant.db import connect
    from quant.services import bars
    with connect() as conn:
        conn.execute("INSERT INTO daily_bar(symbol,trade_date,open,high,low,close,volume,amount) VALUES ('300138.SZ','2026-10-08',10,10,10,10,100,1000)")
        conn.execute("INSERT INTO share_observation(symbol,effective_date,float_shares,source,quality) VALUES ('300138.SZ','2026-10-09',1000,'tdx_base','snapshot')")
        conn.execute("INSERT INTO capital_event(symbol,event_date,category,c1,c2,c3,c4) VALUES ('300138.SZ','2026-10-09',1,10,0,0,0)")
    result=bars('300138.SZ',date(2026,10,8))
    assert result['bars'][0]['turnover_pct'] is None
    assert result['bars'][0]['close']==10


def test_http_endpoints_and_origin_guard(isolated_db):
    from quant.api import app
    client=TestClient(app)
    assert client.get('/api/health').json()=={'ok':True}
    assert client.get('/').status_code==200
    assert client.get('/api/securities?q=300138').json()[0]['symbol']=='300138.SZ'
    assert client.post('/api/screens',json={'min_amount':-1}).status_code==422
    assert client.post('/api/screens',json={}).status_code==400
    assert client.post('/api/imports',json={},headers={'Origin':'https://external.example'}).status_code==403


def test_mcp_tools_registered(isolated_db):
    import asyncio
    from quant.mcp_server import create_server
    tools=asyncio.run(create_server().list_tools())
    assert {t.name for t in tools}=={'data_status','search_securities','daily_bars','technical_screen','list_stock_pools','industry_graph','analyze_news'}


def test_actual_mcp_stdio_handshake(isolated_db):
    import asyncio,json,sys
    from pathlib import Path
    from mcp import ClientSession,StdioServerParameters
    from mcp.client.stdio import stdio_client
    async def exercise():
        params=StdioServerParameters(command=sys.executable,args=['-m','quant.cli','mcp'],
                                     cwd=str(Path(__file__).resolve().parents[1]),env=dict(os.environ))
        async with stdio_client(params) as (reader,writer):
            async with ClientSession(reader,writer) as session:
                await session.initialize()
                tools=await session.list_tools()
                assert len(tools.tools)==7
                result=await session.call_tool('search_securities',{'query':'300138'})
                assert not result.isError
                assert '300138.SZ' in json.dumps(result.model_dump(mode='json'))
    asyncio.run(exercise())


def test_history_range_metadata_and_previous_close(isolated_db):
    from quant.db import connect
    from quant.services import bars
    with connect() as conn:
        for day,price in [(8,10),(9,11),(10,12)]:
            conn.execute('INSERT INTO daily_bar(symbol,trade_date,open,high,low,close,volume,amount) VALUES (%s,%s,%s,%s,%s,%s,100,1000)',
                         ('300138.SZ',date(2026,10,day),price,price,price,price))
    history=bars('300138.SZ',date(2026,10,10),20000,'raw',date(2026,10,9))
    assert history['available_start']=='2026-10-08' and history['available_rows']==3
    assert history['range_rows']==2 and history['returned_rows']==2 and not history['truncated']
    assert [b['trade_date'] for b in history['bars']]==['2026-10-09','2026-10-10']
    assert history['bars'][0]['pct_change']==10  # Uses the prior day outside the visible range.
    limited=bars('300138.SZ',date(2026,10,10),1,'raw')
    assert limited['truncated'] and limited['returned_rows']==1 and limited['range_rows']==3
    assert limited['bars'][0]['price_change']==1
    with pytest.raises(ValueError,match='起始日'):
        bars('300138.SZ',date(2026,10,9),20000,'raw',date(2026,10,10))
