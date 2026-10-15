import logging
from datetime import date
from pathlib import Path
from uuid import UUID
from fastapi import FastAPI, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field
from . import services
from .domain import ScreenRequest
from .ingest import reserve_run,run_import
from .db import connect

app=FastAPI(title='Quant 本地选股工作台',version='0.1.0')
app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','testserver'])
static=Path(__file__).parent/'static'
app.mount('/static',StaticFiles(directory=static),name='static')


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
def home():return FileResponse(static/'index.html')


@app.get('/api/health')
def health():
    with connect() as conn:conn.execute('SELECT 1')
    return {'ok':True}


@app.get('/api/status')
def get_status():return services.status()


@app.get('/api/securities')
def search(q:str='',limit:int=Query(30,ge=1,le=100)):return services.securities(q,limit)


@app.get('/api/bars/{symbol}')
def get_bars(symbol:str,end:date|None=None,start:date|None=None,limit:int=Query(500,ge=1,le=20000),adjustment:str='qfq'):
    return services.bars(symbol,end,limit,adjustment,start)


@app.get('/api/market/{symbol}')
def market(symbol:str,end:date|None=None,start:date|None=None,limit:int=Query(20000,ge=1,le=20000),
           adjustment:str='qfq',timeframe:str='daily',ma:str='5,10,20,60',fast:int=12,slow:int=26,signal:int=9):
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


@app.get('/api/graph')
def get_graph(q:str=Query(min_length=1,max_length=100)):return services.graph(q)


class NewsRequest(BaseModel):
    title:str=Field(min_length=1,max_length=500)
    content:str=Field(default='',max_length=100000)
    source_url:str|None=Field(default=None,max_length=2000)


@app.post('/api/news/analyze')
def news(body:NewsRequest):return services.analyze_news(body.title,body.content,body.source_url)
