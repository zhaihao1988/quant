from datetime import date,datetime
from zoneinfo import ZoneInfo
import json
import pytest
from fastapi.testclient import TestClient
from test_integration import isolated_db
from test_multi_jobs import seed,seed_two,separate_job_locks
from quant.db import connect
from quant.multiscreener import MultiScreenRequest
from quant import multi_jobs as jobs,screen_records as records


def signal_item(args):
    ordinal,stock,effective,_=args
    return ordinal,stock['symbol'],{'signals':[{'symbol':stock['symbol'],'name':stock['name'],'signal_date':effective.isoformat(),'strategy_label':'测试信号'}],'errors':[],'coverage':[]}


def test_archive_is_fixed_latest_finance_is_live_and_input_reuse(isolated_db,monkeypatch):
    seed();pe=[10]
    monkeypatch.setattr(jobs,'scan_stock',signal_item)
    monkeypatch.setattr('quant.fundamentals.attach',lambda signals,day:[{**s,'pe':pe[0]} for s in signals])
    request=MultiScreenRequest(as_of=date(2026,10,9),workers=1)
    job,_=jobs.reserve(request);jobs.run_job(job)
    frozen=records.archive(job)
    assert frozen['payload']['result']['signals'][0]['pe']==10 and frozen['export_path']
    assert records.digest(frozen['payload'])==frozen['sha256']
    pe[0]=20
    assert jobs.snapshot(job)['result']['signals'][0]['pe']==20
    assert jobs.snapshot(job,'archive')['result']['signals'][0]['pe']==10
    assert jobs.reserve(request,reuse_completed=True)==(job,True)
    assert jobs.reserve(request.model_copy(update={'workers':4}),reuse_completed=True)==(job,True)
    second,reused=jobs.reserve(request,reuse_completed=True,force=True)
    assert not reused and jobs.snapshot(second)['version']==2
    jobs.run_job(second)
    assert records.archive(job)['sha256']==frozen['sha256']


def test_zero_signals_archived_and_history_export(isolated_db):
    from quant.api import app
    seed();request=MultiScreenRequest(as_of=date(2026,10,9),workers=1)
    job,_=jobs.reserve(request);jobs.run_job(job)
    assert records.archive(job)['payload']['result']['signals']==[]
    client=TestClient(app)
    history=client.get('/api/multi-screens/history?limit=1&day=2026-10-09').json()
    assert history['total']==1 and history['items'][0]['archive_state']=='saved'
    assert client.get('/api/multi-screens/'+job+'/export?format=csv').status_code==200
    assert client.get('/api/multi-screens/'+job+'/export?format=json').json()['view']=='archive'
    assert client.get('/api/multi-screens/'+job+'?view=archive').json()['result']['signals']==[]


def test_resume_refuses_changed_inputs_without_rewriting_saved_results(isolated_db,monkeypatch):
    seed_two();request=MultiScreenRequest(as_of=date(2026,10,9),workers=1)
    job,_=jobs.reserve(request)
    original=jobs.stock_results
    def stop(*args):
        for result in original(*args):yield result;jobs.cancel(job)
    with monkeypatch.context() as m:m.setattr(jobs,'stock_results',stop);jobs.run_job(job)
    frozen=records.archive(job)
    with connect() as c:c.execute("UPDATE daily_bar SET close=10.5,imported_at=now() WHERE symbol='600036.SH'")
    with pytest.raises(ValueError,match='版本已变化'):jobs.resume(job)
    assert records.archive(job)['sha256']==frozen['sha256']


def test_retry_failed_stock_keeps_parent_and_successes(isolated_db,monkeypatch):
    seed_two();scanned=[];failed=[True]
    def scan(args):
        scanned.append(args[1]['symbol'])
        if args[1]['symbol']=='600036.SH' and failed[0]:
            return args[0],args[1]['symbol'],{'signals':[],'coverage':[],'errors':[{'symbol':'600036.SH','error':'temporary'}]}
        return signal_item(args)
    monkeypatch.setattr(jobs,'scan_stock',scan)
    job,_=jobs.reserve(MultiScreenRequest(as_of=date(2026,10,9),workers=1));jobs.run_job(job)
    assert jobs.snapshot(job)['status']=='partial'
    old=records.archive(job);failed[0]=False;scanned.clear()
    child=jobs.retry_failed(job,1);jobs.run_job(child)
    assert scanned==['600036.SH'] and jobs.snapshot(child)['status']=='completed'
    assert jobs.snapshot(child)['parent_run_id']==job
    assert records.archive(job)['sha256']==old['sha256']


