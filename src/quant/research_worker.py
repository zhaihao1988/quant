"""Independent background worker; no API secrets or usage statistics in logs."""
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from datetime import datetime,timezone
import logging,sys,time
from .db import connect
from . import research as research


def run(job_id):
    with connect() as guard:
        if not guard.execute('SELECT pg_try_advisory_lock(%s) AS ok',(research.WORKER_LOCK,)).fetchone()['ok']:
            with connect() as c:c.execute("UPDATE research_job SET status='paused',message='已有调研后台进程，请稍后继续' WHERE id=%s",(job_id,))
            return
        guard.commit()
        try:coordinate(job_id)
        except Exception as exc:
            with connect() as c:c.execute("UPDATE research_job SET status='paused',message=%s,heartbeat_at=now() WHERE id=%s",('后台中断：'+research.safe_error(exc),job_id))
        finally:guard.execute('SELECT pg_advisory_unlock(%s)',(research.WORKER_LOCK,));guard.commit()


def coordinate(job_id):
    with connect() as c:
        row=c.execute("UPDATE research_job SET status='running',started_at=coalesce(started_at,now()),heartbeat_at=now() WHERE id=%s AND status='queued' RETURNING id",(job_id,)).fetchone()
    if not row:return
    pause=False;blocked=0;reason=None
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures={}
        while True:
            with connect() as c:
                job=c.execute('SELECT * FROM research_job WHERE id=%s',(job_id,)).fetchone()
                pause=pause or job['pause_requested'] or job['status']!='running'
                c.execute('UPDATE research_job SET heartbeat_at=now() WHERE id=%s',(job_id,))
                targets=[]
                if not pause:
                    targets=c.execute("SELECT symbol FROM research_item WHERE job_id=%s AND status='pending' ORDER BY ordinal LIMIT %s",(job_id,2-len(futures))).fetchall()
                    for target in targets:c.execute("UPDATE research_item SET status='running',attempts=attempts+1,started_at=now(),finished_at=NULL WHERE job_id=%s AND symbol=%s",(job_id,target['symbol']))
            for target in targets:futures[executor.submit(research.collect_stock,job_id,target['symbol'])]=target['symbol']
            if not futures:break
            done,_=wait(futures,timeout=2,return_when=FIRST_COMPLETED)
            for future in done:
                symbol=futures.pop(future)
                try:result=future.result()
                except Exception as exc:result={'status':'failed','documents':0,'ready':0,'error':research.safe_error(exc)}
                error=result['error'] or ''
                blocked=blocked+1 if any(x in error for x in ('HTTP 403','HTTP 429','来源连接失败')) else 0
                if '模型HTTP 401' in error or '模型HTTP 403' in error or blocked>=5:
                    pause=True;reason='来源或模型连续受限，已保留进度，请稍后继续'
                    with connect() as c:c.execute('UPDATE research_job SET pause_requested=true WHERE id=%s',(job_id,))
                with connect() as c:c.execute('''UPDATE research_item SET status=%s,documents=%s,ready=%s,error=%s,finished_at=%s
                  WHERE job_id=%s AND symbol=%s''',(result['status'],result['documents'],result['ready'],result['error'],None if result['status']=='pending' else datetime.now(timezone.utc),job_id,symbol))
                logging.info('%s %s documents=%s ready=%s',symbol,result['status'],result['documents'],result['ready'])
    with connect() as c:
        pending=c.execute("SELECT count(*) AS n FROM research_item WHERE job_id=%s AND status IN ('pending','running')",(job_id,)).fetchone()['n']
        failed=c.execute("SELECT count(*) AS n FROM research_item WHERE job_id=%s AND status='failed'",(job_id,)).fetchone()['n']
        state='paused' if pause or pending else 'partial' if failed else 'completed'
        c.execute('UPDATE research_job SET status=%s,message=%s,heartbeat_at=now(),finished_at=%s WHERE id=%s',
          (state,reason or ('已暂停，可继续' if state=='paused' else '采集完成'),None if state=='paused' else datetime.now(timezone.utc),job_id))


if __name__=='__main__':
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    run(sys.argv[1])
