"""Bounded parallel scans with unchanged strategies and durable stock results."""
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
import logging
import multiprocessing
from uuid import uuid4
from psycopg.types.json import Jsonb
from .db import connect
from .sessions import market_session
from .services import clean
from .multiscreener import MultiScreenRequest,load_strategy_data,evaluate_stock

RESERVE_LOCK=779924
WORKER_LOCK=779925


def _active(conn):
    conn.execute('SELECT pg_advisory_xact_lock(%s)',(RESERVE_LOCK,))
    existing=conn.execute("SELECT * FROM multi_screen_run WHERE status IN ('queued','running') ORDER BY started_at DESC LIMIT 1").fetchone()
    if not existing:return None
    available=conn.execute('SELECT pg_try_advisory_xact_lock(%s) AS ok',(WORKER_LOCK,)).fetchone()['ok']
    age=(datetime.now(timezone.utc)-existing['heartbeat_at']).total_seconds()
    if not available or (existing['status']=='queued' and age<120):return existing
    conn.execute("UPDATE multi_screen_run SET status='interrupted',finished_at=now() WHERE id=%s",(existing['id'],))
    return None


def reserve(request: MultiScreenRequest):
    with connect() as conn:
        existing=_active(conn)
        if existing:
            if MultiScreenRequest.model_validate(existing['parameters'])==request:return str(existing['id']),True
            raise ValueError('已有多策略任务正在运行，请等待或停止该任务后再提交')
        session=market_session(conn,request.as_of)
        if not session:raise ValueError('尚无可筛选的盘后数据')
        run_id=str(uuid4())
        conn.execute("INSERT INTO multi_screen_run(id,status,requested_date,effective_date,parameters) VALUES (%s,'queued',%s,%s,%s)",
                     (run_id,request.as_of,session['trade_date'],Jsonb(request.model_dump(mode='json'))))
    return run_id,False


def resume(run_id, workers=4):
    if not 1<=workers<=4:raise ValueError('并行数须为1至4')
    with connect() as conn:
        if _active(conn):raise ValueError('请先停止当前任务，再继续未完成部分')
        job=conn.execute('SELECT * FROM multi_screen_run WHERE id=%s FOR UPDATE',(run_id,)).fetchone()
        if not job or job['status'] not in ('cancelled','interrupted','failed'):
            raise ValueError('仅可继续已停止、中断或失败的任务')
        saved=conn.execute('SELECT count(*) AS n FROM multi_screen_item WHERE run_id=%s',(run_id,)).fetchone()['n']
        if saved!=job['processed']:raise ValueError('旧任务缺少完整逐股记录，请新建任务')
        params=MultiScreenRequest.model_validate({**job['parameters'],'workers':workers})
        conn.execute("UPDATE multi_screen_run SET status='queued',parameters=%s,cancel_requested=false,finished_at=NULL,heartbeat_at=now() WHERE id=%s",(Jsonb(params.model_dump(mode='json')),run_id))
    return str(run_id)


def snapshot(run_id):
    with connect() as conn:
        row=conn.execute('SELECT * FROM multi_screen_run WHERE id=%s',(run_id,)).fetchone()
        items=conn.execute('SELECT result FROM multi_screen_item WHERE run_id=%s ORDER BY ordinal',(run_id,)).fetchall()
    if not row:raise ValueError('多策略任务不存在')
    for item in items:
        for key in ('signals','errors','coverage'):
            row['result'].setdefault(key,[]).extend(item['result'].get(key,[]))
    from .fundamentals import attach
    row['result']['signals']=attach(row['result'].get('signals',[]),row['effective_date'])
    if any(s.get('availability_policy')=='actual_collection_only' for s in row['result']['signals']):
        row['result']['fundamental_status']='actual_collection_only'
    row['result']['note']='原7个技术策略条件不变；财务指标只使用筛选日已采集并核验的版本，冲突报告隔离，缺项不填零。'
    return clean(row)


def recent():
    with connect() as conn:
        rows=conn.execute("""SELECT id,status,effective_date,total,processed,current_symbol,started_at,finished_at,
          coalesce(jsonb_array_length(result->'signals'),0)+(SELECT coalesce(sum(jsonb_array_length(i.result->'signals')),0) FROM multi_screen_item i WHERE i.run_id=multi_screen_run.id) AS signals,
          coalesce(jsonb_array_length(result->'errors'),0)+(SELECT coalesce(sum(jsonb_array_length(i.result->'errors')),0) FROM multi_screen_item i WHERE i.run_id=multi_screen_run.id) AS errors
          FROM multi_screen_run ORDER BY started_at DESC LIMIT 10""").fetchall()
    return clean(rows)


def cancel(run_id):
    with connect() as conn:
        row=conn.execute("UPDATE multi_screen_run SET cancel_requested=true WHERE id=%s AND status IN ('queued','running') RETURNING id",(run_id,)).fetchone()
    return {'requested':bool(row)}


def scan_stock(args):
    """Worker-local connections and fresh per-stock strategy instances."""
    logging.getLogger('quant.legacy_strategies').setLevel(logging.WARNING)
    ordinal,stock,effective,selected=args
    item={'signals':[],'errors':[],'coverage':[]}
    try:
        data,_=load_strategy_data(stock['symbol'],effective)
        if not data:item['errors'].append({'symbol':stock['symbol'],'error':'截至筛选日无日线数据，未执行策略'})
        else:
            signals,errors,evaluations=evaluate_stock(stock['symbol'],stock['name'],effective,data,selected)
            item['signals'].extend(signals);item['errors'].extend(errors)
            item['coverage'].append({'symbol':stock['symbol'],'daily':len(data['daily']),'weekly':len(data['weekly']),
              'monthly':len(data['monthly']),'first_date':data['daily']['date'].iloc[0].isoformat(),
              'last_date':data['daily']['date'].iloc[-1].isoformat(),'evaluations':evaluations})
    except Exception as exc:item['errors'].append({'symbol':stock['symbol'],'error':f'{type(exc).__name__}: {exc}'})
    return ordinal,stock['symbol'],item


