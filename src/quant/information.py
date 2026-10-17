"""Durable incremental public information and atomically activated evidence index."""
from datetime import datetime,timedelta,timezone
from hashlib import sha256
import json,os,subprocess,sys
from uuid import uuid4
from psycopg.types.json import Jsonb
from .db import connect
from .config import ROOT
from .services import clean
from . import embeddings as emb,news_sources

WORKER_LOCK=779961

def digest(value):return sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()

def save_news(source,record):
    fields={k:record[k] for k in ('title','content','published_at','source_url')}
    checksum=digest(fields)
    with connect() as c:
        article=c.execute('''INSERT INTO news_article(source,source_id) VALUES (%s,%s)
         ON CONFLICT(source,source_id) DO UPDATE SET last_seen_at=now() RETURNING *''',(source,record['source_id'])).fetchone()
        revision=c.execute('''INSERT INTO news_revision(article_id,sha256,title,content,source_url,published_at,raw)
         VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(article_id,sha256) DO NOTHING RETURNING id''',
         (article['id'],checksum,record['title'],record['content'],record['source_url'],record['published_at'],Jsonb(record['raw']))).fetchone()
        if revision:c.execute('UPDATE news_article SET current_revision=%s WHERE id=%s',(revision['id'],article['id']))
    return bool(revision)

def poll_source(source,max_pages=20):
    now=datetime.now(timezone.utc)
    with connect() as c:
        state=c.execute('SELECT * FROM news_source_state WHERE source=%s',(source,)).fetchone()
        target=state['watermark']-timedelta(hours=6) if state['watermark'] else now-timedelta(days=1)
        run=c.execute('INSERT INTO news_poll(source,target) VALUES (%s,%s) RETURNING id',(source,target)).fetchone()['id']
    saved=0;oldest=newest=before=None;pages=0;seen=set();complete=False;error=None
    try:
        for page in range(1,max_pages+1):
            records=news_sources.fetch(source,page,before);pages=page
            times=[r['published_at'] for r in records]
            lo,hi=min(times),max(times)
            oldest=min(oldest,lo) if oldest else lo;newest=max(newest,hi) if newest else hi
            ids={r['source_id'] for r in records}
            for r in records:saved+=save_news(source,r)
            if hi<=target:complete=True;break
            if ids<=seen:raise ValueError('分页重复或来源不再提供更早记录')
            if before and source=='cls' and lo>=before:raise ValueError('来源游标未向历史推进')
            seen|=ids;before=lo
            __import__('time').sleep(1.2)
        if not complete:error='已保存可获取新闻；本次未覆盖重叠窗口，历史存在缺口'
    except Exception as exc:
        from .research import safe_error
        error=safe_error(exc)
    # A gap stays in news_poll. Advance only when complete. First limited snapshot
    # establishes an explicit baseline, instead of endlessly chasing unavailable history.
    baseline=not state['watermark'] and newest is not None
    with connect() as c:
        c.execute('''UPDATE news_poll SET finished_at=now(),oldest=%s,newest=%s,pages=%s,saved=%s,status=%s,error=%s WHERE id=%s''',
          (oldest,newest,pages,saved,'completed' if complete else 'gap' if newest else 'failed',error,run))
        failures=0 if complete else state['failures']+1
        c.execute('''UPDATE news_source_state SET watermark=CASE WHEN %s THEN %s ELSE watermark END,checked_at=now(),
          next_at=now()+(%s*interval '1 second'),failures=%s,error=%s,coverage=%s WHERE source=%s''',
          (complete or baseline,newest,min(3600,120*2**min(failures,5)),failures,error,
           '重叠窗口已覆盖' if complete else '首批基线，历史未补全' if baseline else '存在缺口，待重试',source))
    return dict(source=source,saved=saved,complete=complete,error=error)

def load_document(kind,key):
    with connect() as c:
        if kind=='news':
            row=c.execute('''SELECT r.id::text AS revision,r.title,r.content,r.source_url,r.published_at,
             r.collected_at AS known_at,NULL::text AS symbol FROM news_article a JOIN news_revision r ON r.id=a.current_revision WHERE a.id=%s''',(int(key),)).fetchone()
        elif kind=='company':
            symbol,category,report=key.split(':',2)
            row=c.execute('''SELECT d.id::text AS revision,d.title,d.content,p.source_url,
             d.report_date::timestamp AT TIME ZONE 'Asia/Shanghai' AS published_at,p.collected_at AS known_at,d.symbol
             FROM company_document d JOIN company_page_revision p ON p.id=d.page_id
             WHERE d.symbol=%s AND d.kind=%s AND d.report_key=%s ORDER BY p.collected_at DESC,p.id DESC LIMIT 1''',(symbol,category,report)).fetchone()
        elif kind=='research':
            row=c.execute('''SELECT r.id::text AS revision,d.title,d.source_url,d.published_at,
             greatest(r.collected_at,r.parsed_at,d.updated_at) AS known_at,r.extraction,
             (SELECT min(symbol) FROM research_security WHERE document_id=d.id) AS symbol
             FROM research_document d JOIN LATERAL(SELECT * FROM research_revision WHERE document_id=d.id ORDER BY id DESC LIMIT 1) r ON true
             WHERE d.id=%s AND r.status='ready' AND d.content_url=d.pdf_url ''',(int(key),)).fetchone()
            if row:
                row['content']='\n\n'.join('问：'+q['question']+'\n答：'+q['answer'] for q in row.pop('extraction').get('qa_pairs',[]))
        else:raise ValueError('未知资料类型')
    return row

