from datetime import date
import httpx
import pytest
from test_multiscreener import fixture_data
from test_company_documents import operate
from quant import company_documents as docs
from quant.problem_diagnostics import classify,screen_diagnostics
from quant.multiscreener import evaluate_stock
from quant.corporate_actions import pending_message


@pytest.mark.parametrize('value',['2025-0630','202506-30','20250630','2025/6/30','2025-06-30'])
def test_report_date_known_formats(value):
    assert docs.report_date(value)=='2025-06-30'
    with pytest.raises(ValueError):docs.report_date('2025-1301')
    with pytest.raises(ValueError):docs.report_date('2025-630')


def test_source_date_kept_alongside_normalized_report():
    parsed,_=docs.parse_page(operate().replace(b'2026-06-30',b'2026-0630'),'300138','operate')
    report=next(d for d in parsed if d['kind']=='discussion')
    assert report['report_key']=='2026-06-30' and report['details']['source_report_date']=='2026-0630'


def test_transient_connections_retried_but_not_access_blocks(monkeypatch):
    calls=[];sleeps=[]
    monkeypatch.setattr(docs.time,'sleep',sleeps.append)
    def transient(*args):
        calls.append(args)
        if len(calls)<3:raise httpx.ConnectError('SSL EOF')
        return 'url',b'body'
    monkeypatch.setattr(docs,'_fetch_once',transient)
    assert docs.fetch_page('300138.SZ','operate')==('url',b'body')
    assert len(calls)==3 and sleeps==[1,3]
    def blocked(*args):raise ValueError('HTTP 403')
    monkeypatch.setattr(docs,'_fetch_once',blocked)
    with pytest.raises(ValueError):docs.fetch_page('300138.SZ','operate')
    assert sleeps==[1,3]


def test_retry_has_finite_limit(monkeypatch):
    attempts=[]
    monkeypatch.setattr(docs.time,'sleep',lambda n:None)
    def failure(*args):
        attempts.append(1);raise httpx.ReadTimeout('timeout')
    monkeypatch.setattr(docs,'_fetch_once',failure)
    with pytest.raises(httpx.ReadTimeout):docs.fetch_page('300138.SZ','operate')
    assert len(attempts)==3


def test_missing_current_month_skips_without_modifying_strategy():
    data=fixture_data(850)
    cutoff=date(2030,10,16)
    signals,errors,checks=evaluate_stock('300138.SZ','测试',cutoff,data,['MonthlyMAPullbackStrategy'])
    assert not signals and not errors and checks[0]['status']=='skipped'
    result=screen_diagnostics({'coverage':[{'symbol':'300138.SZ','evaluations':checks}]},cutoff)
    assert result['rows'][0]['category']=='missing_period'


def test_old_errors_grouped_without_rewriting_saved_records():
    errors=[{'symbol':'A','error':'ValueError: 所选区间含尚未支持的扩缩股事件'},
            {'symbol':'B','error':'截至筛选日无日线数据，未执行策略'},
            {'symbol':'C','strategy_id':'MonthlyMAPullbackStrategy','error':'IndexError: single positional indexer is out-of-bounds'}]
    result={'errors':errors,'coverage':[{'symbol':'C','last_date':'2026-09-30','evaluations':[]}]}
    report=screen_diagnostics(result,date(2026,10,16))
    assert report['whole_stock_uncomputed']==2 and report['partial_strategy_error_stocks']==1
    assert report['counts']=={'复权待支持':1,'缺少日线':1,'缺当前月行情':1}
    assert result['errors'][2]['error'].startswith('IndexError')
    assert classify('SSL UNEXPECTED_EOF')[0]=='network'
    assert classify("Invalid isoformat string: '2025-0630'")[0]=='source_date'
    assert '重整调整' in pending_message([{'category':15,'event_date':date(2020,1,1)}])