def test_archive_failure_is_separate_and_can_be_retried(isolated_db,monkeypatch):
    seed();job,_=jobs.reserve(MultiScreenRequest(as_of=date(2026,10,9),workers=1))
    def broken(*args):raise OSError('disk unavailable')
    with monkeypatch.context() as m:m.setattr(records,'save_archive',broken);jobs.run_job(job)
    result=jobs.snapshot(job)
    assert result['status']=='completed' and result['archive_state']=='failed'
    records.save_archive(job)
    assert jobs.snapshot(job)['archive_state']=='saved'


def test_orphan_status_recovers_without_new_submission(isolated_db):
    seed();job,_=jobs.reserve(MultiScreenRequest(as_of=date(2026,10,9)))
    with connect() as c:c.execute("UPDATE multi_screen_run SET heartbeat_at=now()-interval '5 minutes' WHERE id=%s",(job,))
    assert jobs.snapshot(job)['status']=='interrupted'


def test_verified_calendar_weekend_holiday_and_unknown_year(isolated_db):
    from quant.trade_calendar import expected_trade_date
    zone=ZoneInfo('Asia/Shanghai')
    assert expected_trade_date(datetime(2026,10,17,18,tzinfo=zone))==date(2026,10,16)
    assert expected_trade_date(datetime(2026,10,7,18,tzinfo=zone))==date(2026,9,30)
    assert expected_trade_date(datetime(2026,10,16,10,tzinfo=zone))==date(2026,10,15)
    assert expected_trade_date(datetime(2027,2,1,18,tzinfo=zone)) is None


def test_shared_reader_blocks_all_quote_writers_and_reverse(isolated_db):
    from quant.data_guard import market_access
    with market_access():
        with market_access():pass
        with pytest.raises(ValueError,match='不能同时'):
            with market_access(write=True):pass
    with market_access(write=True):
        with pytest.raises(ValueError,match='不能同时'):
            with market_access():pass


def test_csv_formula_escape_and_zero_false():
    payload={'id':'test','effective_date':'2026-10-16','result':{'signals':[{'symbol':'=1+1','pe':0,'pe_lt_30':False}]}}
    text=records.csv_export(payload)
    assert "'=1+1" in text and ',0,' in text and 'False' in text


def test_local_export_failure_does_not_lose_database_archive(isolated_db,tmp_path,monkeypatch):
    seed();blocked=tmp_path/'blocked';blocked.write_text('file')
    monkeypatch.setenv('QUANT_ARCHIVE_DIR',str(blocked))
    job,_=jobs.reserve(MultiScreenRequest(as_of=date(2026,10,9),workers=1));jobs.run_job(job)
    saved=records.archive(job)
    assert jobs.snapshot(job)['archive_state']=='saved' and saved['export_error']
    monkeypatch.setenv('QUANT_ARCHIVE_DIR',str(tmp_path/'working'))
    records.export_archive(job)
    assert records.archive(job)['export_error'] is None


def test_quote_revision_creates_new_version_but_keeps_old_archive(isolated_db):
    seed();request=MultiScreenRequest(as_of=date(2026,10,9),workers=1)
    old,_=jobs.reserve(request);jobs.run_job(old);saved=records.archive(old)
    with connect() as c:c.execute("UPDATE daily_bar SET close=10.5,imported_at=now() WHERE trade_date='2026-10-09'")
    new,reused=jobs.reserve(request,reuse_completed=True)
    assert not reused and jobs.snapshot(new)['version']==2 and new!=old
    assert records.archive(old)['sha256']==saved['sha256']


def test_legacy_result_has_no_fabricated_archive(isolated_db):
    import uuid
    from psycopg.types.json import Jsonb
    old=str(uuid.uuid4())
    with connect() as c:c.execute("INSERT INTO multi_screen_run(id,status,effective_date,parameters,finished_at) VALUES (%s,'completed','2026-10-09',%s,now())",(old,Jsonb({})))
    with pytest.raises(ValueError,match='没有当次归档'):records.archive(old)
    with pytest.raises(ValueError,match='不能补造'):records.save_archive(old)


def test_history_pagination_is_not_limited_to_recent_ten(isolated_db):
    import uuid
    from psycopg.types.json import Jsonb
    with connect() as c:
        for _ in range(12):c.execute("INSERT INTO multi_screen_run(id,status,effective_date,parameters) VALUES (%s,'completed','2026-10-09',%s)",(str(uuid.uuid4()),Jsonb({})))
    page=jobs.history(limit=5,offset=10,day=date(2026,10,9))
    assert page['total']==12 and len(page['items'])==2
