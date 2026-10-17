"""Durable research documents, revisions, and resumable all-stock collection."""
from datetime import datetime,timezone
from hashlib import sha256
import os,subprocess,sys
from uuid import uuid4
from dateutil.relativedelta import relativedelta
from psycopg.types.json import Jsonb
from .config import ROOT,settings
from .db import connect
from .services import clean
from .company_documents import validate_symbol
from . import research_sources as source

RESERVE_LOCK=779955
WORKER_LOCK=779954
DOCUMENT_LOCK=779956


def window(today=None):
    from zoneinfo import ZoneInfo
    end=today or datetime.now(ZoneInfo('Asia/Shanghai')).date()
    return end-relativedelta(years=1),end


def recover(c):
    c.execute("UPDATE research_job SET status='paused',message='后台任务中断，可继续已保存进度' WHERE status IN ('queued','running') AND heartbeat_at<now()-interval '5 minutes'")


def status(job_id=None):
    with connect() as c:
        recover(c)
        row=c.execute('SELECT * FROM research_job '+('WHERE id=%s ' if job_id else '')+'ORDER BY created_at DESC LIMIT 1',(job_id,) if job_id else ()).fetchone()
        start,end=window()
        summary=c.execute('''SELECT count(DISTINCT d.id) AS documents,count(DISTINCT rs.symbol) AS stocks,
          count(DISTINCT d.id) FILTER(WHERE r.status='ready') AS ready,count(DISTINCT d.id) FILTER(WHERE r.status='no_qa') AS no_qa,
          count(DISTINCT d.id) FILTER(WHERE r.status='needs_ocr') AS needs_ocr,count(DISTINCT d.id) FILTER(WHERE r.status='failed') AS failed
          FROM research_document d LEFT JOIN LATERAL(SELECT status FROM research_revision WHERE document_id=d.id ORDER BY id DESC LIMIT 1) r ON true
          LEFT JOIN research_security rs ON rs.document_id=d.id WHERE (d.published_at AT TIME ZONE 'Asia/Shanghai')::date BETWEEN %s AND %s''',(start,end)).fetchone()
        if row:
            row['counts']={r['status']:r['n'] for r in c.execute('SELECT status,count(*) AS n FROM research_item WHERE job_id=%s GROUP BY status',(row['id'],)).fetchall()}
            row['processed']=sum(row['counts'].get(s,0) for s in ('saved','no_records','failed'))
            row['current']=[r['symbol'] for r in c.execute("SELECT symbol FROM research_item WHERE job_id=%s AND status='running' ORDER BY ordinal",(row['id'],)).fetchall()]
            row['errors']=c.execute("SELECT i.symbol,s.name,i.error FROM research_item i JOIN security s USING(symbol) WHERE job_id=%s AND status='failed' ORDER BY ordinal LIMIT 30",(row['id'],)).fetchall()
    return clean({'summary':summary,'job':row,'start_date':start,'end_date':end})


def launch(job_id):
    folder=ROOT/'.runtime';folder.mkdir(exist_ok=True)
    with (folder/f'research-{job_id}.log').open('ab') as log:
        subprocess.Popen([sys.executable,'-m','quant.research_worker',str(job_id)],cwd=ROOT,
          stdout=log,stderr=log,stdin=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,start_new_session=os.name!='nt')


def roll_forward_active(connection=None):
    """Advance a long initial backfill across midnight; revisit completed stocks incrementally."""
    from contextlib import nullcontext
    begin,end=window()
    with (nullcontext(connection) if connection is not None else connect()) as c:
        c.execute('SELECT pg_advisory_xact_lock(%s)',(RESERVE_LOCK,))
        job=c.execute("SELECT id FROM research_job WHERE status IN ('queued','running') ORDER BY created_at DESC LIMIT 1 FOR UPDATE").fetchone()
        if not job:return
        c.execute('UPDATE research_job SET start_date=%s,end_date=%s WHERE id=%s',(begin,end,job['id']))
        c.execute('''INSERT INTO research_item(job_id,symbol,ordinal,status)
          SELECT %s,s.symbol,coalesce((SELECT max(ordinal) FROM research_item WHERE job_id=%s),-1)+row_number() OVER(ORDER BY s.symbol),'pending'
          FROM security s WHERE s.kind='stock' AND s.market IN ('SH','SZ','BJ') AND NOT EXISTS
          (SELECT 1 FROM research_item i WHERE i.job_id=%s AND i.symbol=s.symbol) ON CONFLICT DO NOTHING''',(job['id'],job['id'],job['id']))
        c.execute('UPDATE research_job SET total=(SELECT count(*) FROM research_item WHERE job_id=%s) WHERE id=%s',(job['id'],job['id']))
        c.execute('''UPDATE research_item i SET status='pending',started_at=NULL,finished_at=NULL
          WHERE i.job_id=%s AND i.status IN ('saved','no_records') AND NOT EXISTS
          (SELECT 1 FROM research_checkpoint p WHERE p.symbol=i.symbol AND p.checked_through>=%s)''',(job['id'],end))


