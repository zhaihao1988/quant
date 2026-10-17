import logging
import hashlib
from datetime import date
from pathlib import Path
from contextlib import asynccontextmanager
from uuid import UUID
from fastapi import FastAPI, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field
from . import services
from .domain import ScreenRequest
from .ingest import reserve_run,run_import
from .db import connect

@asynccontextmanager
async def lifespan(app):
    recover_workflow_state()
    yield


app=FastAPI(title='Quant 本地选股工作台',version='0.1.0',lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','testserver'])
static=Path(__file__).parent/'static'
app.mount('/static',StaticFiles(directory=static),name='static')


def recover_workflow_state():
    from .multi_jobs import _active
    from .market_prepare import status
    with connect() as conn:_active(conn)
    status()


@app.middleware('http')
async def same_origin_mutations(request: Request,call_next):
    origin=request.headers.get('origin')
    if request.method not in ('GET','HEAD','OPTIONS') and origin:
        from urllib.parse import urlsplit
        if urlsplit(origin).netloc!=request.headers.get('host'):
            return JSONResponse({'detail':'仅允许本地同源调用'},status_code=403)
    return await call_next(request)


@app.exception_handler(ValueError)
async def value_error(request,exc):
    return JSONResponse({'detail':str(exc)},status_code=400)


@app.exception_handler(Exception)
async def unexpected_error(request,exc):
    logging.getLogger(__name__).exception('Request failed')
    return JSONResponse({'detail':'服务异常，请检查本地日志与数据库状态'},status_code=500)


@app.get('/')
def home():
    html=(static/'index.html').read_text(encoding='utf-8')
    for name in ('style.css','app.js','sync.js','market.js','multiscreener.js'):
        version=hashlib.sha256((static/name).read_bytes()).hexdigest()[:12]
        html=html.replace(f'/static/{name}"',f'/static/{name}?v={version}"')
    return HTMLResponse(html,headers={'Cache-Control':'no-cache'})


@app.get('/api/health')
def health():
    with connect() as conn:conn.execute('SELECT 1')
    return {'ok':True}


@app.get('/api/status')
def get_status():return services.status()


@app.get('/api/fundamentals/{symbol}')
def get_fundamentals(symbol:str,as_of:date|None=None):
    from .fundamentals import metrics_many
    return metrics_many([symbol],as_of or date.today())[symbol]


@app.get('/api/fundamental-refresh')
def fundamental_refresh_status():
    from .fundamental_refresh import status
    return status()


@app.post('/api/fundamental-refresh',status_code=202)
def fundamental_refresh_start(tasks:BackgroundTasks):
    from .fundamental_refresh import reserve,run_refresh
    run_id,reused=reserve()
    if not reused:tasks.add_task(run_refresh,run_id)
    return {'id':run_id,'reused':reused}


@app.get('/api/securities')
def search(q:str='',limit:int=Query(30,ge=1,le=100)):return services.securities(q,limit)


@app.get('/api/bars/{symbol}')
def get_bars(symbol:str,end:date|None=None,start:date|None=None,limit:int=Query(500,ge=1,le=20000),adjustment:str='qfq'):
    return services.bars(symbol,end,limit,adjustment,start)


@app.get('/api/market/{symbol}')
def market(symbol:str,end:date|None=None,start:date|None=None,limit:int=Query(20000,ge=1,le=20000),
           adjustment:str='qfq',timeframe:str='daily',ma:str='5,10,20,30,60,120,250',fast:int=12,slow:int=26,signal:int=9):
    from .charting import market_data
    try:periods=[int(v.strip()) for v in ma.split(',') if v.strip()]
    except ValueError:raise ValueError('均线参数需为逗号分隔的整数')
    return market_data(symbol,end,start,limit,adjustment,timeframe,periods,fast,slow,signal)


class ImportRequest(BaseModel):
    symbols:list[str]=Field(default_factory=list,max_length=10000)
    mode:str='incremental'


@app.post('/api/imports',status_code=202)
def start_import(body:ImportRequest,tasks:BackgroundTasks):
    run_id,reused=reserve_run(body.symbols,body.mode)
    if not reused:tasks.add_task(run_import,run_id,body.symbols,body.mode)
    return {'id':run_id,'status':'existing' if reused else 'queued','reused':reused}


@app.post('/api/imports/{run_id}/retry',status_code=202)
def retry_import(run_id:UUID,tasks:BackgroundTasks):
    with connect() as conn:
        old=conn.execute('SELECT * FROM import_run WHERE id=%s',(run_id,)).fetchone()
        failed=conn.execute("SELECT symbol FROM import_item WHERE run_id=%s AND state='failed'",(run_id,)).fetchall()
    if not old:raise HTTPException(404,'任务不存在')
    if old['status'] in ('queued','running'):raise HTTPException(409,'任务仍在运行')
    symbols=[r['symbol'] for r in failed] or old['symbols']
    new_id,reused=reserve_run(symbols,old['mode'],str(run_id))
    if not reused:tasks.add_task(run_import,new_id,symbols,old['mode'])
    return {'id':new_id,'reused':reused,'status':'existing' if reused else 'queued'}


@app.get('/api/sync/status')
def sync_status(state:str='',q:str='',offset:int=Query(0,ge=0),limit:int=Query(30,ge=1,le=100)):
    from .sync_status import comparison
    return comparison(state,q,offset,limit)


@app.post('/api/sync/scan',status_code=202)
def scan_sync(tasks:BackgroundTasks):
    from .sync_status import scan_sources
    tasks.add_task(scan_sources)
    return {'status':'scanning','scope':'A股'}


@app.get('/api/imports/{run_id}')
def get_import(run_id:UUID):
    with connect() as conn:
        row=conn.execute('SELECT * FROM import_run WHERE id=%s',(run_id,)).fetchone()
    if not row:raise HTTPException(404,'任务不存在')
    return services.clean(row)


@app.post('/api/screens')
def run_screen(body:ScreenRequest):return services.screen(body)


@app.get('/api/taxonomies')
def get_taxonomies():return services.taxonomies()


@app.get('/api/financials/status')
def financial_status():
    from .financial_service import financial_status
    return financial_status()


@app.get('/api/financials/{symbol}')
def financial_reports(symbol:str,limit:int=Query(12,ge=1,le=200),fields:str=''):
    from .financial_service import reports
    try:ids=[int(v.strip().upper().removeprefix('FN')) for v in fields.split(',') if v.strip()]
    except ValueError:raise ValueError('财务字段须为逗号分隔的FN编号')
    return reports(symbol,limit,ids)


from .multiscreener import MultiScreenRequest, strategy_catalog


@app.get('/api/multi-screens/strategies')
def multi_strategies():return strategy_catalog()


class MarketPrepareRequest(BaseModel):
    screen:MultiScreenRequest|None=None
    force:bool=False


@app.post('/api/market-prepare',status_code=202)
def prepare_market(body:MarketPrepareRequest,tasks:BackgroundTasks):
    from .market_prepare import reserve,run
    job_id,reused=reserve(body.screen.model_dump(mode='json') if body.screen else None,body.force)
    if not reused:tasks.add_task(run,job_id)
    return {'id':job_id,'reused':reused}


@app.get('/api/market-prepare')
def market_prepare_recent():
    from .market_prepare import status
    return status()


@app.get('/api/market-prepare/{job_id}')
def market_prepare_status(job_id:UUID):
    from .market_prepare import status
    result=status(job_id)
    if not result:raise HTTPException(404,'行情准备任务不存在')
    return result


@app.post('/api/market-prepare/{job_id}/cancel')
def cancel_market_prepare(job_id:UUID):
    from .market_prepare import cancel
    return cancel(job_id)


@app.get('/api/multi-screens/history')
def multi_history(limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0),day:date|None=None,state:str='',scope:str=''):
    from .multi_jobs import history
    return history(limit,offset,day,state,scope)


