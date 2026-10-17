from datetime import date,datetime,timezone
import json
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from test_integration import isolated_db
from quant.db import connect
from quant import research as lib,research_sources as source,research_worker as worker


@pytest.fixture(autouse=True)
def separate_jobs(monkeypatch):
    token=1000000000+uuid4().int%1000000000
    monkeypatch.setattr(lib,'RESERVE_LOCK',token);monkeypatch.setattr(lib,'WORKER_LOCK',token+1)
    monkeypatch.setattr(lib,'DOCUMENT_LOCK',token+2)
    monkeypatch.setenv('QUANT_SILICONFLOW_API_KEY','test-secret-not-real')
    monkeypatch.setattr(lib,'launch',lambda job:None)


def record(source_id='001'):
    return dict(source_id=source_id,title='投资者关系活动记录表',published_at=datetime.now(timezone.utc),source_url='https://www.cninfo.com.cn/new/disclosure/detail?announcementId='+source_id,pdf_url='https://static.cninfo.com.cn/finalpage/2026-10-17/'+source_id+'.PDF')


def pages():return [{'page':1,'text':'介绍：'+('公司保持正常经营。'*15)+'\n问题：产能多少？\n回答：2万吨，尚未投产。'}]


def extracted():return {'qa_pairs':[{'question':'产能多少？','answer':'2万吨，尚未投产。','pages':[1]}],'topics':['产能'],'participants':[]}


def test_calendar_year_and_document_address_guards():
    assert lib.window(date(2024,2,29))==(date(2023,2,28),date(2024,2,29))
    assert source.attachment_url('finalpage/2026-10-17/1.PDF').startswith('https://static.cninfo.com.cn/')
    for bad in ['https://evil.example/a.PDF','//evil.example/a.PDF','http://static.cninfo.com.cn/finalpage/1.PDF']:
        with pytest.raises(ValueError):source.attachment_url(bad)


def test_verbatim_validation_keeps_numbers_negation_and_rejects_hallucination():
    result=source.validate_extraction(extracted(),pages()[0]['text'],[1])
    assert result['qa_pairs'][0]['answer']=='2万吨，尚未投产。'
    wrong=extracted();wrong['qa_pairs'][0]['answer']='2万吨，已经投产。'
    with pytest.raises(ValueError,match='原文核对'):source.validate_extraction(wrong,pages()[0]['text'],[1])
    with pytest.raises(ValueError):source.validate_extraction({'qa_pairs':'x'},'x',[1])
    assert source.validate_extraction({'qa_pairs':[]},'没有问答',[1])['qa_pairs']==[]


def test_chunks_cross_pages_and_overlap_deduplicates(monkeypatch):
    parts=list(source.chunks([{'page':1,'text':'a'*6000},{'page':2,'text':'b'*6000}]))
    assert len(parts)>1 and parts[0]['pages']==[1] and parts[1]['pages']==[1,2]
    assert parts[1]['text'][:500]==parts[0]['text'][-500:]
    monkeypatch.setattr(source,'extract_chunk',lambda chunk:extracted())
    assert len(source.extract([{'page':1,'text':'x'*15000}])['qa_pairs'])==1


def test_list_failure_not_misrepresented_as_empty(monkeypatch):
    monkeypatch.setattr(source,'mapping',lambda:{})
    with pytest.raises(ValueError,match='映射缺失'):source.fetch_announcements('300138.SZ',date(2026,1,1),date(2026,10,17))


def test_model_anchors_copy_complete_original_not_generated_answer():
    text='问题：产能多少？\n答：您好，公司产能为2万吨，\n尚未投产。具体经营情况请参阅定期报告。谢谢关注。'
    data={'qa_pairs':[{'question':'产能多少？','answer_start':'您好，公司产能为2万吨，尚未投产。','answer_end':'具体经营情况请参阅定期报告。谢谢关注。'}]}
    result=source.validate_extraction(data,text,[1,2])
    assert result['qa_pairs'][0]['answer']=='您好，公司产能为2万吨，\n尚未投产。具体经营情况请参阅定期报告。谢谢关注。'
    data['qa_pairs'][0]['answer_start']='尚未投产。'
    with pytest.raises(ValueError,match='省略'):source.validate_extraction(data,text,[1,2])


