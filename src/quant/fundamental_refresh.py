"""Resumable-by-cache local capital/financial refresh, separate from technical scans."""
from datetime import datetime,timezone
from uuid import uuid4
from psycopg.types.json import Jsonb
from .db import connect
from .services import clean
from .capital_ingest import import_capital
from .financial_ingest import import_financials
from .fundamentals import metrics_many,latest_reference_date

RESERVE_LOCK=779934
WORKER_LOCK=779935


def reserve():
    with connect() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(%s)',(RESERVE_LOCK,))
        old=conn.execute("SELECT * FROM fundamental_refresh_run WHERE status IN ('queued','running') ORDER BY started_at DESC LIMIT 1").fetchone()
        if old:
            free=conn.execute('SELECT pg_try_advisory_xact_lock(%s) AS ok',(WORKER_LOCK,)).fetchone()['ok']
            age=(datetime.now(timezone.utc)-old['heartbeat_at']).total_seconds()
            if not free or (old['status']=='queued' and age<120):return str(old['id']),True
            conn.execute("UPDATE fundamental_refresh_run SET status='interrupted',finished_at=now() WHERE id=%s",(old['id'],))
        run_id=str(uuid4())
        conn.execute("INSERT INTO fundamental_refresh_run(id,status) VALUES (%s,'queued')",(run_id,))
    return run_id,False


def status():
    with connect() as conn:
        row=conn.execute('SELECT * FROM fundamental_refresh_run ORDER BY started_at DESC LIMIT 1').fetchone()
    return clean(row) if row else None


def run_refresh(run_id):
    with connect() as lock:
        if not lock.execute('SELECT pg_try_advisory_lock(%s) AS ok',(WORKER_LOCK,)).fetchone()['ok']:return
        lock.commit()
        def progress(**fields):
            with connect() as conn:
                for key,value in fields.items():
                    if key not in ('status','stage','processed','total','result','errors'):raise ValueError('不支持的进度字段')
                    conn.execute(f'UPDATE fundamental_refresh_run SET {key}=%s,heartbeat_at=now() WHERE id=%s',(Jsonb(value) if key in ('result','errors') else value,run_id))
        try:
            with connect() as conn:
                job=conn.execute('SELECT status FROM fundamental_refresh_run WHERE id=%s',(run_id,)).fetchone()
            if not job or job['status']!='queued':return
            progress(status='running',stage='读取A股股本')
            capital=import_capital()
            progress(stage='读取专业财务包',result={'capital':capital})
            financial=import_financials()
            errors=financial['errors']
            result={'capital':capital,'financials':{'id':str(financial['id']),'status':financial['status'],
                    'files':financial['total'],'new_records':financial['reports'],'skipped':financial['skipped']},
                    'reference_date':latest_reference_date().isoformat(),'mode':'latest_collected'}
            with connect() as conn:
                stocks=[r['symbol'] for r in conn.execute("SELECT symbol FROM security WHERE name<>'' ORDER BY symbol")]
            progress(stage='计算选股财务指标',total=len(stocks),result=result,errors=errors)
            coverage={key:0 for key in ('total_shares','float_shares','market_cap','pe','pb','revenue_growth_yoy','profit_growth_yoy','peg_like_ratio')}
            reference=latest_reference_date()
            for start in range(0,len(stocks),100):
                computed=metrics_many(stocks[start:start+100],reference)
                for values in computed.values():
                    for key in coverage:coverage[key]+=int(values.get(key) is not None)
                progress(processed=min(start+100,len(stocks)))
            result['coverage']=coverage
            progress(status='partial' if errors else 'completed',stage='已完成',result=result)
            with connect() as conn:conn.execute('UPDATE fundamental_refresh_run SET finished_at=now() WHERE id=%s',(run_id,))
        except Exception as exc:
            progress(status='failed',stage='更新失败',errors=[{'error':f'{type(exc).__name__}: {exc}'}])
            with connect() as conn:conn.execute('UPDATE fundamental_refresh_run SET finished_at=now() WHERE id=%s',(run_id,))
        finally:
            lock.execute('SELECT pg_advisory_unlock(%s)',(WORKER_LOCK,));lock.commit()
