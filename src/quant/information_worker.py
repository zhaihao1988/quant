"""Single durable information worker, restarted when the local web service starts."""
from concurrent.futures import ThreadPoolExecutor
import logging,time
from .db import connect
from . import information as info

def maintain_research():
    from . import research,company_library
    research.roll_forward_active()
    with connect() as c:
        running=c.execute("SELECT status FROM research_job ORDER BY created_at DESC LIMIT 1").fetchone()
        due=c.execute("SELECT max(created_at)<now()-interval '1 day' AS due FROM research_job").fetchone()['due']
    if running and running['status'] in ('completed','partial','failed') and due:research.start('all')
    with connect() as c:
        running=c.execute("SELECT status FROM company_collection_job ORDER BY created_at DESC LIMIT 1").fetchone()
        due=c.execute("SELECT max(created_at)<now()-interval '1 day' AS due FROM company_collection_job").fetchone()['due']
    if running and running['status'] in ('completed','partial','failed') and due:company_library.start('due')

def run():
    with connect() as guard:
        if not guard.execute('SELECT pg_try_advisory_lock(%s) AS ok',(info.WORKER_LOCK,)).fetchone()['ok']:return
        guard.commit();info.ensure_profile();last_maintenance=0
        with connect() as c:c.execute("UPDATE news_poll SET status='failed',finished_at=now(),error='后台中断，保留水位待重试' WHERE status='running'")
        with ThreadPoolExecutor(max_workers=3) as pool,ThreadPoolExecutor(max_workers=2) as indexing:
            polling={};index_jobs=[]
            while True:
                with connect() as c:
                    state=c.execute('SELECT enabled FROM information_runtime WHERE id=1').fetchone()
                    if not state['enabled']:break
                    c.execute('UPDATE information_runtime SET heartbeat_at=now(),error=NULL WHERE id=1')
                    due=c.execute('SELECT source FROM news_source_state WHERE next_at<=now()').fetchall()
                for row in due:
                    s=row['source']
                    if s not in polling:polling[s]=pool.submit(info.poll_source,s)
                for s,f in list(polling.items()):
                    if f.done():
                        try:f.result()
                        except Exception:logging.exception('source polling failed: %s',s)
                        del polling[s]
                if time.monotonic()-last_maintenance>300:
                    try:maintain_research()
                    except Exception:logging.exception('daily collection check failed')
                    last_maintenance=time.monotonic()
                for future in list(index_jobs):
                    if future.done():
                        future.result();index_jobs.remove(future)
                while len(index_jobs)<2:index_jobs.append(indexing.submit(info.index_one))
                time.sleep(2)
        guard.execute('SELECT pg_advisory_unlock(%s)',(info.WORKER_LOCK,));guard.commit()
    # A rapid pause/start may arrive while thread pools are draining. Honor it.
    with connect() as c:restart=c.execute('SELECT enabled FROM information_runtime WHERE id=1').fetchone()['enabled']
    if restart:info.launch()

if __name__=='__main__':
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(message)s')
    try:run()
    except Exception as exc:
        from .research import safe_error
        with connect() as c:c.execute('UPDATE information_runtime SET error=%s WHERE id=1',(safe_error(exc),))
        logging.error('information worker stopped: %s',safe_error(exc))