@app.get('/api/multi-screens/plans')
def multi_plans():
    with connect() as conn:
        rows=conn.execute('SELECT DISTINCT ON(scope_signature) scope_signature,parameters,effective_date FROM multi_screen_run WHERE scope_signature IS NOT NULL ORDER BY scope_signature,started_at DESC').fetchall()
    return services.clean(rows)


@app.get('/api/multi-screens/latest')
def multi_latest():
    from .multi_jobs import latest_session
    return latest_session()


@app.get('/api/multi-screens')
def multi_recent():
    from .multi_jobs import recent
    return recent()


@app.post('/api/multi-screens',status_code=202)
def multi_start(body:MultiScreenRequest,tasks:BackgroundTasks,force:bool=False):
    from .multi_jobs import reserve,run_job
    run_id,reused=reserve(body,reuse_completed=True,force=force)
    if not reused:tasks.add_task(run_job,run_id)
    return {'id':run_id,'reused':reused}


@app.get('/api/multi-screens/{run_id}')
def multi_status(run_id:UUID,view:str='latest',revision:int|None=Query(None,ge=1)):
    from .multi_jobs import snapshot
    return snapshot(run_id,view,archive_revision=revision)


@app.get('/api/multi-screens/{run_id}/export')
def multi_export(run_id:UUID,format:str='csv',view:str='archive',revision:int|None=Query(None,ge=1)):
    from .multi_jobs import snapshot
    from .screen_records import csv_export
    if format not in ('csv','json'):raise ValueError('导出格式须为csv或json')
    payload=snapshot(run_id,view,archive_revision=revision)
    filename=f'quant-{payload["effective_date"]}-{str(run_id)[:8]}-{view}.{format}'
    headers={'Content-Disposition':f'attachment; filename="{filename}"'}
    if format=='csv':return Response(csv_export(payload),media_type='text/csv; charset=utf-8',headers=headers)
    return JSONResponse(payload,headers=headers)


