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
from .screen_records import input_identity,stock_scope

RESERVE_LOCK=779924
WORKER_LOCK=779925


def _active(conn):
    conn.execute('SELECT pg_advisory_xact_lock(%s)',(RESERVE_LOCK,))
    existing=conn.execute("SELECT * FROM multi_screen_run WHERE status IN ('queued','running') ORDER BY started_at DESC LIMIT 1").fetchone()
    if not existing:return None
    available=conn.execute('SELECT pg_try_advisory_xact_lock(%s) AS ok',(WORKER_LOCK,)).fetchone()['ok']
    age=(datetime.now(timezone.utc)-existing['heartbeat_at']).total_seconds()
    if not available or (existing['status']=='queued' and age<120):return existing
    conn.execute("""UPDATE multi_screen_run SET status='interrupted',finished_at=now(),
        archive_error=CASE WHEN archive_state='pending' THEN '服务中断，已完成的逐股结果保留，可继续或补存' ELSE archive_error END,
        archive_state=CASE WHEN archive_state='pending' THEN 'failed' ELSE archive_state END WHERE id=%s""",(existing['id'],))
    return None


def latest_session():
    from .trade_calendar import expected_trade_date
    with connect() as conn:
        session=market_session(conn)
    expected=expected_trade_date()
    return clean({'date_mode':'latest','effective_date':session['trade_date'] if session else None,
                  'session_coverage':session,'expected_date':expected,
                  'calendar_note':None if expected else '当前年份交易日历尚未核验，仅按文件日期判断'})


def reserve(request: MultiScreenRequest,reuse_completed=False,force=False):
    with connect() as conn:
        session=market_session(conn,request.as_of)
        if not session:raise ValueError('尚无可筛选的盘后数据')
        existing=_active(conn)
        if existing:
            if MultiScreenRequest.model_validate(existing['parameters'])==request and existing['effective_date']==session['trade_date']:
                return str(existing['id']),True
            if request.as_of is None and existing['effective_date']!=session['trade_date']:
                raise ValueError('最新交易日已更新，但旧日期任务仍在运行；请等待或停止旧任务，再按最新日期新建选股')
            raise ValueError('已有多策略任务正在运行，请等待或停止该任务后再提交')
        signature,scope,manifest=input_identity(conn,request,session['trade_date'])
        if reuse_completed and not force:
            old=conn.execute("SELECT id FROM multi_screen_run WHERE input_signature=%s AND status IN ('completed','partial') AND archive_state IN ('saved','failed','pending') ORDER BY finished_at DESC LIMIT 1",(signature,)).fetchone()
            if old:return str(old['id']),True
        return _create(conn,request,session['trade_date'],signature,scope,manifest),False


def _create(conn,request,effective,signature,scope,manifest,parent=None):
        run_id=str(uuid4())
        version=conn.execute('SELECT coalesce(max(version),0)+1 AS n FROM multi_screen_run WHERE effective_date=%s AND scope_signature=%s',(effective,scope)).fetchone()['n']
        conn.execute("""INSERT INTO multi_screen_run(id,status,requested_date,effective_date,parameters,input_signature,scope_signature,input_manifest,parent_run_id,version,archive_state)
            VALUES (%s,'queued',%s,%s,%s,%s,%s,%s,%s,%s,'pending')""",
                     (run_id,request.as_of,effective,Jsonb(request.model_dump(mode='json')),signature,scope,Jsonb(manifest),parent,version))
        return run_id


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
        current,_,_=input_identity(conn,params,job['effective_date'])
        if not job['input_signature'] or current!=job['input_signature']:raise ValueError('行情、权息、范围或策略版本已变化，请新建选股；旧结果保留')
        if params.as_of is None:
            session=market_session(conn)
            if not session or session['trade_date']!=job['effective_date']:
                raise ValueError('最新可用交易日已变化，请新建最新日期选股；旧任务结果仍保留')
        conn.execute("UPDATE multi_screen_run SET status='queued',parameters=%s,cancel_requested=false,finished_at=NULL,archive_state='pending',archive_error=NULL,heartbeat_at=now() WHERE id=%s",(Jsonb(params.model_dump(mode='json')),run_id))
    return str(run_id)


def snapshot(run_id,view='latest',recover=True,archive_revision=None):
    if view not in ('latest','archive'):raise ValueError('非法结果口径')
    if view=='archive':
        from .screen_records import archive
        record=archive(run_id,archive_revision)
        return {**record['payload'],'archive_info':{k:v for k,v in record.items() if k!='payload'}}
    with connect() as conn:
        if recover:_active(conn)
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
    row['result']['fundamental_reference']='latest_collected'
    row['result']['note']='原7个技术策略条件不变；按用户要求统一附加最新财务参考，明确财务和价格日期，冲突报告隔离。'
    row['view']='latest'
    row.pop('input_manifest',None)
    with connect() as conn:
        row['archive_info']=conn.execute("SELECT revision,created_at,sha256,export_error,payload->'comparison' AS comparison FROM screen_archive WHERE run_id=%s ORDER BY revision DESC LIMIT 1",(run_id,)).fetchone()
    return clean(row)


