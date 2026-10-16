from datetime import date
import math
import struct
import pytest
import uuid
from test_integration import isolated_db
from quant.db import connect
from quant.tdx.financial import financial_header,financial_records,decode_fields,announcement_date
from quant.financial_fields import standard_facts,company_profile
from quant.financial_ingest import import_financial_file,import_financials


@pytest.fixture(autouse=True)
def separate_financial_lock(monkeypatch):
    monkeypatch.setattr('quant.financial_ingest.IMPORT_LOCK',1000000+uuid.uuid4().int%1000000000)
    monkeypatch.setattr('quant.financial_review.REVIEW_LOCK',1000000+uuid.uuid4().int%1000000000)


def package(records,period=20260630,n=584):
    size=n*4;start=20+11*len(records)
    index=b'';payload=b''
    for i,(code,fields) in enumerate(records):
        values=[0.]*n
        for field,value in fields.items():
            if field<=n:values[field-1]=value
        index+=struct.pack('<6sBI',code.encode(),0,start+i*size)
        payload+=struct.pack('<'+'f'*n,*values)
    return struct.pack('<hIHIII',1,period,len(records),0,size if records else 0,0)+index+payload


def test_raw_zero_missing_unknown_and_units():
    data=package([('300138',{96:12345.,271:123456.,502:12.5,314:260820,580:float('nan')})])
    code,tag,raw=next(financial_records(data))
    values=decode_fields(raw)
    assert code=='300138' and values[17]==0 and values[580] is None
    facts=standard_facts(values)
    assert facts['revenue_total']['value']==125000
    assert facts['net_profit_parent']['value']==12345
    assert facts['equity_parent']['value']==123456
    assert announcement_date(values,date(2026,6,30))==date(2026,8,20)
    assert announcement_date({314:229},date(1999,12,31))==date(2000,2,29)
    assert announcement_date({314:260231},date(2026,6,30)) is None
    assert standard_facts({74:10})['revenue_total']['value'] is None
    assert standard_facts({502:0})['revenue_total']['status']=='available'


def test_corrupt_index_and_variable_field_count():
    data=package([('300138',{})],n=20)
    assert financial_header(data)['field_count']==20
    assert len(decode_fields(next(financial_records(data))[2]))==20
    with pytest.raises(ValueError):financial_header(data[:-1])
    bad=bytearray(data);struct.pack_into('<I',bad,27,0)
    with pytest.raises(ValueError):list(financial_records(bad))
    duplicate=package([('300138',{}),('300138',{})])
    assert len(list(financial_records(duplicate)))==2
    overlap=bytearray(duplicate);struct.pack_into('<I',overlap,38,42)
    with pytest.raises(ValueError):list(financial_records(overlap))


def test_profiles_do_not_classify_by_zero_values():
    assert company_profile(['银行','股份制银行'])=='bank'
    assert company_profile(['证券'])=='securities'
    assert company_profile(['保险'])=='insurance'
    assert company_profile(['食品','其他农产品加工'])=='general'
    assert company_profile([])=='unknown'
    assert company_profile(['银行','保险'])=='unknown'


def test_financial_revision_idempotence_and_unknown_stocks(isolated_db,tmp_path):
    path=tmp_path/'gpcw20260630.dat'
    path.write_bytes(package([('300138',{96:10,502:20,314:260820}),('999999',{96:30})]))
    assert import_financial_file(path)['reports']==2
    assert import_financial_file(path)['skipped']
    path.write_bytes(package([('300138',{96:11,502:20,314:260820}),('999999',{96:30})]))
    assert not import_financial_file(path)['skipped']
    with connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM financial_report').fetchone()['n']==4
        assert conn.execute('SELECT count(*) AS n FROM financial_current_report').fetchone()['n']==2
        assert conn.execute('SELECT count(*) AS n FROM financial_current_report WHERE symbol IS NULL').fetchone()['n']==1
        current=conn.execute("SELECT standard FROM financial_current_report WHERE symbol='300138.SZ'").fetchone()
        assert current['standard']['net_profit_parent']['value']==11
    path.write_bytes(package([]))
    with pytest.raises(ValueError,match='减少'):import_financial_file(path)


def test_concurrent_source_change_rolls_back(isolated_db,tmp_path,monkeypatch):
    from quant import financial_ingest
    path=tmp_path/'gpcw20260630.dat';path.write_bytes(package([('300138',{96:10})]))
    original=financial_ingest.financial_records
    def changing(*args):
        yield from original(*args)
        path.write_bytes(path.read_bytes()+b'x')
    monkeypatch.setattr(financial_ingest,'financial_records',changing)
    with pytest.raises(ValueError,match='发生变化'):import_financial_file(path)
    with connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM financial_file_revision').fetchone()['n']==0
        assert conn.execute('SELECT count(*) AS n FROM financial_report').fetchone()['n']==0


def test_financial_status_and_report_api(isolated_db,tmp_path):
    from fastapi.testclient import TestClient
    from quant.api import app
    (tmp_path/'gpcw20260630.dat').write_bytes(package([('300138',{96:10,502:20,314:260820})]))
    (tmp_path/'gpcw20260930.dat').write_bytes(package([],20260930))
    run=import_financials(tmp_path)
    assert run['status']=='completed' and run['reports']==1
    assert import_financials(tmp_path)['skipped']==2
    client=TestClient(app)
    status=client.get('/api/financials/status').json()
    assert status['covered_securities']==1 and status['latest_report_date']=='2026-06-30'
    result=client.get('/api/financials/300138.SZ?fields=FN96,FN502,FN999').json()
    assert result['reports'][0]['fields'][0]['raw_value']==10
    assert result['reports'][0]['fields'][1]['raw_unit']=='万元'
    assert result['reports'][0]['fields'][2]['state']=='field_absent'
    assert client.get('/api/financials/300138.SZ?fields=nope').status_code==400
