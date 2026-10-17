"""Durable source comparison -> incremental import -> optional latest-date screen."""
from datetime import datetime,timezone
from uuid import uuid4
from psycopg.types.json import Jsonb
from .db import connect
from .config import settings
from .services import clean
from .sessions import choose_session,market_session
from .multiscreener import MultiScreenRequest
from . import ingest,multi_jobs,sync_status
from .tdx.readers import stable_bytes

RESERVE_LOCK=779936
WORKER_LOCK=779937


def reserve(parameters=None):
    if parameters is not None:
        parameters=MultiScreenRequest.model_validate(parameters).model_dump(mode='json')
        if parameters['as_of'] is not None:raise ValueError('此入口统一使用同步后的最新日期')
    with connect() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(%s)',(RESERVE_LOCK,))
        old=conn.execute("SELECT * FROM market_prepare_run WHERE status IN ('queued','running') ORDER BY started_at DESC LIMIT 1").fetchone()
        if old:
            free=conn.execute('SELECT pg_try_advisory_xact_lock(%s) AS ok',(WORKER_LOCK,)).fetchone()['ok']
            age=(datetime.now(timezone.utc)-old['heartbeat_at']).total_seconds()
            if not free or (old['status']=='queued' and age<120):
                if old['parameters']==parameters:return str(old['id']),True
                raise ValueError('已有行情核对或同步任务，请等待完成后再提交')
            conn.execute("UPDATE market_prepare_run SET status='interrupted',stage='服务中断，请重新核对',finished_at=now() WHERE id=%s",(old['id'],))
        # Avoid importing into an actively running technical scan.
        if multi_jobs._active(conn):raise ValueError('已有技术选股运行，请等待完成或先停止，再更新行情')
        job_id=str(uuid4())
        conn.execute("INSERT INTO market_prepare_run(id,status,parameters) VALUES (%s,'queued',%s)",(job_id,Jsonb(parameters) if parameters is not None else None))
    return job_id,False


def status(job_id=None):
    with connect() as conn:
        row=conn.execute('SELECT * FROM market_prepare_run WHERE id=%s',(job_id,)).fetchone() if job_id else conn.execute('SELECT * FROM market_prepare_run ORDER BY started_at DESC LIMIT 1').fetchone()
        if row and row['import_id']:
            row['import_progress']=conn.execute('SELECT status,stage,processed,total,changed_rows,skipped,errors FROM import_run WHERE id=%s',(row['import_id'],)).fetchone()
    return clean(row) if row else None


def inspect_sources():
    if not sync_status.scan_sources():raise ValueError('源文件核对未完成或已有核对运行，请稍后重试')
    with connect() as conn:
        rows=conn.execute('SELECT symbol,state,local_date,db_date,source_mtime_ns FROM source_inventory ORDER BY symbol').fetchall()
    counts={}
    for row in rows:counts[row['state']]=counts.get(row['state'],0)+1
    if counts.get('busy'):raise ValueError(f"{counts['busy']}份日线正在写入或不完整，请等待通达信下载完成后重试")
    if not rows or not any(r['local_date'] for r in rows):raise ValueError('没有可用的本地A股日线，请先完成通达信下载')
    dates={}
    for row in rows:
        if row['local_date']:dates[row['local_date']]=dates.get(row['local_date'],0)+1
    local_session=choose_session([{'trade_date':d,'securities':n} for d,n in dates.items()])
    if not local_session:raise ValueError('本地行情尚未满足盘后完整性要求，请等待下载完成')
    return rows,{'counts':counts,'local_date':max(dates).isoformat(),
                 'target_date':local_session['trade_date'].isoformat(),
                 'db_date':max((r['db_date'] for r in rows if r['db_date']),default=None),
                 'missing_files':counts.get('missing',0),'older_files':counts.get('older',0)}


def run(job_id):
    screen_id=None
    ready=False
    with connect() as worker:
        if not worker.execute('SELECT pg_try_advisory_lock(%s) AS ok',(WORKER_LOCK,)).fetchone()['ok']:return
        worker.commit()
        def update(**fields):
            allowed={'status','stage','result','error','import_id','screen_id'}
            if not fields.keys()<=allowed:raise ValueError('非法准备任务字段')
            with connect() as conn:
                for key,value in fields.items():
                    conn.execute(f'UPDATE market_prepare_run SET {key}=%s,heartbeat_at=now() WHERE id=%s',(Jsonb(clean(value)) if key=='result' else value,job_id))
        try:
            with connect() as conn:
                job=conn.execute('SELECT * FROM market_prepare_run WHERE id=%s',(job_id,)).fetchone()
            if not job or job['status']!='queued':return
            update(status='running',stage='核对本地交易日期、文件时间戳和数据库')
            rows,before=inspect_sources();update(result={'before':before})
            pending=[r['symbol'] for r in rows if r['state'] in ('new','changed')]
            # Ex-rights inputs can change independently of daily bars.
            gbbq=settings().tdx_root/'T0002/hq_cache/gbbq'
            _,digest=stable_bytes(gbbq)
            with connect() as conn:
                old=conn.execute('SELECT sha256 FROM import_file WHERE path=%s',(str(gbbq),)).fetchone()
            if not pending and (not old or old['sha256']!=digest):
                pending=[r['symbol'] for r in rows if r['state']=='aligned']
            if pending:
                import_id,reused=ingest.reserve_run(pending,'incremental')
                if reused:raise ValueError('已有行情同步运行，请等待完成后重新核对选股')
                update(import_id=import_id,stage=f'增量同步{len(pending)}份A股日线')
                ingest.run_import(import_id,pending,'incremental')
                with connect() as conn:
                    imported=conn.execute('SELECT status,errors FROM import_run WHERE id=%s',(import_id,)).fetchone()
                if imported['status']!='completed':
                    raise ValueError('行情同步未全部成功，未启动选股：'+'；'.join(imported['errors'][:3]))
            update(stage='同步后再次核对日期与文件变化')
            _,after=inspect_sources()
            if after['counts'].get('new') or after['counts'].get('changed'):
                raise ValueError('核对发现仍有未同步或同步期间变化的日线，请重新更新；未启动选股')
            with connect() as conn:session=market_session(conn)
            if not session or session['trade_date'].isoformat()!=after['target_date']:
                raise ValueError('数据库与本地可用交易日尚未对齐，未启动选股')
            result={'before':before,'after':after,'effective_date':session['trade_date'].isoformat(),
                    'synced_files':len(pending),'note':'缺少源文件及本地较旧文件保留原数据，不用旧文件覆盖；缺行情股票仍由策略报告。'}
            if job['parameters'] is not None:
                update(stage='行情已对齐，创建最新日期选股',result=result)
                screen_id,reused=multi_jobs.reserve(MultiScreenRequest.model_validate(job['parameters']))
                if reused:raise ValueError('已有选股任务运行，请查看任务记录')
                update(screen_id=screen_id)
            update(status='completed',stage='已对齐，已启动选股' if screen_id else '行情更新完成',result=result)
            with connect() as conn:conn.execute('UPDATE market_prepare_run SET finished_at=now() WHERE id=%s',(job_id,))
            ready=True
        except Exception as exc:
            update(status='failed',stage='核对或更新未完成，未启动选股',error=f'{type(exc).__name__}: {exc}')
            with connect() as conn:conn.execute('UPDATE market_prepare_run SET finished_at=now() WHERE id=%s',(job_id,))
        finally:
            worker.execute('SELECT pg_advisory_unlock(%s)',(WORKER_LOCK,));worker.commit()
    if screen_id and ready:multi_jobs.run_job(screen_id)
