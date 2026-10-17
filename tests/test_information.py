from datetime import datetime,timedelta,timezone
from hashlib import sha256
from uuid import uuid4
import pytest
from psycopg.types.json import Jsonb
from test_integration import isolated_db
from quant.db import connect
from quant import information as info,embeddings as emb,events,news_sources

def record(title='产能投产',content='公司计划建设产能，尚未投产。',source_id='1'):
 return dict(source_id=source_id,title=title,content=content,published_at=datetime.now(timezone.utc)-timedelta(minutes=1),source_url='https://example.com/1',raw={'id':source_id})

def fake_vectors(texts,query=False):return [[1.0]+[0.0]*1023 for t in texts]

def test_news_idempotent_and_correction_transactional_queue(isolated_db):
 r=record();assert info.save_news('ths',r);assert not info.save_news('ths',r)
 with connect() as c:
  assert c.execute('SELECT count(*) n FROM news_revision').fetchone()['n']==1
  assert c.execute('SELECT generation FROM evidence_queue').fetchone()['generation']==1
 assert info.save_news('ths',{**r,'content':'公司已投产，公告更正。'})
 with connect() as c:
  assert c.execute('SELECT count(*) n FROM news_revision').fetchone()['n']==2
  assert c.execute('SELECT generation FROM evidence_queue').fetchone()['generation']==2

def test_embedding_checks_order_dimensions_nonfinite_and_zero():
 vectors=fake_vectors(['a','b']);data=[{'index':1,'embedding':vectors[1]},{'index':0,'embedding':vectors[0]}]
 assert emb.validate(data,2)==vectors
 for bad in ([{'index':0,'embedding':[1]}],[{'index':0,'embedding':[0]*1024}],[{'index':0,'embedding':[float('nan')]+[0]*1023}],[{'index':2,'embedding':vectors[0]}]):
  with pytest.raises(ValueError):emb.validate(bad,1)
 with pytest.raises(ValueError):emb.validate([data[1],data[1]],2)

def test_token_chunks_exact_source_and_bounded():
 text='中文产品与产能，保留否认及财务数字123.45。'*800
 parts=list(emb.chunks(text));assert len(parts)>5
 assert all(p in text and len(emb.tokenizer().encode(p).ids)<=512 for p in parts)

def test_atomic_index_reuses_cache_and_excludes_stale_versions(isolated_db,monkeypatch):
 calls=[]
 monkeypatch.setattr(emb,'embed',lambda texts,query=False:calls.append(len(texts)) or fake_vectors(texts))
 r=record();info.save_news('ths',r);assert info.index_one()
 assert len(info.search('产能','news')['evidence'])==1
 # Cross-source duplicates share content embeddings but preserve provenance.
 info.save_news('cls',r);assert info.index_one();assert calls[:2]==[1,1] # index + search; second index reuses cached vector
 with connect() as c:
  assert c.execute('SELECT count(*) n FROM embedding_cache').fetchone()['n']==2 # one passage plus cached query
  assert c.execute('SELECT count(*) n FROM evidence_chunk').fetchone()['n']==2
 info.save_news('ths',{**r,'content':'公司明确否认产品投产。'})
 # A pending revision must never return the old active content as current evidence.
 assert len(info.search('产能','news')['evidence'])==1
 assert info.index_one()
 with connect() as c:assert c.execute("SELECT content FROM evidence_chunk WHERE doc_key='1'").fetchone()['content'].endswith('公司明确否认产品投产。')

def test_new_change_during_api_call_cannot_activate_obsolete_generation(isolated_db,monkeypatch):
 r=record();info.save_news('ths',r)
 def change(texts,query=False):
  info.save_news('ths',{**r,'content':'原消息撤回，尚未投产。'})
  return fake_vectors(texts)
 monkeypatch.setattr(emb,'embed',change);info.index_one()
 with connect() as c:
  assert c.execute('SELECT count(*) n FROM evidence_chunk').fetchone()['n']==0
  assert c.execute('SELECT state FROM evidence_queue').fetchone()['state']=='pending'
 monkeypatch.setattr(emb,'embed',fake_vectors);info.index_one()
 with connect() as c:assert '撤回' in c.execute('SELECT content FROM evidence_chunk').fetchone()['content']