def start(mode='all'):
    if mode not in ('all','retry'):raise ValueError('未知调研更新方式')
    settings()
    if not os.getenv('QUANT_SILICONFLOW_API_KEY'):raise ValueError('尚未配置硅基流动Key')
    with connect() as c:
        c.execute('SELECT pg_advisory_xact_lock(%s)',(RESERVE_LOCK,));recover(c)
        active=c.execute("SELECT id FROM research_job WHERE status IN ('queued','running') LIMIT 1").fetchone()
        if active:
            roll_forward_active(c)
            return str(active['id'])
        if mode=='retry':
            stocks=c.execute('''SELECT DISTINCT s.symbol FROM security s JOIN research_item i ON s.symbol=i.symbol
              WHERE i.job_id=(SELECT id FROM research_job ORDER BY created_at DESC LIMIT 1) AND i.status='failed' ORDER BY s.symbol''').fetchall()
        else:
            stocks=c.execute("""SELECT symbol FROM security WHERE kind='stock' AND market IN ('SH','SZ','BJ')
              ORDER BY CASE WHEN symbol='300138.SZ' THEN 0 WHEN symbol IN
              (SELECT symbol FROM multi_screen_item WHERE run_id=(SELECT r.id FROM multi_screen_run r
               WHERE EXISTS(SELECT 1 FROM multi_screen_item h WHERE h.run_id=r.id AND jsonb_array_length(coalesce(h.result->'signals','[]'::jsonb))>0)
               ORDER BY r.effective_date DESC,r.started_at DESC NULLS LAST LIMIT 1)
               AND jsonb_array_length(coalesce(result->'signals','[]'::jsonb))>0) THEN 1 ELSE 2 END,symbol""").fetchall()
        if not stocks:raise ValueError('没有待采集的股票')
        job_id=uuid4();begin,end=window()
        c.execute("INSERT INTO research_job(id,status,start_date,end_date,total) VALUES (%s,'queued',%s,%s,%s)",(job_id,begin,end,len(stocks)))
        with c.cursor() as cursor:cursor.executemany("INSERT INTO research_item(job_id,symbol,ordinal,status) VALUES (%s,%s,%s,'pending')",[(job_id,r['symbol'],i) for i,r in enumerate(stocks)])
    try:launch(job_id)
    except Exception:
        with connect() as c:c.execute("UPDATE research_job SET status='paused',message='后台启动失败，可继续' WHERE id=%s",(job_id,))
        raise
    return str(job_id)


def pause(job_id):
    with connect() as c:
        row=c.execute("UPDATE research_job SET pause_requested=true,message='正在暂停，完成当前文件后保留进度' WHERE id=%s AND status IN ('queued','running') RETURNING id",(job_id,)).fetchone()
    if not row:raise ValueError('任务不在运行')


def resume(job_id):
    with connect() as c:
        c.execute('SELECT pg_advisory_xact_lock(%s)',(RESERVE_LOCK,));recover(c)
        if c.execute("SELECT 1 FROM research_job WHERE status IN ('queued','running')").fetchone():raise ValueError('已有调研任务运行')
        row=c.execute("UPDATE research_job SET status='queued',pause_requested=false,heartbeat_at=now(),finished_at=NULL,message=NULL WHERE id=%s AND status='paused' RETURNING id",(job_id,)).fetchone()
        if not row:raise ValueError('只能继续已暂停的任务')
        c.execute("UPDATE research_item SET status='pending',started_at=NULL,finished_at=NULL WHERE job_id=%s AND status IN ('running','failed')",(job_id,))
    launch(job_id)


