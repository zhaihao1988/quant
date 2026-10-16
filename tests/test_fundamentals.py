from datetime import date
import pytest
from test_integration import isolated_db
from test_financial import package
from quant.db import connect
from quant.financial_ingest import import_financial_file
from quant.fundamentals import calculate_metrics,load_inputs,metrics_many


def report(period,profit,revenue,equity=500,state='available'):
    return {'period':date.fromisoformat(period),'state':state,'values':{'net_profit_parent':profit,'revenue_total':revenue,'equity_parent':equity},'ids':[1]}


def test_annual_formulas_and_zero_missing():
    reports=[report('2026-06-30',60,150),report('2025-12-31',100,200),report('2025-06-30',40,100),report('2024-12-31',80,180),report('2023-12-31',70,160)]
    r=calculate_metrics(reports,10,100)
    assert r['market_cap']==1000 and r['pe']==10 and r['pb']==2
    assert r['revenue_growth_yoy']==.5 and r['profit_growth_yoy']==.5
    assert r['peg_like_ratio']==.2 and r['growth_positive'] is True
    assert r['net_profit_positive_3y_latest'] is True and r['pe_lt_30'] is True
    reports[0]['values']['net_profit_parent']=0
    r=calculate_metrics(reports,10,100)
    assert r['net_profit_positive_3y_latest'] is False and r['profit_growth_yoy']==-1
    assert r['peg_like_ratio'] is None


def test_blocked_latest_annual_not_replaced_by_older():
    r=calculate_metrics([report('2025-12-31',100,200,state='pending_conflict'),report('2024-12-31',80,100)],10,100)
    assert r['pe'] is None and r['pb'] is None
    assert r['fundamental_states']['pe']=='pending_conflict'
    assert r['market_cap']==1000
    missing=calculate_metrics([],10,None,'not_collected_as_of')
    assert missing['pe'] is None and missing['fundamental_states']['pe']=='not_collected_as_of'


def test_whole_conflict_quarantine_and_identical_duplicates(isolated_db,tmp_path,monkeypatch):
    from quant.financial_review import quarantine_existing
    monkeypatch.setattr('quant.financial_review.REVIEW_LOCK',1220001)
    p=tmp_path/'gpcw20251231.dat'
    p.write_bytes(package([('300138',{96:100,314:260420,337:20}),('300138',{96:100,314:260420,337:30})],20251231))
    import_financial_file(p)
    with connect() as conn:
        rows=conn.execute('SELECT standard,review_state,payload FROM financial_report').fetchall()
        assert len(rows)==2 and all(r['standard']=={} and r['review_state']=='pending_conflict' for r in rows)
        payloads=[bytes(r['payload']) for r in rows]
        # Bootstrap audit also handles previously imported unreviewed conflicts.
        conn.execute("UPDATE financial_report SET review_state='unreviewed'")
    audit=quarantine_existing()
    assert audit['quarantined_reports']==1 and audit['quarantined_source_records']==2
    with connect() as conn:
        assert [bytes(r['payload']) for r in conn.execute('SELECT payload FROM financial_report ORDER BY id')]==payloads
    r=load_inputs('300138.SZ',date(2099,1,1))
    assert r['fundamental_basis']['annual_for_pe']['state']=='pending_conflict'
    p=tmp_path/'gpcw20260630.dat'
    p.write_bytes(package([('300138',{96:60,314:260820}),('300138',{96:60,314:260820})]))
    import_financial_file(p)
    with connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM financial_report WHERE review_state='verified'").fetchone()['n']==2


def test_actual_collection_boundary_valuation_and_revision(isolated_db,tmp_path):
    p=tmp_path/'gpcw20251231.dat'
    p.write_bytes(package([('300138',{96:100,271:500,502:2,314:260420})],20251231))
    import_financial_file(p)
    with connect() as conn:
        conn.execute("UPDATE financial_file_revision SET observed_at='2026-10-16 09:00:00+08'")
        conn.execute("UPDATE financial_report SET reviewed_at='2026-10-16 10:00:00+08'")
        conn.execute("INSERT INTO daily_bar(symbol,trade_date,open,high,low,close,volume,amount) VALUES ('300138.SZ','2026-10-14',10,10,10,10,100,1000)")
        conn.execute("INSERT INTO share_observation(symbol,effective_date,total_shares,source,quality,observed_at) VALUES ('300138.SZ','2026-10-14',100,'test','snapshot','2026-10-14 20:00:00+08')")
    early=load_inputs('300138.SZ',date(2026,10,14))
    assert early['pe'] is None and early['fundamental_states']['pe']=='not_collected_as_of'
    assert early['market_cap']==1000
    current=metrics_many(['300138.SZ'],date(2026,10,16))['300138.SZ']
    assert current['pe']==10 and current['pb']==2 and current['price_date']=='2026-10-14'
    assert metrics_many(['300138.SZ'],date(2026,10,16))['300138.SZ']==current
    p.write_bytes(package([('300138',{96:200,271:500,502:2,314:260420})],20251231))
    import_financial_file(p)
    with connect() as conn:
        conn.execute("UPDATE financial_file_revision SET observed_at='2026-10-17 09:00:00+08' WHERE is_current")
        conn.execute("UPDATE financial_report SET reviewed_at='2026-10-17 10:00:00+08' WHERE file_id=(SELECT id FROM financial_file_revision WHERE is_current)")
    assert load_inputs('300138.SZ',date(2026,10,16))['pe']==10
    assert load_inputs('300138.SZ',date(2026,10,17))['pe']==5
    with pytest.raises(ValueError):load_inputs('NOPE',date(2026,10,16))