def test_failed_embedding_preserves_original_and_retryable_queue(isolated_db,monkeypatch):
 info.save_news('ths',record())
 monkeypatch.setattr(emb,'embed',lambda *a,**k:(_ for _ in ()).throw(ValueError('向量模型HTTP 429')))
 info.index_one()
 with connect() as c:
  assert c.execute('SELECT count(*) n FROM news_revision').fetchone()['n']==1
  assert c.execute('SELECT state,error FROM evidence_queue').fetchone()=={'state':'failed','error':'向量模型HTTP 429'}

def test_lease_recovery_and_single_claim(isolated_db):
 info.save_news('ths',record());first=info.claim();assert first and not info.claim()
 with connect() as c:c.execute("UPDATE evidence_queue SET lease_until=now()-interval '1 second'")
 second=info.claim();assert second and first['lease']!=second['lease']

def test_asof_excludes_future_collected_or_published(isolated_db,monkeypatch):
 monkeypatch.setattr(emb,'embed',fake_vectors)
 info.save_news('ths',record());info.index_one()
 assert not info.search('产能','news',as_of=datetime.now(timezone.utc)-timedelta(days=1))['evidence']

def test_source_failure_does_not_advance_and_empty_is_not_success(isolated_db,monkeypatch):
 watermark=datetime.now(timezone.utc)-timedelta(days=1)
 with connect() as c:c.execute("UPDATE news_source_state SET watermark=%s WHERE source='ths'",(watermark,))
 monkeypatch.setattr(news_sources,'fetch',lambda *a:(_ for _ in ()).throw(ValueError('来源HTTP 403')))
 r=info.poll_source('ths');assert not r['complete']
 with connect() as c:assert c.execute("SELECT watermark FROM news_source_state WHERE source='ths'").fetchone()['watermark']==watermark

def test_overlapping_pages_idempotence_and_watermark(isolated_db,monkeypatch):
 r=record();r['published_at']=datetime.now(timezone.utc)-timedelta(days=2)
 monkeypatch.setattr(news_sources,'fetch',lambda *a:[r])
 assert info.poll_source('ths')['complete'];assert info.poll_source('ths')['saved']==0
 with connect() as c:assert c.execute('SELECT count(*) n FROM news_article').fetchone()['n']==1

def test_evidence_validator_rejects_hallucination_and_wrong_stock():
 evidence=[dict(id=1,symbol='300138.SZ',content='公司尚未投产，正在建设。')]
 good={'summary':'需核验','items':[{'symbol':'300138.SZ','relation':'否认或不确定','reason':'未投产','evidence':[{'id':1,'quote':'公司尚未投产'}]}]}
 assert events.validate_result(good,evidence)['items'][0]['relation']=='否认或不确定'
 good['items'][0]['evidence'][0]['quote']='公司已经投产'
 with pytest.raises(ValueError):events.validate_result(good,evidence)
 good['items'][0]['evidence'][0]['quote']='公司尚未投产';good['items'][0]['symbol']='600036.SH'
 with pytest.raises(ValueError):events.validate_result(good,evidence)

def test_http_controls_and_validation(isolated_db,monkeypatch):
 from fastapi.testclient import TestClient
 from quant.api import app
 monkeypatch.setattr(info,'launch',lambda:None)
 client=TestClient(app)
 assert client.get('/api/information/status').status_code==200
 assert client.post('/api/information/control',json={'action':'start'}).json()['runtime']['enabled']
 assert not client.post('/api/information/control',json={'action':'pause'}).json()['runtime']['enabled']
 assert client.post('/api/information/control',json={'action':'bad'}).status_code==400
 assert client.post('/api/evidence/search',json={'query':''}).status_code==422