def save_metadata(symbol,record):
    fingerprint=sha256(__import__('json').dumps(record,sort_keys=True,default=str,ensure_ascii=False).encode()).hexdigest()
    with connect() as c:
        row=c.execute('''INSERT INTO research_document(source_id,title,published_at,source_url,pdf_url,metadata_sha256)
          VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(source,source_id) DO UPDATE SET last_seen_at=now(),
          title=excluded.title,published_at=excluded.published_at,source_url=excluded.source_url,pdf_url=excluded.pdf_url,
          updated_at=CASE WHEN research_document.metadata_sha256 IS DISTINCT FROM excluded.metadata_sha256 THEN now() ELSE research_document.updated_at END,
          metadata_sha256=excluded.metadata_sha256 RETURNING *''',
          (record['source_id'],record['title'],record['published_at'],record['source_url'],record['pdf_url'],fingerprint)).fetchone()
        c.execute('INSERT INTO research_security(document_id,symbol) VALUES (%s,%s) ON CONFLICT DO NOTHING',(row['id'],symbol))
        c.execute('INSERT INTO research_metadata_revision(document_id,sha256,metadata) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',
          (row['id'],fingerprint,Jsonb(__import__('json').loads(__import__('json').dumps(record,default=str)))))
    return row


def process_document(document):
    with connect() as guard:
        guard.execute('SELECT pg_advisory_lock(%s,%s)',(DOCUMENT_LOCK,document['id']));guard.commit()
        try:
            document=guard.execute('SELECT * FROM research_document WHERE id=%s',(document['id'],)).fetchone();guard.commit()
            return _process_document(document)
        finally:guard.execute('SELECT pg_advisory_unlock(%s,%s)',(DOCUMENT_LOCK,document['id']));guard.commit()


def _process_document(document):
    with connect() as c:
        old=c.execute('SELECT * FROM research_revision WHERE document_id=%s AND parser_version=%s AND model=%s ORDER BY id DESC LIMIT 1',
          (document['id'],source.PARSER_VERSION,source.MODEL)).fetchone()
    checked=document.get('content_checked_at')
    recheck=not checked or (datetime.now(timezone.utc)-checked).days>=30 or document.get('content_url')!=document['pdf_url']
    if old and not recheck and old['status'] in ('ready','no_qa','needs_ocr'):return old['status']
    if not old:
        with connect() as c:old=c.execute('SELECT * FROM research_revision WHERE document_id=%s ORDER BY id DESC LIMIT 1',(document['id'],)).fetchone()
    body=source.download_pdf(document['pdf_url']) if recheck or not old else bytes(old['body'])
    with connect() as c:c.execute('UPDATE research_document SET content_checked_at=now(),content_url=%s WHERE id=%s',(document['pdf_url'],document['id']))
    if old and sha256(body).hexdigest()==old['sha256'] and old['parser_version']==source.PARSER_VERSION and old['model']==source.MODEL and old['status'] in ('ready','no_qa','needs_ocr'):
        return old['status']
    digest=sha256(body).hexdigest()
    with connect() as c:
        row=c.execute('''INSERT INTO research_revision(document_id,sha256,body,pages,parser_version,model,status)
          VALUES (%s,%s,%s,'[]',%s,%s,'pending') ON CONFLICT(document_id,sha256,parser_version,model)
          DO UPDATE SET status='pending',error=NULL RETURNING id''',
          (document['id'],digest,body,source.PARSER_VERSION,source.MODEL)).fetchone()
    revision_id=row['id']
    try:
        pages=old['pages'] if old and old['sha256']==digest and old['pages'] else source.pdf_pages(body)
        with connect() as c:c.execute('UPDATE research_revision SET pages=%s WHERE id=%s',(Jsonb(pages),revision_id))
        if sum(len(p['text'].strip()) for p in pages)<80:
            state='needs_ocr';extraction={}
        else:
            extraction=source.extract(pages);state='ready' if extraction['qa_pairs'] else 'no_qa'
            if state=='no_qa' and __import__('re').search(r'(?:问[题：:]|Q\s*\d|答[：:])','\n'.join(p['text'] for p in pages)):
                raise ValueError('原文疑似含问答但模型未提取，请核对原文')
        with connect() as c:c.execute('UPDATE research_revision SET status=%s,extraction=%s,error=NULL,parsed_at=now() WHERE id=%s',(state,Jsonb(extraction),revision_id))
        return state
    except Exception as exc:
        message=safe_error(exc)
        with connect() as c:c.execute("UPDATE research_revision SET status='failed',error=%s,extraction=%s WHERE id=%s",(message,Jsonb({'unverified_output':getattr(exc,'model_output',{})}),revision_id))
        raise ValueError(message) from None


