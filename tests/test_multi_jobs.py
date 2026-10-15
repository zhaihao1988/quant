from datetime import date
from fastapi.testclient import TestClient
from test_integration import isolated_db
from quant.db import connect
from quant.multiscreener import MultiScreenRequest
from quant.multi_jobs import reserve,run_job,snapshot,cancel


def seed():
    with connect() as conn:
        for d in ('2026-10-08','2026-10-09'):
            conn.execute("INSERT INTO daily_bar(symbol,trade_date,open,high,low,close,volume,amount) VALUES ('300138.SZ',%s,10,11,9,10,10000,100000)",(d,))


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