def claim():
    lease=uuid4()
    with connect() as c:
        row=c.execute('''WITH selected AS (SELECT kind,doc_key FROM evidence_queue
          WHERE (state IN ('pending','failed') AND next_at<=now() AND attempts<6) OR (state='running' AND lease_until<now())
          ORDER BY priority,updated_at FOR UPDATE SKIP LOCKED LIMIT 1)
          UPDATE evidence_queue q SET state='running',lease=%s,lease_until=now()+interval '10 minutes',attempts=attempts+1
          FROM selected s WHERE q.kind=s.kind AND q.doc_key=s.doc_key RETURNING q.*''',(lease,)).fetchone()
    return row

def index_one():
    job=claim()
    if not job:return False
    try:
        doc=load_document(job['kind'],job['doc_key']);profile=emb.profile()
        pieces=list(emb.chunks(doc['title']+'\n'+doc['content'])) if doc else []
        if len(pieces)>2000:raise ValueError('文档过长，需核验后索引')
        hashes=[sha256(p.encode()).hexdigest() for p in pieces]
        with connect() as c:
            existing={r['sha256'] for r in c.execute('SELECT sha256 FROM embedding_cache WHERE profile=%s AND sha256=ANY(%s)',(profile,hashes)).fetchall()}
        missing=list(dict.fromkeys(h for h in hashes if h not in existing));by_hash=dict(zip(hashes,pieces))
        for start in range(0,len(missing),8):
            batch=missing[start:start+8];vectors=emb.embed([by_hash[h] for h in batch])
            with connect() as c:
                for h,v in zip(batch,vectors):c.execute('INSERT INTO embedding_cache(profile,sha256,embedding) VALUES (%s,%s,%s::public.vector) ON CONFLICT DO NOTHING',(profile,h,emb.literal(v)))
                c.execute("UPDATE evidence_queue SET lease_until=now()+interval '10 minutes' WHERE kind=%s AND doc_key=%s AND lease=%s",(job['kind'],job['doc_key'],job['lease']))
        with connect() as c:
            current=c.execute('SELECT * FROM evidence_queue WHERE kind=%s AND doc_key=%s FOR UPDATE',(job['kind'],job['doc_key'])).fetchone()
            if current['generation']!=job['generation'] or current['lease']!=job['lease']:return True
            c.execute('DELETE FROM evidence_chunk WHERE kind=%s AND doc_key=%s',(job['kind'],job['doc_key']))
            if doc:
                for i,(h,p) in enumerate(zip(hashes,pieces)):
                    c.execute('''INSERT INTO evidence_chunk(kind,doc_key,revision,ordinal,profile,sha256,symbol,title,content,source_url,published_at,known_at)
                      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                      (job['kind'],job['doc_key'],doc['revision'],i,profile,h,doc['symbol'],doc['title'],p,doc['source_url'],doc['published_at'],doc['known_at']))
            c.execute("UPDATE evidence_queue SET state='ready',error=NULL,lease=NULL,lease_until=NULL WHERE kind=%s AND doc_key=%s",(job['kind'],job['doc_key']))
    except Exception as exc:
        from .research import safe_error
        with connect() as c:c.execute("""UPDATE evidence_queue SET state='failed',error=%s,next_at=now()+(%s*interval '1 second'),lease=NULL,lease_until=NULL
          WHERE kind=%s AND doc_key=%s AND generation=%s AND lease=%s""",(safe_error(exc),min(3600,30*2**min(job['attempts'],6)),job['kind'],job['doc_key'],job['generation'],job['lease']))
    return True

def status():
    with connect() as c:
        runtime=c.execute('SELECT * FROM information_runtime WHERE id=1').fetchone()
        runtime['process_active']=c.execute("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND classid=0 AND objid=%s AND granted) AS active",(WORKER_LOCK,)).fetchone()['active']
        counts={r['state']:r['n'] for r in c.execute('SELECT state,count(*) n FROM evidence_queue GROUP BY state')}
        rows=c.execute('SELECT * FROM news_source_state ORDER BY source').fetchall()
        totals=c.execute('SELECT (SELECT count(*) FROM news_article) AS articles,(SELECT count(*) FROM evidence_chunk) AS chunks,(SELECT count(*) FROM embedding_cache) AS vectors').fetchone()
        errors=c.execute("SELECT kind,doc_key,error,attempts FROM evidence_queue WHERE state='failed' ORDER BY updated_at DESC LIMIT 15").fetchall()
        gaps=c.execute("SELECT source,started_at,target,oldest,error FROM news_poll WHERE status IN ('gap','failed') ORDER BY id DESC LIMIT 10").fetchall()
    return clean(dict(runtime=runtime,sources=rows,queue=counts,totals=totals,errors=errors,gaps=gaps))

def launch():
    folder=ROOT/'.runtime';folder.mkdir(exist_ok=True)
    with (folder/'information-worker.log').open('ab') as log:
        subprocess.Popen([sys.executable,'-m','quant.information_worker'],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=log,
          creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,start_new_session=os.name!='nt')

def control(action):
    if action not in ('start','pause','retry','update'):raise ValueError('未知更新操作')
    with connect() as c:
        c.execute('SELECT pg_advisory_xact_lock(779962)')
        c.execute('UPDATE information_runtime SET enabled=%s WHERE id=1',(action!='pause',))
        if action in ('update','retry'):c.execute('UPDATE news_source_state SET next_at=now(),failures=0')
        if action=='retry':c.execute("UPDATE evidence_queue SET state='pending',attempts=0,next_at=now(),error=NULL WHERE state='failed'")
    if action!='pause':launch()
    return status()

def ensure_profile():
    profile=emb.profile()
    with connect() as c:
        row=c.execute('SELECT index_profile FROM information_runtime WHERE id=1 FOR UPDATE').fetchone()
        if row['index_profile']!=profile:
            c.execute("UPDATE evidence_queue SET generation=generation+1,state='pending',attempts=0,next_at=now(),updated_at=now()")
            c.execute('UPDATE information_runtime SET index_profile=%s WHERE id=1',(profile,))

def feed(query='',limit=40):
    with connect() as c:
        rows=c.execute('''SELECT a.id,a.source,a.first_seen_at,r.id AS revision_id,r.title,r.content,r.source_url,r.published_at,
          (SELECT state FROM evidence_queue WHERE kind='news' AND doc_key=a.id::text) AS index_state
          FROM news_article a JOIN news_revision r ON r.id=a.current_revision
          WHERE (%s='' OR r.title ILIKE %s OR r.content ILIKE %s) ORDER BY r.published_at DESC LIMIT %s''',(query,'%'+query+'%','%'+query+'%',limit)).fetchall()
    return clean(rows)

def search(query,kind='company',symbol=None,as_of=None,limit=12):
    if kind not in ('company','research','news','all'):raise ValueError('未知资料类型')
    if not query.strip() or len(query)>1500:raise ValueError('查询长度应为1至1500字符')
    if not 1<=limit<=30:raise ValueError('检索数量超限')
    cutoff=as_of or datetime.now(timezone.utc)
    if cutoff.tzinfo is None:raise ValueError('历史检索截止时间必须包含时区')
    profile=emb.profile();query_hash=sha256((emb.INSTRUCTION+query).encode()).hexdigest()
    with connect() as c:cached=c.execute('SELECT embedding::text AS value FROM embedding_cache WHERE profile=%s AND sha256=%s',(profile,query_hash)).fetchone()
    if cached:vector=cached['value']
    else:
        vector=emb.literal(emb.embed([query],query=True)[0])
        with connect() as c:c.execute('INSERT INTO embedding_cache(profile,sha256,embedding) VALUES (%s,%s,%s::public.vector) ON CONFLICT DO NOTHING',(profile,query_hash,vector))
    with connect() as c:
        # Pending/error revisions are excluded. A stale previous index never silently
        # supplies current evidence; atomic replacement leaves it available for audit.
        rows=c.execute('''SELECT ec.*,s.name,1-(v.embedding OPERATOR(public.<=>) %s::public.vector) AS similarity,
          CASE WHEN ec.content ILIKE %s THEN 1 ELSE 0 END AS literal_match
          FROM evidence_chunk ec JOIN embedding_cache v USING(profile,sha256)
          JOIN evidence_queue q ON q.kind=ec.kind AND q.doc_key=ec.doc_key AND q.state='ready'
          LEFT JOIN security s ON s.symbol=ec.symbol WHERE ec.profile=%s AND (%s='all' OR ec.kind=%s)
          AND (%s::text IS NULL OR ec.symbol=%s) AND ec.known_at<=%s AND (ec.published_at IS NULL OR ec.published_at<=%s)
          ORDER BY literal_match DESC,v.embedding OPERATOR(public.<=>) %s::public.vector LIMIT %s''',
          (vector,'%'+query+'%',profile,kind,kind,symbol,symbol,cutoff,cutoff,vector,limit)).fetchall()
    return clean({'query':query,'as_of':cutoff,'profile':profile,'evidence':rows})
