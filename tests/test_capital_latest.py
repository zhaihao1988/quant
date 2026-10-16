import struct
from datetime import date
import uuid
import pytest
from test_integration import isolated_db
from test_financial import package
from quant.db import connect
from quant.capital_ingest import import_capital,shares_value
from quant.financial_ingest import import_financial_file
from quant.fundamentals import load_inputs,attach


def dbf(rows):
    fields=[('SC',1),('GPDM',6),('ZGB',18),('LTAG',18),('HG',18),('BG',18),('GXRQ',8)]
    header_size=33+32*len(fields);row_size=1+sum(w for _,w in fields)
    head=bytearray(32);head[0]=3
    struct.pack_into('<IHH',head,4,len(rows),header_size,row_size)
    descriptors=b''
    for name,width in fields:
        field=bytearray(32);field[:len(name)]=name.encode();field[11]=ord('C');field[16]=width;descriptors+=field
    return bytes(head)+descriptors+b'\r'+b''.join(b' '+b''.join(str(row.get(name,'')).encode().ljust(width,b' ') for name,width in fields) for row in rows)+b'\x1a'


def row(total='1',floating='.5',**more):
    return {'SC':'0','GPDM':'300138','ZGB':total,'LTAG':floating,'HG':'0','BG':'0','GXRQ':'20260825',**more}


@pytest.fixture(autouse=True)
def separate_locks(monkeypatch):
    monkeypatch.setattr('quant.capital_ingest.CAPITAL_LOCK',1000000+uuid.uuid4().int%1000000000)
    monkeypatch.setattr('quant.fundamental_refresh.RESERVE_LOCK',1000000+uuid.uuid4().int%1000000000)
    monkeypatch.setattr('quant.fundamental_refresh.WORKER_LOCK',1000000+uuid.uuid4().int%1000000000)
    monkeypatch.setattr('quant.financial_ingest.IMPORT_LOCK',1000000+uuid.uuid4().int%1000000000)


def test_shares_units_and_invalid_values():
    assert shares_value('48310.01')==483100100 and shares_value('0')==0
    assert shares_value('') is None
    for invalid in ['NaN','inf','-1','0.00001','abc']:
        with pytest.raises(ValueError):shares_value(invalid)


def test_capital_immutable_versions_and_duplicate_guard(isolated_db,tmp_path):
    p=tmp_path/'base.dbf';original=dbf([row()]);p.write_bytes(original)
    assert not import_capital(p)['skipped']
    assert import_capital(p)['skipped']
    p.write_bytes(dbf([row(total='2')]))
    import_capital(p)
    with connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM capital_file_revision').fetchone()['n']==2
        assert bytes(conn.execute('SELECT payload FROM capital_file_revision ORDER BY id LIMIT 1').fetchone()['payload'])==original
        assert conn.execute('SELECT total_shares FROM capital_snapshot ORDER BY file_id DESC LIMIT 1').fetchone()['total_shares']==20000
    p.write_bytes(dbf([row(),row(total='3')]))
    with pytest.raises(ValueError,match='冲突股本'):import_capital(p)
    p.write_bytes(dbf([]))
    with pytest.raises(ValueError,match='完整A股'):import_capital(p)
    with connect() as conn:assert conn.execute('SELECT count(*) AS n FROM capital_file_revision').fetchone()['n']==2


def test_latest_reference_and_strict_history_are_separate(isolated_db,tmp_path,monkeypatch):
    p=tmp_path/'base.dbf';p.write_bytes(dbf([row()]));import_capital(p)
    f=tmp_path/'gpcw20251231.dat';f.write_bytes(package([('300138',{96:100,271:500,502:2,314:260420})],20251231));import_financial_file(f)
    with connect() as conn:
        conn.execute("UPDATE capital_file_revision SET observed_at='2026-10-16 10:00:00+08'")
        conn.execute("UPDATE financial_file_revision SET observed_at='2026-10-16 10:00:00+08'")
        conn.execute("UPDATE financial_report SET reviewed_at='2026-10-16 10:00:00+08'")
        conn.execute("INSERT INTO daily_bar(symbol,trade_date,open,high,low,close,volume,amount) VALUES ('300138.SZ','2026-10-14',10,10,10,10,100,1000)")
    assert load_inputs('300138.SZ',date(2026,10,14))['pe'] is None
    current=load_inputs('300138.SZ',date(2026,10,16))
    assert current['total_shares']==10000 and current['float_shares']==5000
    assert current['market_cap']==100000 and current['pe']==1000
    assert current['float_market_cap']==50000 and current['turnover_pct']==2
    assert current['revenue_total']==20000 and current['financial_report_date']=='2025-12-31'
    assert current['shares_effective_date'] is None
    monkeypatch.setattr('quant.fundamentals.latest_reference_date',lambda:date(2026,10,16))
    signal={'symbol':'300138.SZ','signal_date':'2026-10-14','pe':None,'details':{'unchanged':True}}
    attached=attach([signal],date(2026,10,14))[0]
    assert attached['pe']==1000 and attached['signal_date']=='2026-10-14'
    assert attached['technical_as_of']=='2026-10-14' and attached['fundamental_reference']=='latest_collected'
    assert signal['pe'] is None and signal['details']==attached['details']
    p.write_bytes(dbf([row(total='2')]));import_capital(p)
    with connect() as conn:conn.execute("UPDATE capital_file_revision SET observed_at='2026-10-17 10:00:00+08' WHERE id=(SELECT max(id) FROM capital_file_revision)")
    assert load_inputs('300138.SZ',date(2026,10,16))['total_shares']==10000
    assert load_inputs('300138.SZ',date(2026,10,17))['total_shares']==20000


def test_refresh_job_dedup_cache_and_http(isolated_db,tmp_path,monkeypatch):
    from quant import fundamental_refresh as jobs
    from fastapi.testclient import TestClient
    from quant.api import app
    root=tmp_path/'T0002'/'hq_cache';root.mkdir(parents=True);(root/'base.dbf').write_bytes(dbf([row()]))
    cw=tmp_path/'vipdoc'/'cw';cw.mkdir(parents=True)
    (cw/'gpcw20251231.dat').write_bytes(package([('300138',{96:100,271:500,502:2,314:260420})],20251231))
    monkeypatch.setenv('QUANT_TDX_ROOT',str(tmp_path))
    job,reused=jobs.reserve();assert not reused and jobs.reserve()==(job,True)
    jobs.run_refresh(job);result=jobs.status()
    assert result['status']=='completed' and result['processed']==result['total']==1
    assert result['result']['coverage']['total_shares']==1
    with connect() as conn:assert conn.execute('SELECT count(*) AS n FROM financial_metric_cache').fetchone()['n']==1
    again,_=jobs.reserve();jobs.run_refresh(again)
    assert jobs.status()['result']['capital']['skipped']
    assert jobs.status()['result']['financials']['skipped']==1
    client=TestClient(app)
    assert client.get('/api/fundamental-refresh').json()['status']=='completed'
    assert client.post('/api/fundamental-refresh',json={},headers={'Origin':'https://other.example'}).status_code==403
