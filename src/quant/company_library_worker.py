"""Bounded background collector. Every completed stock is committed independently."""
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from threading import Lock
from datetime import datetime,timezone
import logging,sys,time
from .db import connect
from . import company_documents as docs
from .company_library import WORKER_LOCK

INTERVAL=1.5
_pace=Lock()
_next_request=0.0


def fetch(symbol):
    global _next_request
    with _pace:
        delay=_next_request-time.monotonic()
        if delay>0:time.sleep(delay)
        _next_request=time.monotonic()+INTERVAL
    try:
        url,body=docs.fetch_page(symbol,'operate')
        parsed,_=docs.parse_page(body,symbol[:6],'operate')
        reports=[d for d in parsed if d['kind']=='discussion' and not d['is_excerpt']]
        docs.save_page(symbol,'operate',url,body)
        return {'status':'saved' if reports else 'no_content','report_count':len(reports),
                'error':None if reports else '来源页面未提供经营评述全文'}
    except Exception as exc:
        logging.exception('Collection failed for %s',symbol)
        return {'status':'failed','report_count':0,'error':str(exc)[:500]}


def run(job_id):
    with connect() as guard:
        if not guard.execute('SELECT pg_try_advisory_lock(%s) AS acquired',(WORKER_LOCK,)).fetchone()['acquired']:
            with connect() as c:c.execute("UPDATE company_collection_job SET status='paused',message='已有后台采集进程，请稍后继续' WHERE id=%s AND status='queued'",(job_id,))
            return
        guard.commit()
        try:_run(job_id)
        except Exception:
            logging.exception('Collection coordinator failed')
            with connect() as c:c.execute("UPDATE company_collection_job SET status='paused',message='后台采集中断，可继续补齐',heartbeat_at=now() WHERE id=%s AND status IN ('queued','running')",(job_id,))
        finally:
            guard.execute('SELECT pg_advisory_unlock(%s)',(WORKER_LOCK,));guard.commit()


def _run(job_id):
    with connect() as c:
        row=c.execute("UPDATE company_collection_job SET status='running',started_at=coalesce(started_at,now()),heartbeat_at=now() WHERE id=%s AND status='queued' RETURNING id",(job_id,)).fetchone()
    if not row:return
    consecutive=0;source_paused=False;last_beat=0
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures={};pause=False
        while True:
            with connect() as c:
                job=c.execute('SELECT status,pause_requested FROM company_collection_job WHERE id=%s',(job_id,)).fetchone()
                if not job or job['status']!='running':pause=True
                pause=pause or bool(job and job['pause_requested']) or source_paused
                if time.monotonic()-last_beat>5:
                    c.execute('UPDATE company_collection_job SET heartbeat_at=now() WHERE id=%s',(job_id,));last_beat=time.monotonic()
                if not pause and len(futures)<3:
                    pending=c.execute("SELECT symbol FROM company_collection_item WHERE job_id=%s AND status='pending' ORDER BY symbol LIMIT %s",(job_id,3-len(futures))).fetchall()
                    for item in pending:
                        c.execute("UPDATE company_collection_item SET status='running',started_at=now(),attempts=attempts+1 WHERE job_id=%s AND symbol=%s",(job_id,item['symbol']))
            # Submit only after the running markers are committed.
            if not pause:
                for item in pending if len(futures)<3 else []:
                    futures[executor.submit(fetch,item['symbol'])]=item['symbol']
            if not futures:break
            done,_=wait(futures,timeout=1,return_when=FIRST_COMPLETED)
            for future in done:
                symbol=futures.pop(future);result=future.result()
                blocked=result['status']=='failed' and any(t in (result['error'] or '') for t in ('403','429','证券代码不匹配'))
                consecutive=consecutive+1 if blocked else 0
                source_paused=source_paused or consecutive>=5
                with connect() as c:
                    c.execute('''UPDATE company_collection_item SET status=%s,report_count=%s,error=%s,finished_at=now()
                      WHERE job_id=%s AND symbol=%s''',(result['status'],result['report_count'],result['error'],job_id,symbol))
                logging.info('%s %s %s',symbol,result['status'],result['report_count'])
    with connect() as c:
        pending=c.execute("SELECT count(*) AS n FROM company_collection_item WHERE job_id=%s AND status IN ('pending','running')",(job_id,)).fetchone()['n']
        failed=c.execute("SELECT count(*) AS n FROM company_collection_item WHERE job_id=%s AND status='failed'",(job_id,)).fetchone()['n']
        state='paused' if pause or pending else 'partial' if failed else 'completed'
        message='来源连续拒绝访问，已暂停，请稍后继续' if source_paused else '已暂停，可继续补齐' if state=='paused' else '采集完成，来源无正文的股票已单独标记'
        c.execute('''UPDATE company_collection_job SET status=%s,message=%s,heartbeat_at=now(),finished_at=%s WHERE id=%s''',
                  (state,message,None if state=='paused' else datetime.now(timezone.utc),job_id))


if __name__=='__main__':
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    run(sys.argv[1])