def test_research_metadata_revisions_and_periodic_pdf_check(isolated_db,monkeypatch):
 from quant import research,research_sources as source
 from test_research import record as rr,pages,extracted
 r=rr();doc=research.save_metadata('300138.SZ',r);calls=[]
 monkeypatch.setattr(source,'download_pdf',lambda url:calls.append(url) or b'%PDF-a')
 monkeypatch.setattr(source,'pdf_pages',lambda b:pages())
 monkeypatch.setattr(source,'extract',lambda p:extracted())
 research.process_document(doc);research.process_document(doc);assert len(calls)==1
 with connect() as c:c.execute("UPDATE research_document SET content_checked_at=now()-interval '31 days'")
 research.process_document(doc);assert len(calls)==2
 changed=research.save_metadata('300138.SZ',{**r,'title':'更正后的标题'})
 assert changed['title']=='更正后的标题'
 with connect() as c:assert c.execute('SELECT count(*) n FROM research_metadata_revision').fetchone()['n']==2

def test_stock_events_empty_and_semantic_candidates_are_explicit(isolated_db,monkeypatch):
 monkeypatch.setattr(emb,'embed',fake_vectors)
 result=events.stock_events('300138.SZ');assert result['evidence']==[]
 info.save_news('ths',record());info.index_one()
 result=events.stock_events('300138.SZ')
 assert result['evidence'][0]['match_type']=='语义候选，关联待核验'
 with connect() as c:c.execute("INSERT INTO daily_bar(symbol,trade_date,open,high,low,close,volume,amount) VALUES('300138.SZ',current_date-1,10,10,10,10,100,1000)")
 assert events.stock_events('300138.SZ')['evidence'][0]['timing']=='最近收盘后，后续观察'

def test_failed_model_snapshot_persists_and_identical_evidence_reuses_success(isolated_db,monkeypatch):
 evidence=[{'id':1,'content':'原文尚未投产','symbol':'300138.SZ'}]
 def fail(*a):raise ValueError('模型HTTP 503')
 monkeypatch.setattr(events,'model_analysis',fail)
 failed=events.create_analysis('测试',evidence,emb.profile(),datetime.now(timezone.utc));assert failed['status']=='failed' and failed['error']=='模型HTTP 503'
 with connect() as c:assert c.execute('SELECT status FROM evidence_analysis').fetchone()['status']=='failed'
 calls=[]
 monkeypatch.setattr(events,'model_analysis',lambda *a:calls.append(1) or {'summary':'证据不足','items':[]})
 first=events.create_analysis('测试',evidence,emb.profile(),datetime.now(timezone.utc))
 second=events.create_analysis('测试',evidence,emb.profile(),datetime.now(timezone.utc))
 assert first['id']==second['id'] and calls==[1]

def test_active_initial_backfill_rolls_across_midnight_incrementally(isolated_db,monkeypatch):
 from quant import research,research_sources as source
 from datetime import date
 today=date.today();job=uuid4()
 with connect() as c:
  c.execute("INSERT INTO research_job(id,status,start_date,end_date,total) VALUES(%s,'running',%s,%s,1)",(job,today-timedelta(days=365),today-timedelta(days=1)))
  c.execute("INSERT INTO research_item(job_id,symbol,ordinal,status) VALUES(%s,'300138.SZ',0,'saved')",(job,))
  c.execute("INSERT INTO research_checkpoint(symbol,checked_through,full_checked_at) VALUES('300138.SZ',%s,now())",(today-timedelta(days=1),))
 research.roll_forward_active()
 with connect() as c:
  assert c.execute('SELECT end_date FROM research_job').fetchone()['end_date']==today
  assert c.execute('SELECT status FROM research_item').fetchone()['status']=='pending'
 calls=[]
 monkeypatch.setattr(source,'fetch_announcements',lambda symbol,start,end:calls.append((start,end)) or [])
 assert research.collect_stock(job,'300138.SZ')['status']=='no_records'
 assert calls==[(today-timedelta(days=8),today)]
 with connect() as c:c.execute("UPDATE research_item SET status='no_records'")
 research.roll_forward_active()
 with connect() as c:assert c.execute('SELECT status FROM research_item').fetchone()['status']=='no_records'
 with connect() as c:c.execute("INSERT INTO security(symbol,code,market,name) VALUES('600036.SH','600036','SH','招商银行')")
 research.roll_forward_active()
 with connect() as c:
  assert c.execute('SELECT total FROM research_job').fetchone()['total']==2
  assert c.execute("SELECT status FROM research_item WHERE symbol='600036.SH'").fetchone()['status']=='pending'

