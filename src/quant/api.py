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
from .ingest import new_run,run_import
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
def get_bars(symbol:str,end:date|None=None,limit:int=Query(500,ge=1,le=5000),adjustment:str='qfq'):
    return services.bars(symbol,end,limit,adjustment)


class ImportRequest(BaseModel):
    symbols:list[str]=Field(default_factory=list,max_length=10000)


@app.post('/api/imports',status_code=202)
def start_import(body:ImportRequest,tasks:BackgroundTasks):
    run_id=new_run();tasks.add_task(run_import,run_id,body.symbols)
    return {'id':run_id,'status':'queued'}


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