def test_original_saved_before_model_failure_retry_and_no_duplicate_calls(isolated_db,monkeypatch):
    doc=lib.save_metadata('300138.SZ',record());calls=[]
    monkeypatch.setattr(source,'download_pdf',lambda url:calls.append('download') or b'%PDF-original')
    monkeypatch.setattr(source,'pdf_pages',lambda body:pages())
    def fail(pages):raise ValueError('模型HTTP 503')
    monkeypatch.setattr(source,'extract',fail)
    with pytest.raises(ValueError):lib.process_document(doc)
    with connect() as c:
        row=c.execute('SELECT * FROM research_revision').fetchone()
        assert bytes(row['body'])==b'%PDF-original' and row['status']=='failed' and row['pages']==pages()
    monkeypatch.setattr(source,'extract',lambda pages:calls.append('model') or extracted())
    assert lib.process_document(doc)=='ready' and lib.process_document(doc)=='ready'
    assert calls==['download','model']
    assert len(lib.documents('300138.SZ')['documents'])==1
    assert lib.save_metadata('300138.SZ',record())['id']==doc['id']
    with connect() as c:assert c.execute('SELECT count(*) AS n FROM research_revision').fetchone()['n']==1


def test_scanned_pdf_preserved_and_marked_needs_ocr(isolated_db,monkeypatch):
    doc=lib.save_metadata('300138.SZ',record())
    monkeypatch.setattr(source,'download_pdf',lambda url:b'%PDF-scanned')
    monkeypatch.setattr(source,'pdf_pages',lambda body:[{'page':1,'text':''}])
    monkeypatch.setattr(source,'extract',lambda pages:pytest.fail('Scanned PDF must not be sent empty to model'))
    assert lib.process_document(doc)=='needs_ocr'


def test_all_stock_job_pause_resume_failure_retry_and_http(isolated_db,monkeypatch):
    with connect() as c:c.execute("INSERT INTO security(symbol,code,market,name) VALUES ('600036.SH','600036','SH','招商银行')")
    monkeypatch.setattr(source,'fetch_announcements',lambda symbol,*args:[] if symbol=='600036.SH' else [record()])
    monkeypatch.setattr(source,'download_pdf',lambda url:b'%PDF-original')
    monkeypatch.setattr(source,'pdf_pages',lambda body:pages())
    monkeypatch.setattr(source,'extract',lambda pages:extracted())
    job=lib.start();assert lib.start()==job
    lib.pause(job);worker.run(job);assert lib.status()['job']['status']=='paused'
    lib.resume(job);worker.run(job)
    assert lib.status()['job']['counts']=={'no_records':1,'saved':1}
    from quant.api import app
    with TestClient(app) as client:
        data=client.get('/api/research/300138.SZ').json();revision=data['documents'][0]['revision_id']
        assert data['documents'][0]['extraction']['qa_pairs'][0]['answer']=='2万吨，尚未投产。'
        assert client.get(f'/api/research-original/{revision}/pdf').content==b'%PDF-original'
        assert client.get(f'/api/research-original/{revision}/text').json()['pages']==pages()
        assert client.get('/api/research-jobs').json()['summary']['ready']==1
        assert client.post('/api/research-jobs',json={},headers={'Origin':'https://evil.example'}).status_code==403


