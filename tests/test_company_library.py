from uuid import UUID,uuid4
import pytest
from test_integration import isolated_db
from test_company_documents import operate
from quant.db import connect
from quant import company_library as lib,company_library_worker as worker,company_documents as docs


@pytest.fixture(autouse=True)
def separate_collection_locks(monkeypatch):
    token=1000000000+uuid4().int%1000000000
    monkeypatch.setattr(worker,'WORKER_LOCK',token)
    monkeypatch.setattr(lib,'RESERVE_LOCK',token+1)


def seed():
    with connect() as c:
        c.execute("INSERT INTO security(symbol,code,market,name) VALUES ('600036.SH','600036','SH','招商银行'),('000002.SZ','000002','SZ','万科')")


def fake_fetch(symbol,page):
    return f'https://basic.10jqka.com.cn/{symbol[:6]}/operate.html',operate().replace(b'300138',symbol[:6].encode())


def test_all_stocks_cached_reports_search_and_reuse(isolated_db,monkeypatch):
    seed();launches=[]
    monkeypatch.setattr(lib,'launch_worker',lambda job:launches.append(job))
    docs.save_page('300138.SZ','operate','https://basic.10jqka.com.cn/300138/operate.html',operate())
    listing=lib.library()
    assert listing['summary']['total']==3 and listing['summary']['stored']==1
    assert lib.library(query='招商')['stocks'][0]['symbol']=='600036.SH'
    assert lib.library(state='stored')['total']==1
    job,reused=lib.start();assert not reused and len(launches)==1
    assert lib.start()==(job,True)
    assert lib.job_status(job)['counts']=={'cached':1,'pending':2}
    monkeypatch.setattr(worker,'INTERVAL',0);monkeypatch.setattr(docs,'fetch_page',fake_fetch)
    worker.run(job)
    assert lib.job_status(job)['status']=='completed'
    assert lib.library()['summary']['stored']==3
    assert lib.job_status(job)['counts']['saved']==2


def test_pause_resume_no_content_and_retry_errors(isolated_db,monkeypatch):
    seed();monkeypatch.setattr(lib,'launch_worker',lambda job:None);monkeypatch.setattr(worker,'INTERVAL',0)
    def fetch(symbol,page):
        if symbol=='600036.SH':raise ValueError('网络暂不可用')
        if symbol=='000002.SZ':return fake_fetch(symbol,page)[0],b'<html><title>Test(000002)</title><div id="intro"><ul class="main_intro_list"><li><span>Business</span><p>Only business introduction</p></li></ul></div></html>'
        return fake_fetch(symbol,page)
    monkeypatch.setattr(docs,'fetch_page',fetch)
    job,_=lib.start();lib.pause(job);worker.run(job)
    assert lib.job_status(job)['status']=='paused'
    lib.resume(job);worker.run(job)
    status=lib.job_status(job)
    assert status['status']=='partial' and status['counts']=={'failed':1,'no_content':1,'saved':1}
    retry,_=lib.start('retry');assert lib.job_status(retry)['total']==1
    monkeypatch.setattr(docs,'fetch_page',fake_fetch);worker.run(retry)
    assert lib.job_status(retry)['status']=='completed'
    assert lib.library(state='failed')['total']==0
    assert lib.library(state='no_content')['total']==1


def test_stale_worker_recovery_and_http_endpoints(isolated_db,monkeypatch):
    monkeypatch.setattr(lib,'launch_worker',lambda job:None)
    job,_=lib.start()
    with connect() as c:
        c.execute("UPDATE company_collection_job SET heartbeat_at=now()-interval '5 minutes',created_at=now()-interval '5 minutes' WHERE id=%s",(job,))
    assert lib.job_status(job)['status']=='paused'
    from quant.api import app
    from fastapi.testclient import TestClient
    with TestClient(app) as client:
        assert client.get('/api/stock-library').json()['summary']['total']==1
        assert client.get('/api/stock-library?state=invalid').status_code==400
        assert client.post('/api/stock-library/jobs/'+job+'/resume',json={}).status_code==202
        assert client.post('/api/stock-library/jobs',json={},headers={'origin':'https://example.com'}).status_code==403


def test_rechecked_unchanged_reports_are_fresh(isolated_db,monkeypatch):
    monkeypatch.setattr(lib,'launch_worker',lambda job:None)
    docs.save_page('300138.SZ','operate','https://basic.10jqka.com.cn/300138/operate.html',operate())
    with connect() as c:c.execute("UPDATE company_page_revision SET collected_at=now()-interval '8 days'")
    assert lib.library()['summary']['due']==1
    job,_=lib.start();monkeypatch.setattr(worker,'INTERVAL',0);monkeypatch.setattr(docs,'fetch_page',fake_fetch);worker.run(job)
    assert lib.library()['summary']['due']==0
    assert lib.library()['stocks'][0]['collected_at']!=lib.library()['stocks'][0]['checked_at']
    cached,_=lib.start();worker.run(cached)
    assert lib.job_status(cached)['counts']=={'cached':1}
    with connect() as c:c.execute("UPDATE company_collection_item SET finished_at=now()-interval '8 days' WHERE status='saved'")
    # Reusing a cache today cannot extend the age of the last source check.
    assert lib.library()['summary']['due']==1


def test_repeated_access_rejections_pause_the_collector(isolated_db,monkeypatch):
    monkeypatch.setattr(lib,'launch_worker',lambda job:None);monkeypatch.setattr(worker,'INTERVAL',0)
    with connect() as c:
        for i in range(1,9):
            code=f'{i:06d}'
            c.execute("INSERT INTO security(symbol,code,market,name) VALUES (%s,%s,'SZ','访问测试')",(code+'.SZ',code))
    def reject(*args):raise ValueError('同花顺返回HTTP 403，请稍后重试')
    monkeypatch.setattr(docs,'fetch_page',reject)
    job,_=lib.start();worker.run(job)
    status=lib.job_status(job)
    assert status['status']=='paused' and '拒绝访问' in status['message']
    assert status['counts']['failed']>=5
