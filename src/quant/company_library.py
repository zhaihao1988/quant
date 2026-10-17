"""A-stock library and durable, separately hosted document collection jobs."""
from datetime import datetime,timezone,timedelta
import os,subprocess,sys
from uuid import uuid4
from .db import connect
from .config import ROOT
from .services import clean

RESERVE_LOCK=779953
WORKER_LOCK=779952

# Current documents are immutable revisions; counts are by report period, not revision.
STOCKS='''WITH documents AS (
 SELECT d.symbol,count(DISTINCT d.report_key) FILTER(WHERE NOT d.is_excerpt) AS report_count,
 max(d.report_date) AS report_date,max(p.collected_at) AS collected_at
 FROM company_document d JOIN company_page_revision p ON p.id=d.page_id
 WHERE d.kind='discussion' GROUP BY d.symbol
), stocks AS (
 SELECT s.symbol,s.name,s.market,coalesce(d.report_count,0) AS report_count,d.report_date,d.collected_at,
 last.status AS last_status,last.error,last.finished_at AS checked_at,
 industry.names AS industry,
 CASE WHEN active.status IS NOT NULL THEN 'updating'
 WHEN last.status='failed' THEN 'failed'
 WHEN coalesce(d.report_count,0)>0 THEN 'stored'
 WHEN last.status='no_content' THEN 'no_content' ELSE 'pending' END AS state,
 CASE WHEN coalesce(d.report_count,0)>0 AND greatest(d.collected_at,CASE WHEN last.status IN ('saved','cached') THEN last.finished_at END)>now()-interval '7 days' THEN false
 WHEN last.status='no_content' AND last.finished_at>now()-interval '7 days' THEN false
 ELSE true END AS due
 FROM security s LEFT JOIN documents d ON d.symbol=s.symbol
 LEFT JOIN LATERAL(SELECT i.status,i.error,i.finished_at FROM company_collection_item i WHERE i.symbol=s.symbol AND i.finished_at IS NOT NULL AND i.status IN ('saved','no_content','failed') ORDER BY i.finished_at DESC LIMIT 1) last ON true
 LEFT JOIN LATERAL(SELECT i.status FROM company_collection_item i JOIN company_collection_job j ON j.id=i.job_id
 WHERE i.symbol=s.symbol AND i.status IN ('pending','running') AND j.status IN ('queued','running') LIMIT 1) active ON true
 LEFT JOIN LATERAL(SELECT string_agg(DISTINCT t.name,'、' ORDER BY t.name) AS names FROM membership m JOIN taxonomy t ON t.id=m.taxonomy_id
 WHERE m.symbol=s.symbol AND t.kind='industry' AND m.snapshot_date=(SELECT max(m2.snapshot_date) FROM membership m2 JOIN taxonomy t2 ON t2.id=m2.taxonomy_id WHERE m2.symbol=s.symbol AND t2.kind='industry')) industry ON true
 WHERE s.kind='stock' AND s.market IN ('SH','SZ','BJ')
) '''


def recover(conn):
    conn.execute("UPDATE company_collection_job SET status='paused',message='后台任务已中断，可继续补齐' WHERE status IN ('running','queued') AND coalesce(heartbeat_at,created_at)<now()-interval '2 minutes'")


def job_status(job_id=None,connection=None):
    from contextlib import nullcontext
    with (nullcontext(connection) if connection is not None else connect()) as c:
        recover(c)
        row=c.execute('SELECT * FROM company_collection_job '+('WHERE id=%s ' if job_id else '')+'ORDER BY created_at DESC LIMIT 1',(job_id,) if job_id else ()).fetchone()
        if not row:return None
        counts=c.execute('SELECT status,count(*) AS n FROM company_collection_item WHERE job_id=%s GROUP BY status',(row['id'],)).fetchall()
        row['counts']={r['status']:r['n'] for r in counts}
        row['processed']=sum(row['counts'].get(k,0) for k in ('saved','cached','no_content','failed'))
        row['current']=c.execute("SELECT symbol FROM company_collection_item WHERE job_id=%s AND status='running' ORDER BY symbol",(row['id'],)).fetchall()
        row['errors']=c.execute("SELECT i.symbol,s.name,i.status,i.error FROM company_collection_item i JOIN security s ON s.symbol=i.symbol WHERE job_id=%s AND i.status IN ('failed','no_content') ORDER BY i.symbol LIMIT 50",(row['id'],)).fetchall()
    return clean(row)


