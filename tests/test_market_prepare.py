import hashlib
import struct
import uuid
from datetime import date
import pytest
from fastapi.testclient import TestClient
from test_integration import isolated_db
from quant.db import connect
from quant import market_prepare as jobs,ingest,multi_jobs,sync_status


def record(day,close=1100):
    return struct.pack('<IIIIIfII',day,1000,1200,900,close,100000,10000,0)


@pytest.fixture
def source(isolated_db,tmp_path,monkeypatch):
    key=1000000+uuid.uuid4().int%1000000000
    monkeypatch.setattr(jobs,'RESERVE_LOCK',key)
    monkeypatch.setattr(jobs,'WORKER_LOCK',key+1)
    monkeypatch.setattr(sync_status,'SCAN_LOCK',key+2)
    monkeypatch.setattr(multi_jobs,'RESERVE_LOCK',key+3)
    monkeypatch.setattr(multi_jobs,'WORKER_LOCK',key+4)
    monkeypatch.setenv('QUANT_TDX_ROOT',str(tmp_path))
    day=tmp_path/'vipdoc/sz/lday/sz300138.day';day.parent.mkdir(parents=True)
    day.write_bytes(record(20201008))
    ingest.import_day(day,'300138.SZ')
    gbbq=tmp_path/'T0002/hq_cache/gbbq';gbbq.parent.mkdir(parents=True);gbbq.write_bytes(b'fixture')
    with connect() as c:
        c.execute('INSERT INTO import_file(path,sha256,byte_size,record_count) VALUES (%s,%s,7,0)',(str(gbbq),hashlib.sha256(b'fixture').hexdigest()))
    monkeypatch.setattr(ingest,'metadata',lambda root:{'300138.SZ':None})
    monkeypatch.setattr(ingest,'capital',lambda *args:0)
    monkeypatch.setattr(ingest,'taxonomy',lambda *args:0)
    return day


def test_prepare_imports_before_resolving_screen_date_and_deduplicates(source,monkeypatch):
    source.write_bytes(record(20201008)+record(20201009))
    executed=[];monkeypatch.setattr(multi_jobs,'run_job',lambda job:executed.append(job))
    parameters={'symbols':['300138.SZ'],'workers':1}
    job,reused=jobs.reserve(parameters);assert not reused
    assert jobs.reserve(parameters)==(job,True)
    with pytest.raises(ValueError,match='已有行情核对'):jobs.reserve()
    jobs.run(job);result=jobs.status(job)
    assert result['status']=='completed' and result['result']['before']['db_date']=='2020-10-08'
    assert result['result']['after']['db_date']=='2020-10-09'
    assert result['result']['effective_date']=='2020-10-09' and executed==[result['screen_id']]
    with connect() as c:
        screen=c.execute('SELECT effective_date FROM multi_screen_run WHERE id=%s',(result['screen_id'],)).fetchone()
        assert screen['effective_date']==date(2020,10,9)
        assert c.execute('SELECT count(*) AS n FROM daily_bar').fetchone()['n']==2
    jobs.run(job);assert len(executed)==1


def test_same_date_revision_sync_only_and_aligned_repeat(source):
    source.write_bytes(record(20201008,1150))
    job,_=jobs.reserve();jobs.run(job);result=jobs.status(job)
    assert result['status']=='completed' and result['screen_id'] is None
    with connect() as c:assert float(c.execute('SELECT close FROM daily_bar').fetchone()['close'])==11.5
    again,_=jobs.reserve();jobs.run(again)
    assert jobs.status(again)['status']=='completed' and jobs.status(again)['import_id'] is None


def test_incomplete_file_blocks_screen_and_retains_database(source):
    source.write_bytes(record(20201008)+b'incomplete')
    job,_=jobs.reserve({'symbols':['300138.SZ']});jobs.run(job)
    result=jobs.status(job)
    assert result['status']=='failed' and result['screen_id'] is None and '不完整' in result['error']
    with connect() as c:
        assert c.execute('SELECT count(*) AS n FROM multi_screen_run').fetchone()['n']==0
        assert c.execute('SELECT count(*) AS n FROM daily_bar').fetchone()['n']==1


def test_import_failure_blocks_screen(source,monkeypatch):
    source.write_bytes(record(20201008)+record(20201009))
    def failed(job,*args):
        with connect() as c:c.execute("UPDATE import_run SET status='failed',errors='[\"fixture error\"]' WHERE id=%s",(job,))
    monkeypatch.setattr(ingest,'run_import',failed)
    job,_=jobs.reserve({'symbols':['300138.SZ']});jobs.run(job)
    result=jobs.status(job)
    assert result['status']=='failed' and result['screen_id'] is None and 'fixture error' in result['error']


def test_post_import_source_change_blocks_screen(source,monkeypatch):
    source.write_bytes(record(20201008)+record(20201009))
    original=ingest.run_import
    def changing(*args):
        original(*args);source.write_bytes(source.read_bytes()+record(20201012))
    monkeypatch.setattr(ingest,'run_import',changing)
    job,_=jobs.reserve({'symbols':['300138.SZ']});jobs.run(job)
    assert jobs.status(job)['status']=='failed' and jobs.status(job)['screen_id'] is None


def test_prepare_http_and_invalid_historical_scope(source):
    from quant.api import app
    client=TestClient(app)
    assert client.post('/api/market-prepare',json={},headers={'Origin':'https://other.example'}).status_code==403
    assert client.post('/api/market-prepare',json={'screen':{'as_of':'2020-10-08'}}).status_code==400
    response=client.post('/api/market-prepare',json={});assert response.status_code==202
    assert client.get('/api/market-prepare/'+response.json()['id']).json()['status']=='completed'
    assert client.get('/api/market-prepare').json()['screen_id'] is None


def test_missing_source_is_reported_without_claiming_full_coverage(source):
    with connect() as c:c.execute("INSERT INTO security(symbol,code,market,name) VALUES ('600036.SH','600036','SH','缺文件样本')")
    job,_=jobs.reserve();jobs.run(job)
    result=jobs.status(job)
    assert result['status']=='completed' and result['result']['after']['missing_files']==1
    assert result['result']['synced_files']==0