def recent(limit=10,offset=0,day=None,state='',scope=''):
    with connect() as conn:
        _active(conn)
        where=[];params=[]
        if day:where.append('effective_date=%s');params.append(day)
        if state:where.append('status=%s');params.append(state)
        if scope:where.append('scope_signature=%s');params.append(scope)
        clause=' WHERE '+' AND '.join(where) if where else ''
        rows=conn.execute("""SELECT id,status,effective_date,total,processed,current_symbol,started_at,finished_at,version,archive_state,archive_error,parameters,scope_signature,
          coalesce(jsonb_array_length(result->'signals'),0)+(SELECT coalesce(sum(jsonb_array_length(i.result->'signals')),0) FROM multi_screen_item i WHERE i.run_id=multi_screen_run.id) AS signals,
          coalesce(jsonb_array_length(result->'errors'),0)+(SELECT coalesce(sum(jsonb_array_length(i.result->'errors')),0) FROM multi_screen_item i WHERE i.run_id=multi_screen_run.id) AS errors
          FROM multi_screen_run"""+clause+' ORDER BY started_at DESC LIMIT %s OFFSET %s',params+[limit,offset]).fetchall()
    return clean(rows)


def history(limit=20,offset=0,day=None,state='',scope=''):
    items=recent(limit,offset,day,state,scope)
    where=[];params=[]
    if day:where.append('effective_date=%s');params.append(day)
    if state:where.append('status=%s');params.append(state)
    if scope:where.append('scope_signature=%s');params.append(scope)
    with connect() as conn:total=conn.execute('SELECT count(*) AS n FROM multi_screen_run'+(' WHERE '+' AND '.join(where) if where else ''),params).fetchone()['n']
    return {'items':items,'total':total,'offset':offset,'limit':limit}


def task_progress(run_id):
    with connect() as conn:
        _active(conn)
        row=conn.execute('SELECT id,status,effective_date,total,processed,archive_state,archive_error FROM multi_screen_run WHERE id=%s',(run_id,)).fetchone()
    if not row:raise ValueError('任务不存在')
    return clean(row)


def retry_failed(run_id,workers=4):
    if not 1<=workers<=4:raise ValueError('并行数须为1至4')
    with connect() as conn:
        if _active(conn):raise ValueError('已有任务运行，请等待完成')
        job=conn.execute('SELECT * FROM multi_screen_run WHERE id=%s',(run_id,)).fetchone()
        if not job or job['status'] not in ('partial','failed'):raise ValueError('此任务没有可单独重试的失败项')
        request=MultiScreenRequest.model_validate({**job['parameters'],'workers':workers})
        signature,scope,manifest=input_identity(conn,request,job['effective_date'])
        if signature!=job['input_signature']:raise ValueError('输入已变化，请按最新数据新建选股；旧归档保留')
        bad=conn.execute("SELECT count(*) AS n FROM multi_screen_item WHERE run_id=%s AND jsonb_array_length(result->'errors')>0",(run_id,)).fetchone()['n']
        if not bad:raise ValueError('没有可重算的逐股失败记录，请检查任务问题')
        child=_create(conn,request,job['effective_date'],signature,scope,manifest,run_id)
        copied=conn.execute("""INSERT INTO multi_screen_item(run_id,ordinal,symbol,result)
            SELECT %s,ordinal,symbol,result FROM multi_screen_item WHERE run_id=%s AND jsonb_array_length(result->'errors')=0""",(child,run_id)).rowcount
        conn.execute('UPDATE multi_screen_run SET total=%s,processed=%s WHERE id=%s',(job['total'],copied,child))
    return child


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
    from .data_guard import market_access
    from .screen_records import save_archive
    try:
        with market_access():
            _run_job(run_id)
            with connect() as conn:row=conn.execute('SELECT status,archive_state FROM multi_screen_run WHERE id=%s',(run_id,)).fetchone()
            if row and row['status'] not in ('queued','running') and row['archive_state'] in ('pending','failed'):
                try:save_archive(run_id)
                except Exception as exc:
                    with connect() as conn:conn.execute("UPDATE multi_screen_run SET archive_state='failed',archive_error=%s WHERE id=%s",(f'{type(exc).__name__}: {exc}',run_id))
    except ValueError as exc:
        with connect() as conn:conn.execute("UPDATE multi_screen_run SET status='failed',finished_at=now(),archive_state='failed',archive_error=%s,result=jsonb_set(result,'{fatal_error}',%s) WHERE id=%s AND status='queued'",(str(exc),Jsonb(str(exc)),run_id))


def _run_job(run_id):
    with connect() as lock:
        if not lock.execute('SELECT pg_try_advisory_lock(%s) AS ok',(WORKER_LOCK,)).fetchone()['ok']:return
        lock.commit()
        try:
            with connect() as conn:
                job=conn.execute('SELECT * FROM multi_screen_run WHERE id=%s',(run_id,)).fetchone()
                if not job or job['status']!='queued':return
                request=MultiScreenRequest.model_validate(job['parameters'])
                effective=job['effective_date']
                signature,_,manifest=input_identity(conn,request,effective)
                if job['input_signature'] and signature!=job['input_signature']:raise ValueError('输入数据已变化，请新建选股')
                stocks=manifest['stocks']
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