def test_pdf_url_change_does_not_index_old_body_with_new_metadata(isolated_db,monkeypatch):
 from quant import research,research_sources as source
 from test_research import record as rr,pages,extracted
 r=rr();doc=research.save_metadata('300138.SZ',r)
 monkeypatch.setattr(source,'download_pdf',lambda url:b'%PDF-a')
 monkeypatch.setattr(source,'pdf_pages',lambda b:pages())
 monkeypatch.setattr(source,'extract',lambda p:extracted())
 research.process_document(doc)
 assert info.load_document('research',str(doc['id']))
 research.save_metadata('300138.SZ',{**r,'pdf_url':r['pdf_url'].replace('.PDF','-corrected.PDF')})
 assert info.load_document('research',str(doc['id'])) is None

def test_exact_news_mention_can_support_citation_but_not_arbitrary_stock():
 evidence=[{'id':'news:1','symbol':None,'mentioned_symbols':['300138.SZ'],'content':'晨光生物公告，尚未投产。'}]
 item={'symbol':'300138.SZ','relation':'否认或不确定','reason':'尚未投产','evidence':[{'id':'news:1','quote':'晨光生物公告'}]}
 assert events.validate_result({'summary':'核对','items':[item]},evidence)['items'][0]['symbol']=='300138.SZ'
 item['symbol']='600036.SH'
 with pytest.raises(ValueError):events.validate_result({'summary':'核对','items':[item]},evidence)

def test_streamed_model_requires_complete_output_and_retries_bad_quote(monkeypatch):
 import json
 monkeypatch.setenv('QUANT_SILICONFLOW_API_KEY','test-not-a-real-key')
 evidence=[{'id':1,'symbol':'300138.SZ','content':'公司尚未投产，保持谨慎。'}]
 result={'summary':'需核验','items':[{'symbol':'300138.SZ','relation':'否认或不确定','reason':'未投产','evidence':[{'id':1,'quote':'公司尚未投产'}]}]}
 wrong=json.loads(json.dumps(result));wrong['items'][0]['evidence'][0]['quote']='公司已经投产'
 calls=[]
 class Reply:
  status_code=200
  def __init__(self,content,finish='stop'):self.content=content;self.finish=finish
  def __enter__(self):return self
  def __exit__(self,*a):pass
  def iter_lines(self):
   raw=json.dumps(self.content,ensure_ascii=False)
   yield 'data: '+json.dumps({'choices':[{'delta':{'content':raw[:20]}}]})
   yield 'data: '+json.dumps({'choices':[{'delta':{'content':raw[20:]},'finish_reason':self.finish}]})
 class Client:
  def __init__(self,*a,**k):pass
  def __enter__(self):return self
  def __exit__(self,*a):pass
  def stream(self,*a,**k):
   calls.append(k['json']);return Reply(wrong if len(calls)==1 else result)
 monkeypatch.setattr(events.httpx,'Client',Client)
 assert events.model_analysis('新闻',evidence)==result and len(calls)==2
 assert calls[0]['stream'] is True
 class Truncated(Client):
  def stream(self,*a,**k):return Reply(result,'length')
 monkeypatch.setattr(events.httpx,'Client',Truncated)
 with pytest.raises(ValueError,match='未完成'):events.model_analysis('新闻',evidence)

def test_model_sentence_references_copy_original_punctuation_and_negation():
 evidence=[{'id':'news:12','symbol':None,'mentioned_symbols':['605066.SH'],'content':'天正电气：产品进展\n公司尚未投产，保持谨慎。未来仍不确定。'}]
 result={'summary':'事实','items':[{'symbol':'605066.SH','relation':'否认或不确定','reason':'尚未投产','evidence':[{'id':'news:12','sentence_id':2}]}]}
 validated=events.validate_result(result,evidence)
 assert validated['items'][0]['evidence'][0]['quote']=='公司尚未投产，保持谨慎。'
 result['items'][0]['evidence'][0]['sentence_id']=99
 with pytest.raises(ValueError):events.validate_result(result,evidence)
