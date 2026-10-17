"""Prefetch the latest screening hits; resumable via versioned local documents."""
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
from pathlib import Path
from threading import Lock,Event
import json,time
from quant.db import connect
from quant.services import clean
from quant import company_documents as docs

from quant.config import ROOT
_guard=connect();_guard_conn=_guard.__enter__()
if not _guard_conn.execute('SELECT pg_try_advisory_lock(779951) AS acquired').fetchone()['acquired']:
    raise RuntimeError('已有经营评述批量任务在运行')
_guard_conn.commit()
pace=Lock();stopped=Event();next_request=0.0;blocked=0
with connect() as c:
    run=c.execute('''SELECT r.id,r.effective_date,count(*) AS stocks FROM multi_screen_run r
      JOIN multi_screen_item i ON i.run_id=r.id WHERE jsonb_array_length(i.result->'signals')>0
      GROUP BY r.id,r.effective_date,r.total,r.started_at
      ORDER BY r.effective_date DESC,r.total DESC,r.started_at DESC LIMIT 1''').fetchone()
    if not run:raise RuntimeError('没有可抓取的选股结果')
    targets=c.execute('''SELECT i.symbol,s.name,EXISTS(SELECT 1 FROM company_document d JOIN company_page_revision p ON p.id=d.page_id
      WHERE d.symbol=i.symbol AND d.kind='discussion' AND NOT d.is_excerpt AND p.collected_at>now()-interval '7 days') AS fresh
      FROM multi_screen_item i JOIN security s ON s.symbol=i.symbol WHERE i.run_id=%s
      AND jsonb_array_length(i.result->'signals')>0 ORDER BY i.symbol''',(run['id'],)).fetchall()

destination=ROOT/'.runtime'/f"discussion-prefetch-{run['id']}.json"
destination.parent.mkdir(parents=True,exist_ok=True)
result={'run':clean(run),'started_at':datetime.now(timezone.utc).isoformat(),'status':'running','total':len(targets),
        'processed':0,'cached':0,'saved':0,'failed':0,'items':[]}
def persist():
    temp=destination.with_suffix('.tmp')
    temp.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8');temp.replace(destination)

def fetch(stock):
    global next_request,blocked
    if stock['fresh']:return {**stock,'status':'cached'}
    if stopped.is_set():return {**stock,'status':'deferred','error':'连续访问受限，暂停后续请求'}
    try:
        with pace:
            delay=next_request-time.monotonic()
            if delay>0:time.sleep(delay)
            next_request=time.monotonic()+1.5
        if stopped.is_set():return {**stock,'status':'deferred','error':'连续访问受限，暂停后续请求'}
        url,body=docs.fetch_page(stock['symbol'],'operate')
        parsed,_=docs.parse_page(body,stock['symbol'][:6],'operate')
        discussions=[d for d in parsed if d['kind']=='discussion']
        docs.save_page(stock['symbol'],'operate',url,body)
        with pace:blocked=0
        return {**stock,'status':'saved' if discussions else 'no_discussion','reports':len(discussions),
                'full_reports':sum(not d['is_excerpt'] for d in discussions),'characters':sum(len(d['content']) for d in discussions)}
    except Exception as exc:
        error=str(exc)
        if '403' in error or '429' in error or '证券代码不匹配' in error:
            with pace:
                blocked+=1
                if blocked>=5:stopped.set()
        return {**stock,'status':'failed','error':error[:300]}

persist()
with ThreadPoolExecutor(max_workers=3) as executor:
    pending={executor.submit(fetch,stock):stock for stock in targets}
    for future in as_completed(pending):
        item=future.result();result['items'].append(item);result['processed']+=1
        if item['status'] in ('cached','saved'):result[item['status']]+=1
        elif item['status'] in ('failed','no_discussion'):result['failed']+=1
        persist()
        if result['processed']%10==0 or item['status'] in ('failed','no_discussion'):
            print(f"{result['processed']}/{result['total']} cached={result['cached']} saved={result['saved']} failed={result['failed']} {item['symbol']} {item['status']}",flush=True)
result['status']='partial' if result['failed'] or stopped.is_set() else 'completed'
result['finished_at']=datetime.now(timezone.utc).isoformat();persist()
print(json.dumps({k:v for k,v in result.items() if k!='items'},ensure_ascii=False),flush=True)
_guard.__exit__(None,None,None)
