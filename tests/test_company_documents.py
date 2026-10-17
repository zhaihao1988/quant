from datetime import datetime,timedelta,timezone
import pytest
from fastapi.testclient import TestClient
from test_integration import isolated_db
from quant import company_documents as docs
from quant.db import connect


def page(text,code='300138'):
    return (f'<html><head><meta charset="gbk"><title>晨光生物({code}) F10</title></head><body>'+text+'</body></html>').encode('gb18030')


def operate(content='这是报告完整原文，包含公司业务、未来计划和风险分析。'):
    return page('''<div id="intro"><ul class="main_intro_list"><li><span>主营业务：</span><p>天然植物提取与研发。</p></li></ul></div>
      <div id="observe"><div class="m_tab"><a class="operateTab">2026-06-30</a></div>
      <div class="m_tab_content m_tab_content2"><p class="f14 pr">摘要查看全部▼</p>
      <p class="none clearfix">'''+content+'''<script>不能显示的脚本</script><a class="more">收起▲</a></p></div></div>''')


def test_parser_uses_full_original_and_refuses_wrong_stock():
    documents,encoding=docs.parse_page(operate(),'300138','operate')
    assert encoding=='gb18030'
    discussion=next(d for d in documents if d['kind']=='discussion')
    assert discussion['report_date']=='2026-06-30' and not discussion['is_excerpt']
    assert discussion['content'].startswith('这是报告完整原文')
    assert '脚本' not in discussion['content'] and '摘要' not in discussion['content']
    with pytest.raises(ValueError,match='证券代码不匹配'):docs.parse_page(operate(),'600036','operate')
    with pytest.raises(ValueError):docs.parse_page(page('<p>请完成验证</p>'),'300138','operate')


def test_parser_company_and_executive_biographies():
    body=page('''<div id="detail"><table><tr><td><strong>公司名称：</strong><span>晨光生物科技集团股份有限公司</span></td></tr></table></div>
      <div id="manager"><table class="ggintro"><thead><tr><td><h3>卢庆国</h3></td><td class="jobs">董事长</td><td class="date">公告日期：2026-07-03</td></tr></thead>
      <tbody><tr><td class="mainintro"><p>公司创始人，拥有多年植物提取行业经验。</p></td></tr></tbody></table></div>''')
    result,_=docs.parse_page(body,'300138','company')
    assert {d['kind'] for d in result}=={'company','executives'}
    assert result[1]['details']['people'][0]['name']=='卢庆国'


def test_versions_failure_preserves_cache_and_read_never_fetches(isolated_db,monkeypatch):
    url=f'{docs.BASE}/300138/operate.html'
    docs.save_page('300138.SZ','operate',url,operate())
    docs.save_page('300138.SZ','operate',url,operate())
    with connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM company_page_revision').fetchone()['n']==1
    docs.save_page('300138.SZ','operate',url,operate('这是修订后的报告原文，包含新的经营信息与公司风险。'))
    monkeypatch.setattr(docs._executor,'submit',lambda fn,*args:fn(*args))
    def fail(*args):raise ValueError('同花顺返回HTTP 403，请稍后重试')
    monkeypatch.setattr(docs,'fetch_page',fail)
    job,reused=docs.reserve_refresh('300138.SZ',True)
    assert not reused
    current=docs.documents('300138.SZ')
    assert current['refresh']['status']=='failed'
    assert next(d for d in current['documents'] if d['kind']=='discussion')['content'].startswith('这是修订')
    assert docs.reserve_refresh('300138.SZ',True)==(job,True)
    from quant.api import app
    with TestClient(app) as client:
        assert client.get('/api/company-documents/300138.SZ').json()['documents']
        assert client.get('/api/company-documents/999999.SZ').status_code==400
        assert client.post('/api/company-documents/300138.SZ/refresh',json={'force':True},headers={'origin':'https://example.com'}).status_code==403
        detail=client.get('/api/stock-detail/300138.SZ')
        assert detail.status_code==200 and detail.json()['fundamentals']['pe'] is None
    with connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM company_page_revision').fetchone()['n']==2


def test_interrupted_refresh_can_be_retried(isolated_db,monkeypatch):
    from uuid import uuid4
    with connect() as conn:
        conn.execute("INSERT INTO company_refresh(symbol,job_id,status,started_at) VALUES ('300138.SZ',%s,'running',now()-interval '5 minutes')",(uuid4(),))
    assert docs.documents('300138.SZ')['refresh']['status']=='interrupted'
    submitted=[]
    monkeypatch.setattr(docs._executor,'submit',lambda *args:submitted.append(args))
    assert docs.reserve_refresh('300138.SZ',True)[1] is False
    assert len(submitted)==1
    assert docs.reserve_refresh('300138.SZ',True)[1] is True


def test_partial_refresh_keeps_successful_page_and_throttles(isolated_db,monkeypatch):
    def fetch(symbol,kind):
        if kind=='company':raise ValueError('页面暂不可用')
        return f'{docs.BASE}/300138/operate.html',operate()
    monkeypatch.setattr(docs,'fetch_page',fetch)
    monkeypatch.setattr(docs._executor,'submit',lambda fn,*args:fn(*args))
    docs.reserve_refresh('300138.SZ')
    current=docs.documents('300138.SZ')
    assert current['refresh']['status']=='partial' and len(current['documents'])==2
    assert not current['auto_refresh_due']
    with connect() as conn:
        conn.execute("UPDATE company_refresh SET finished_at=now()-interval '2 hours'")
    assert docs.documents('300138.SZ')['auto_refresh_due']


def test_unchanged_old_content_uses_last_successful_check_for_ttl(isolated_db):
    from uuid import uuid4
    docs.save_page('300138.SZ','operate',f'{docs.BASE}/300138/operate.html',operate())
    with connect() as conn:
        conn.execute("UPDATE company_page_revision SET collected_at=now()-interval '8 days'")
        conn.execute("INSERT INTO company_refresh(symbol,job_id,status,finished_at,last_success_at) VALUES ('300138.SZ',%s,'completed',now(),now())",(uuid4(),))
    current=docs.documents('300138.SZ')
    assert not current['stale'] and not current['auto_refresh_due']
