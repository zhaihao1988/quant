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
    assert {t.name for t in tools}=={'data_status','search_securities','daily_bars','technical_screen','list_stock_pools','industry_graph','analyze_news','multi_strategy_catalog','start_multi_screen','multi_screen_status','financial_data_status','financial_reports','stock_fundamentals','public_information_status','update_public_information','search_local_evidence','analyze_stored_news','stock_related_events'}


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
                assert len(tools.tools)==18
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


def test_block_incremental_and_old_revision(isolated_db,tmp_path):
    from datetime import timedelta
    from quant.ingest import import_day
    from quant.db import connect
    p=tmp_path/'sz300138.day'
    def record(i,close=1100):
        d=date(2020,1,1)+timedelta(days=i)
        return struct.pack('<IIIIIfII',int(d.strftime('%Y%m%d')),1000,1200,900,close,100000,10000,0)
    original=b''.join(record(i) for i in range(600));p.write_bytes(original)
    first=import_day(p,'300138.SZ',details=True)
    assert first['changed']==600 and first['parsed']==600
    p.write_bytes(original+record(600))
    appended=import_day(p,'300138.SZ',details=True)
    assert appended['changed']==1 and 0<appended['parsed']<100
    revised=bytearray(p.read_bytes());revised[10*32:11*32]=record(10,1150);p.write_bytes(revised)
    history=import_day(p,'300138.SZ',details=True)
    assert history['changed']==1 and history['parsed']==601
    with connect() as c:
        assert c.execute('SELECT count(*) AS n FROM daily_bar').fetchone()['n']==601
        assert float(c.execute("SELECT close FROM daily_bar WHERE trade_date='2020-01-11'").fetchone()['close'])==11.5


def test_verify_detects_same_timestamp_modification(isolated_db,tmp_path):
    from quant.ingest import import_day
    p=tmp_path/'sz300138.day'
    def record(close):return struct.pack('<IIIIIfII',20261008,1000,1200,900,close,100000,10000,0)
    p.write_bytes(record(1100));import_day(p,'300138.SZ');old=p.stat()
    p.write_bytes(record(1150));os.utime(p,ns=(old.st_atime_ns,old.st_mtime_ns))
    assert import_day(p,'300138.SZ',mode='verify')==(1,False)


def test_changing_source_rolls_back_file_transaction(isolated_db,tmp_path,monkeypatch):
    from quant import ingest
    from quant.db import connect
    p=tmp_path/'sz300138.day'
    first=struct.pack('<IIIIIfII',20261008,1000,1200,900,1100,100000,10000,0)
    second=struct.pack('<IIIIIfII',20261009,1000,1200,900,1150,100000,10000,0)
    p.write_bytes(first);ingest.import_day(p,'300138.SZ');p.write_bytes(first+second)
    original=ingest.day_records
    def mutate(data):
        yield from original(data)
        p.write_bytes(first+second+b'partial')
    monkeypatch.setattr(ingest,'day_records',mutate)
    with pytest.raises(ValueError,match='回滚'):ingest.import_day(p,'300138.SZ')
    with connect() as c:assert c.execute('SELECT count(*) AS n FROM daily_bar').fetchone()['n']==1


def test_duplicate_clicks_share_one_task_and_orphans_recover(isolated_db,monkeypatch):
    from quant.api import app
    from quant import api
    from quant.db import connect
    from quant.ingest import reserve_run
    calls=[];monkeypatch.setattr(api,'run_import',lambda *a:calls.append(a))
    client=TestClient(app)
    one=client.post('/api/imports',json={}).json();two=client.post('/api/imports',json={}).json()
    assert one['id']==two['id'] and two['reused'] and len(calls)==1
    with connect() as c:c.execute("UPDATE import_run SET status='running',heartbeat_at=now()-interval '10 minutes' WHERE id=%s",(one['id'],))
    recovered,reused=reserve_run()
    assert not reused and recovered!=one['id']
    with connect() as c:assert c.execute('SELECT status FROM import_run WHERE id=%s',(one['id'],)).fetchone()['status']=='interrupted'


def test_missing_database_tail_repairs_even_with_unchanged_source(isolated_db,tmp_path):
    from datetime import timedelta
    from quant.ingest import import_day
    from quant.db import connect
    from quant.config import settings
    from psycopg.rows import dict_row
    p=tmp_path/'sz300138.day'
    records=[]
    for i in range(300):
        d=date(2020,1,1)+timedelta(days=i)
        records.append(struct.pack('<IIIIIfII',int(d.strftime('%Y%m%d')),1000,1200,900,1100,100000,10000,0))
    p.write_bytes(b''.join(records));import_day(p,'300138.SZ')
    with connect() as c:c.execute("DELETE FROM daily_bar WHERE trade_date>='2020-09-01'")
    # Same source and fingerprints, but actual DB latest date is behind. A pooled
    # autocommit connection still uses an independent transaction per file.
    with psycopg.connect(settings().database_url,autocommit=True,row_factory=dict_row) as c:
        result=import_day(p,'300138.SZ',details=True,connection=c)
        assert result['changed']==56 and not result['skipped'] and result['parsed']<100
        assert c.execute('SELECT count(*) AS n FROM daily_bar').fetchone()['n']==300


def test_incremental_checks_recent_tail_even_if_timestamp_unchanged(isolated_db,tmp_path):
    from quant.ingest import import_day
    p=tmp_path/'sz300138.day'
    def record(close):return struct.pack('<IIIIIfII',20261008,1000,1200,900,close,100000,10000,0)
    p.write_bytes(record(1100));import_day(p,'300138.SZ');old=p.stat()
    p.write_bytes(record(1150));os.utime(p,ns=(old.st_atime_ns,old.st_mtime_ns))
    assert import_day(p,'300138.SZ')==(1,False)