def test_old_published_records_filtered_and_failed_stock_retried(isolated_db,monkeypatch):
    old=record();old['published_at']=datetime(2020,1,1,tzinfo=timezone.utc)
    lib.save_metadata('300138.SZ',old)
    assert lib.documents('300138.SZ')['documents']==[]
    monkeypatch.setattr(source,'fetch_announcements',lambda *args:(_ for _ in ()).throw(ValueError('来源HTTP 500')))
    job=lib.start();worker.run(job);assert lib.status()['job']['status']=='partial'
    retry=lib.start('retry');monkeypatch.setattr(source,'fetch_announcements',lambda *args:[]);worker.run(retry)
    assert lib.status()['job']['counts']=={'no_records':1}


def test_key_redaction():
    assert 'test-secret-not-real' not in lib.safe_error(ValueError('test-secret-not-real'))


def test_incomplete_model_classification_retried_with_explicit_missing_ids(monkeypatch):
    calls=[]
    class Reply:
        status_code=200
        def json(self):
            return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'qa_ids':[] if len(calls)==1 else [1],'excluded_ids':[]})}}]}
    class Client:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,*args,**kwargs):calls.append(kwargs['json']['messages'][:]);return Reply()
    monkeypatch.setattr(source.httpx,'Client',lambda **kwargs:Client())
    result=source.extract_chunk({'text':'1、产能多少？\n您好，公司产能2万吨，尚未投产。','pages':[1]})
    assert len(calls)==2 and '必须恰好覆盖这些id：1' in calls[-1][-1]['content']
    assert result['qa_pairs'][0]['answer']=='您好，公司产能2万吨，尚未投产。'


def test_numbered_candidates_require_explicit_answers_and_keep_cross_page_text():
    text='1、董事长张某\n2、秘书李某\n主要内容\n1、产能多少？\n您好，公司产能2万吨，\n尚未投产。\n2、海外销售情况？\n答：公司海外收入占比12%，存在风险。\n附件清单（如有）'
    pairs=source.candidate_pairs(text)
    assert len(pairs)==2 and pairs[0]['answer']=='您好，公司产能2万吨，\n尚未投产。'
    assert pairs[1]['answer']=='公司海外收入占比12%，存在风险。'
    greeting=source.candidate_pairs('1、您好，请问原料供应如何？\n您好，公司供应稳定，谢谢关注。')
    assert len(greeting)==1 and greeting[0]['question']=='您好，请问原料供应如何？'
    plain=source.candidate_pairs('1. 请介绍资产质量？\n我行资产质量保持稳定。\n2. 地产敞口情况：\n我行地产敞口下降。')
    assert len(plain)==2 and plain[0]['answer']=='我行资产质量保持稳定。'


def test_numbered_line_selection_copies_all_original_answer_lines():
    text='1、产能多少？\n您好，公司产能2万吨，\n尚未投产。\n2、海外销售情况？\n公司海外收入占比12%，存在风险。\n附件清单（如有）'
    result=source.validate_extraction({'spans':[{'question_start':1,'question_end':1},{'question_start':4,'question_end':4}]},text,[1])
    assert result['qa_pairs'][0]['answer']=='您好，公司产能2万吨，\n尚未投产。'
    assert result['qa_pairs'][1]['question']=='海外销售情况？'
    assert result['qa_pairs'][1]['answer']=='公司海外收入占比12%，存在风险。'
    with pytest.raises(ValueError):source.validate_extraction({'spans':[{'question_start':400,'question_end':401}]},text,[1])


def test_question_locations_copy_original_complete_answers():
    text='1、产能多少？\n您好，公司产能2万吨，尚未投产。\n2、海外销售情况？\n公司海外收入占比12%，存在风险。\n附件清单（如有）\n日期 2026年10月17日'
    result=source.validate_extraction({'questions':['产能多少？','海外销售情况？']},text,[1])
    assert [p['answer'] for p in result['qa_pairs']]==['您好，公司产能2万吨，尚未投产。','公司海外收入占比12%，存在风险。']
    with pytest.raises(ValueError,match='遗漏'):source.validate_extraction({'questions':['产能多少？']},text,[1])