def library(query='',state='',industry='',offset=0,limit=50):
    if state not in ('','pending','stored','failed','no_content','updating'):raise ValueError('未知资料状态')
    if not 0<=offset or not 1<=limit<=100:raise ValueError('分页范围不正确')
    where="WHERE (symbol ILIKE %s OR name ILIKE %s) AND (%s='' OR state=%s) AND (%s='' OR industry LIKE %s)"
    args=(f'%{query}%',f'%{query}%',state,state,industry,f'%{industry}%')
    with connect() as c:
        recover(c)
        summary=c.execute(STOCKS+'''SELECT count(*) AS total,count(*) FILTER(WHERE report_count>0) AS stored,
          count(*) FILTER(WHERE due) AS due,count(*) FILTER(WHERE state='failed') AS failed,
          count(*) FILTER(WHERE state='no_content') AS no_content,sum(report_count) AS reports FROM stocks''').fetchone()
        total=c.execute(STOCKS+'SELECT count(*) AS n FROM stocks '+where,args).fetchone()['n']
        rows=c.execute(STOCKS+'SELECT * FROM stocks '+where+' ORDER BY symbol LIMIT %s OFFSET %s',(*args,limit,offset)).fetchall()
        industries=c.execute("SELECT DISTINCT name FROM taxonomy WHERE kind='industry' ORDER BY name").fetchall()
        job=job_status(connection=c)
    return clean({'summary':summary,'total':total,'offset':offset,'limit':limit,'stocks':rows,'industries':[r['name'] for r in industries],'job':job})


def launch_worker(job_id):
    folder=ROOT/'.runtime';folder.mkdir(parents=True,exist_ok=True)
    with (folder/f'company-library-{job_id}.log').open('ab') as log:
        subprocess.Popen([sys.executable,'-m','quant.company_library_worker',str(job_id)],cwd=ROOT,
          stdout=log,stderr=log,stdin=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,
          start_new_session=os.name!='nt')


def start(mode='due'):
    if mode not in ('due','retry'):raise ValueError('未知更新方式')
    with connect() as c:
        c.execute('SELECT pg_advisory_xact_lock(%s)',(RESERVE_LOCK,));recover(c)
        active=c.execute("SELECT id FROM company_collection_job WHERE status IN ('queued','running') ORDER BY created_at DESC LIMIT 1").fetchone()
        if active:return str(active['id']),True
        targets=c.execute(STOCKS+"SELECT symbol,due,report_count,last_status FROM stocks "+("WHERE state='failed'" if mode=='retry' else '')+' ORDER BY symbol').fetchall()
        if not targets:raise ValueError('没有需要重试的股票' if mode=='retry' else '本地尚无A股股票')
        job=uuid4()
        c.execute("INSERT INTO company_collection_job(id,status,mode,total,heartbeat_at) VALUES (%s,'queued',%s,%s,now())",(job,mode,len(targets)))
        with c.cursor() as cursor:
            cursor.executemany('''INSERT INTO company_collection_item(job_id,symbol,status,report_count,finished_at)
              VALUES (%s,%s,%s,%s,%s)''',[(job,r['symbol'],'pending' if r['due'] or mode=='retry' else 'cached',r['report_count'],
              None if r['due'] or mode=='retry' else datetime.now(timezone.utc)) for r in targets])
    try:launch_worker(job)
    except Exception:
        with connect() as c:c.execute("UPDATE company_collection_job SET status='failed',message='后台进程未能启动，请查看日志',finished_at=now() WHERE id=%s",(job,))
        raise
    return str(job),False


def pause(job_id):
    with connect() as c:
        row=c.execute("UPDATE company_collection_job SET pause_requested=true,message='正在暂停，已入库资料保留' WHERE id=%s AND status IN ('queued','running') RETURNING id",(job_id,)).fetchone()
    if not row:raise ValueError('任务当前不在运行')


def resume(job_id):
    with connect() as c:
        c.execute('SELECT pg_advisory_xact_lock(%s)',(RESERVE_LOCK,));recover(c)
        row=c.execute('SELECT * FROM company_collection_job WHERE id=%s',(job_id,)).fetchone()
        if not row or row['status']!='paused':raise ValueError('只能继续已暂停的任务')
        if c.execute("SELECT 1 FROM company_collection_job WHERE status IN ('queued','running')").fetchone():raise ValueError('已有资料采集任务运行中')
        c.execute("UPDATE company_collection_item SET status='pending',started_at=NULL WHERE job_id=%s AND status IN ('running','failed')",(job_id,))
        c.execute("UPDATE company_collection_job SET status='queued',pause_requested=false,heartbeat_at=now(),finished_at=NULL,message=NULL WHERE id=%s",(job_id,))
    try:launch_worker(job_id)
    except Exception:
        with connect() as c:c.execute("UPDATE company_collection_job SET status='paused',message='后台进程未能启动，可再次继续' WHERE id=%s",(job_id,))
        raise
    return job_status(job_id)
