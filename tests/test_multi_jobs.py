from datetime import date
import uuid
import pytest
from fastapi.testclient import TestClient
from test_integration import isolated_db
from quant.db import connect
from quant.multiscreener import MultiScreenRequest
from quant.multi_jobs import reserve,run_job,snapshot,cancel


@pytest.fixture(autouse=True)
def separate_job_locks(monkeypatch):
    # PG advisory locks are database-wide, including across temporary schemas.
    # Never interfere with a real scan running alongside the test suite.
    from quant import multi_jobs
    key=1000000+(uuid.uuid4().int%1000000000)
    monkeypatch.setattr(multi_jobs,'RESERVE_LOCK',key)
    monkeypatch.setattr(multi_jobs,'WORKER_LOCK',key+1)


def seed():
    with connect() as conn:
        for d in ('2026-10-08','2026-10-09'):
            conn.execute("INSERT INTO daily_bar(symbol,trade_date,open,high,low,close,volume,amount) VALUES ('300138.SZ',%s,10,11,9,10,10000,100000)",(d,))


def seed_two():
    seed()
    with connect() as conn:
        conn.execute("INSERT INTO security(symbol,code,market,name) VALUES ('600036.SH','600036','SH','测试招商')")
        conn.execute("INSERT INTO daily_bar(symbol,trade_date,open,high,low,close,volume,amount) SELECT '600036.SH',trade_date,open,high,low,close,volume,amount FROM daily_bar WHERE symbol='300138.SZ'")


def test_real_spawn_pool_matches_serial_and_preserves_order(isolated_db):
    seed_two();outputs=[]
    for workers in (1,2):
        run_id,_=reserve(MultiScreenRequest(as_of=date(2026,10,9),workers=workers))
        run_job(run_id);result=snapshot(run_id)
        assert result['status']=='completed' and result['processed']==2
        assert result['result']['execution']['workers']==workers
        outputs.append({key:result['result'][key] for key in ('signals','errors','coverage')})
    assert outputs[0]==outputs[1]
    assert [c['symbol'] for c in outputs[1]['coverage']]==['300138.SZ','600036.SH']


def test_stop_then_resume_preserves_completed_items(isolated_db,monkeypatch):
    from quant import multi_jobs
    seed_two()
    run_id,_=reserve(MultiScreenRequest(as_of=date(2026,10,9),workers=1))
    original=multi_jobs.stock_results
    def stop_after_first(*args):
        for item in original(*args):
            yield item
            cancel(run_id)
    with monkeypatch.context() as patch:
        patch.setattr(multi_jobs,'stock_results',stop_after_first)
        run_job(run_id)
    assert snapshot(run_id)['status']=='cancelled'
    with connect() as conn:
        saved=conn.execute('SELECT symbol,result FROM multi_screen_item WHERE run_id=%s',(run_id,)).fetchall()
    assert len(saved)==1
    assert multi_jobs.resume(run_id,2)==run_id
    run_job(run_id);result=snapshot(run_id)
    assert result['status']=='completed' and result['processed']==2
    assert result['result']['execution']['resumed_after']==1
    with connect() as conn:
        retained=conn.execute('SELECT result FROM multi_screen_item WHERE run_id=%s AND symbol=%s',(run_id,saved[0]['symbol'])).fetchone()
        assert retained['result']==saved[0]['result']
        assert conn.execute('SELECT count(*) AS n FROM multi_screen_item WHERE run_id=%s',(run_id,)).fetchone()['n']==2
    with pytest.raises(ValueError):multi_jobs.resume(run_id,2)


def test_job_deduplication_and_persisted_result(isolated_db):
    seed()
    request=MultiScreenRequest(as_of=date(2026,10,9),symbols=['300138.SZ'])
    run_id,reused=reserve(request)
    assert not reused
    assert reserve(request)==(run_id,True)
    run_job(run_id)
    result=snapshot(run_id)
    assert result['status']=='completed' and result['processed']==result['total']==1
    assert result['result']['fundamental_status']=='not_connected'
    assert result['result']['coverage'][0]['daily']==2
    assert len(result['result']['coverage'][0]['evaluations'])==7
    assert not result['result']['errors']


def test_job_cancel_and_data_errors(isolated_db):
    seed()
    request=MultiScreenRequest(as_of=date(2026,10,9))
    run_id,_=reserve(request)
    assert cancel(run_id)['requested']
    run_job(run_id)
    assert snapshot(run_id)['status']=='cancelled'
    run_id,_=reserve(MultiScreenRequest(as_of=date(2026,10,9),symbols=['000000.SZ']))
    run_job(run_id)
    result=snapshot(run_id)
    assert result['status']=='partial' and result['result']['errors'][0]['symbol']=='000000.SZ'


def test_multi_http_and_old_screen_remain_independent(isolated_db):
    from quant.api import app
    seed();client=TestClient(app)
    catalog=client.get('/api/multi-screens/strategies').json()
    assert len(catalog['strategies'])==7
    assert client.post('/api/multi-screens',json={'strategies':[]}).status_code==422
    response=client.post('/api/multi-screens',json={'as_of':'2026-10-09','symbols':['300138.SZ']})
    assert response.status_code==202
    result=client.get('/api/multi-screens/'+response.json()['id']).json()
    assert result['status']=='completed'
    assert client.post('/api/screens',json={'as_of':'2026-10-09'}).status_code==200
    with connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM screen_run').fetchone()['n']==1
        assert conn.execute('SELECT count(*) AS n FROM multi_screen_run').fetchone()['n']==1


def test_resume_endpoint_and_worker_limits(isolated_db):
    from quant.api import app
    seed();client=TestClient(app)
    assert client.post('/api/multi-screens',json={'workers':5}).status_code==422
    run_id,_=reserve(MultiScreenRequest(as_of=date(2026,10,9),workers=1))
    cancel(run_id);run_job(run_id)
    response=client.post('/api/multi-screens/'+run_id+'/resume',json={'workers':2})
    assert response.status_code==202
    result=snapshot(run_id)
    assert result['status']=='completed' and result['processed']==1
    assert result['parameters']['workers']==2
    assert client.post('/api/multi-screens/'+run_id+'/resume',json={'workers':2}).status_code==400