def stock_results(work, workers, should_stop, heartbeat):
    """Bound in-flight work; original stock order is retained in the saved ordinal."""
    if workers==1:
        for args in work:
            if should_stop():break
            heartbeat([args[1]['symbol']])
            yield scan_stock(args)
        return
    remaining=iter(work)
    with ProcessPoolExecutor(max_workers=workers,mp_context=multiprocessing.get_context('spawn')) as pool:
        pending={};stopping=False
        def fill():
            while not stopping and len(pending)<workers:
                args=next(remaining,None)
                if args is None:break
                pending[pool.submit(scan_stock,args)]=args
        if should_stop():return
        fill()
        while pending:
            stopping=stopping or should_stop()
            if stopping:
                for future in pending:future.cancel()
            heartbeat([args[1]['symbol'] for args in pending.values()])
            done,_=wait(pending,timeout=1,return_when=FIRST_COMPLETED)
            for future in done:
                pending.pop(future)
                if not future.cancelled():yield future.result()
            stopping=stopping or should_stop()
            fill()


def run_job(run_id):
    with connect() as lock:
        if not lock.execute('SELECT pg_try_advisory_lock(%s) AS ok',(WORKER_LOCK,)).fetchone()['ok']:return
        lock.commit()
        try:
            with connect() as conn:
                job=conn.execute('SELECT * FROM multi_screen_run WHERE id=%s',(run_id,)).fetchone()
                if not job or job['status']!='queued':return
                request=MultiScreenRequest.model_validate(job['parameters'])
                effective=job['effective_date']
                clauses=["s.name<>''"];params=[]
                if request.symbols:clauses.append('s.symbol=ANY(%s)');params.append(request.symbols)
                if request.taxonomy_id:
                    clauses.append('''s.symbol IN (SELECT symbol FROM membership WHERE taxonomy_id=%s AND snapshot_date=(SELECT max(snapshot_date) FROM membership WHERE taxonomy_id=%s AND snapshot_date<=%s))''')
                    params.extend([request.taxonomy_id,request.taxonomy_id,effective])
                stocks=conn.execute('SELECT s.symbol,s.name FROM security s WHERE '+' AND '.join(clauses)+' ORDER BY s.symbol',params).fetchall()
                saved=conn.execute('SELECT symbol,result FROM multi_screen_item WHERE run_id=%s',(run_id,)).fetchall()
                done={item['symbol'] for item in saved}
                if done-{s['symbol'] for s in stocks} or (done and len(stocks)!=job['total']):raise ValueError('股票池已变化，不能继续此任务，请新建任务')
                work=[(i,stock,effective,request.strategies) for i,stock in enumerate(stocks,1) if stock['symbol'] not in done]
                workers=min(request.workers,max(1,len(work)))
                missing=set(request.symbols)-{s['symbol'] for s in stocks}
                output={'signals':[],'errors':[{'symbol':s,'error':'不在所选本地股票池中'} for s in sorted(missing)],'coverage':[],
                    'adjustment':'qfq','volume_unit':'手','lookback':'all_history','fundamental_status':'not_connected',
                    'execution':{'mode':'processes' if workers>1 else 'sequential','workers':workers,'resumed_after':len(done)},
                    'note':'原7个策略独立触发后汇总；仅保留筛选日信号。基本面未接入。历史成分仅覆盖已采集快照。'}
                conn.execute("UPDATE multi_screen_run SET status='running',total=%s,processed=%s,result=%s,heartbeat_at=now() WHERE id=%s",(len(stocks),len(done),Jsonb(output),run_id))
            processed=len(done);stopped=False
            had_errors=bool(output['errors']) or any(s['result'].get('errors') for s in saved)
            with connect() as writer:
                def should_stop():
                    nonlocal stopped
                    row=writer.execute('SELECT cancel_requested FROM multi_screen_run WHERE id=%s',(run_id,)).fetchone()
                    writer.commit();stopped=stopped or row['cancel_requested']
                    return stopped
                def heartbeat(symbols):
                    writer.execute('UPDATE multi_screen_run SET current_symbol=%s,heartbeat_at=now() WHERE id=%s',(', '.join(symbols),run_id));writer.commit()
                for ordinal,symbol,item in stock_results(work,workers,should_stop,heartbeat):
                    writer.execute('INSERT INTO multi_screen_item(run_id,ordinal,symbol,result) VALUES (%s,%s,%s,%s)',(run_id,ordinal,symbol,Jsonb(item)))
                    processed+=1;had_errors=had_errors or bool(item['errors'])
                    writer.execute('UPDATE multi_screen_run SET processed=%s,heartbeat_at=now() WHERE id=%s',(processed,run_id));writer.commit()
                state='cancelled' if stopped else 'partial' if had_errors else 'completed'
                writer.execute('UPDATE multi_screen_run SET status=%s,current_symbol=NULL,finished_at=now(),heartbeat_at=now() WHERE id=%s',(state,run_id))
        except Exception as exc:
            with connect() as conn:
                conn.execute("UPDATE multi_screen_run SET status='failed',result=jsonb_set(result,'{fatal_error}',%s),finished_at=now() WHERE id=%s",(Jsonb(f'{type(exc).__name__}: {exc}'),run_id))
        finally:
            lock.execute('SELECT pg_advisory_unlock(%s)',(WORKER_LOCK,));lock.commit()