def safe_error(exc):
    key=os.getenv('QUANT_SILICONFLOW_API_KEY','')
    text=str(exc).replace(key,'[REDACTED]') if key else str(exc)
    return text[:500]


def collect_stock(job_id,symbol):
    with connect() as c:job=c.execute('SELECT * FROM research_job WHERE id=%s',(job_id,)).fetchone()
    with connect() as c:checkpoint=c.execute('SELECT * FROM research_checkpoint WHERE symbol=%s',(symbol,)).fetchone()
    begin=job['start_date'];full=True
    if checkpoint and checkpoint['checked_through'] and checkpoint['full_checked_at'] and (datetime.now(timezone.utc)-checkpoint['full_checked_at']).days<7:
        begin=max(begin,checkpoint['checked_through']-__import__('datetime').timedelta(days=7));full=False
    records=source.fetch_announcements(symbol,begin,job['end_date'])
    def checkpoint_success():
        with connect() as c:c.execute('''INSERT INTO research_checkpoint(symbol,checked_through,full_checked_at) VALUES(%s,%s,CASE WHEN %s THEN now() END)
          ON CONFLICT(symbol) DO UPDATE SET checked_through=greatest(research_checkpoint.checked_through,excluded.checked_through),
          full_checked_at=coalesce(excluded.full_checked_at,research_checkpoint.full_checked_at)''',(symbol,job['end_date'],full))
    if not records:
        checkpoint_success()
        return {'status':'no_records','documents':0,'ready':0,'error':None}
    failures=[];ready=0
    for record in records:
        with connect() as c:control=c.execute('SELECT pause_requested FROM research_job WHERE id=%s',(job_id,)).fetchone()
        if control['pause_requested']:return {'status':'pending','documents':len(records),'ready':ready,'error':None}
        document=save_metadata(symbol,record)
        try:
            state=process_document(document)
            if state=='ready':ready+=1
        except Exception as exc:
            failures.append(f"{record['source_id']}：{safe_error(exc)}")
            if any(x in failures[-1] for x in ('模型HTTP 401','模型HTTP 403')):break
    if not failures:checkpoint_success()
    return {'status':'failed' if failures else 'saved','documents':len(records),'ready':ready,'error':'；'.join(failures)[:1500] or None}


def documents(symbol):
    validate_symbol(symbol);begin,end=window()
    with connect() as c:
        rows=c.execute('''SELECT d.id,d.title,d.published_at,d.source_url,d.first_seen_at,r.id AS revision_id,r.status,
          r.extraction,r.error,r.model,r.collected_at,r.parsed_at
          FROM research_security s JOIN research_document d ON d.id=s.document_id
          LEFT JOIN LATERAL(SELECT * FROM research_revision WHERE document_id=d.id ORDER BY id DESC LIMIT 1) r ON true
          WHERE s.symbol=%s AND (d.published_at AT TIME ZONE 'Asia/Shanghai')::date BETWEEN %s AND %s ORDER BY d.published_at DESC,d.id DESC''',(symbol,begin,end)).fetchall()
        latest=c.execute('''SELECT i.status,i.error,i.finished_at,j.start_date,j.end_date FROM research_item i JOIN research_job j ON j.id=i.job_id
          WHERE i.symbol=%s ORDER BY j.created_at DESC LIMIT 1''',(symbol,)).fetchone()
    return clean({'symbol':symbol,'start_date':begin,'end_date':end,'documents':rows,'collection':latest})


def original(revision_id):
    with connect() as c:row=c.execute('SELECT body,pages FROM research_revision WHERE id=%s',(revision_id,)).fetchone()
    if not row:raise ValueError('原文尚未下载')
    return row