@app.get('/api/multi-screens/{run_id}/progress')
def multi_progress(run_id:UUID):
    from .multi_jobs import task_progress
    return task_progress(run_id)


@app.get('/api/multi-screens/{run_id}/archives')
def multi_archives(run_id:UUID):
    with connect() as conn:rows=conn.execute("SELECT revision,created_at,payload->>'status' AS status,sha256 FROM screen_archive WHERE run_id=%s ORDER BY revision DESC",(run_id,)).fetchall()
    return services.clean(rows)


@app.post('/api/multi-screens/{run_id}/archive')
def multi_archive(run_id:UUID):
    from .screen_records import save_archive,export_archive
    record=save_archive(run_id);export_archive(run_id,record['revision'])
    return {'saved':True,'revision':record['revision']}


@app.post('/api/multi-screens/{run_id}/retry',status_code=202)
def multi_retry(run_id:UUID,tasks:BackgroundTasks,workers:int=Query(4,ge=1,le=4)):
    from .multi_jobs import retry_failed,run_job
    job=retry_failed(run_id,workers)
    tasks.add_task(run_job,job)
    return {'id':job}


@app.post('/api/multi-screens/{run_id}/cancel')
def multi_cancel(run_id:UUID):
    from .multi_jobs import cancel
    return cancel(run_id)


class MultiResumeRequest(BaseModel):
    workers:int=Field(default=4,ge=1,le=4)


@app.post('/api/multi-screens/{run_id}/resume',status_code=202)
def multi_resume(run_id:UUID,body:MultiResumeRequest,tasks:BackgroundTasks):
    from .multi_jobs import resume,run_job
    with connect() as conn:old=conn.execute('SELECT parameters FROM multi_screen_run WHERE id=%s',(run_id,)).fetchone()
    if old and old['parameters'].get('as_of') is None:
        from .market_prepare import inspect_sources
        from .tdx.readers import stable_bytes
        from .config import settings
        _,source=inspect_sources(require_latest=True)
        if source['counts'].get('new') or source['counts'].get('changed'):
            raise ValueError('本地行情有更新，请开始新的选股；旧任务结果保留')
        path=settings().tdx_root/'T0002/hq_cache/gbbq';_,token=stable_bytes(path)
        with connect() as conn:stored=conn.execute('SELECT sha256 FROM import_file WHERE path=%s',(str(path),)).fetchone()
        if not stored or stored['sha256']!=token:raise ValueError('权息资料有更新，请开始新的选股')
    task_id=resume(run_id,body.workers)
    tasks.add_task(run_job,task_id)
    return {'id':task_id}


@app.get('/api/graph')
def get_graph(q:str=Query(min_length=1,max_length=100)):return services.graph(q)


class NewsRequest(BaseModel):
    title:str=Field(min_length=1,max_length=500)
    content:str=Field(default='',max_length=100000)
    source_url:str|None=Field(default=None,max_length=2000)


@app.post('/api/news/analyze')
def news(body:NewsRequest):return services.analyze_news(body.title,body.content,body.source_url)
