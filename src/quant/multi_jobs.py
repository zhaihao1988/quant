"""Persisted, single-worker strategy scans. Existing combination screener is independent."""
from datetime import date, datetime, timezone
from uuid import uuid4
from psycopg.types.json import Jsonb
from .db import connect
from .sessions import market_session
from .services import clean
from .multiscreener import MultiScreenRequest,load_strategy_data,evaluate_stock


def reserve(request: MultiScreenRequest):
    with connect() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(779924)')
        existing=conn.execute("SELECT * FROM multi_screen_run WHERE status IN ('queued','running') ORDER BY started_at DESC LIMIT 1").fetchone()
        if existing:
            available=conn.execute('SELECT pg_try_advisory_xact_lock(779925) AS ok').fetchone()['ok']
            age=(datetime.now(timezone.utc)-existing['heartbeat_at']).total_seconds()
            if not available or (existing['status']=='queued' and age<120):
                if existing['parameters']==request.model_dump(mode='json'):
                    return str(existing['id']),True
                raise ValueError('已有多策略任务正在运行，请等待或停止该任务后再提交')
            conn.execute("UPDATE multi_screen_run SET status='interrupted',finished_at=now() WHERE id=%s",(existing['id'],))
        session=market_session(conn,request.as_of)
        if not session:raise ValueError('尚无可筛选的盘后数据')
        run_id=str(uuid4())
        conn.execute("INSERT INTO multi_screen_run(id,status,requested_date,effective_date,parameters) VALUES (%s,'queued',%s,%s,%s)",
                     (run_id,request.as_of,session['trade_date'],Jsonb(request.model_dump(mode='json'))))
    return run_id,False


def snapshot(run_id):
    with connect() as conn:
        row=conn.execute('SELECT * FROM multi_screen_run WHERE id=%s',(run_id,)).fetchone()
        items=conn.execute('SELECT result FROM multi_screen_item WHERE run_id=%s ORDER BY ordinal',(run_id,)).fetchall()
    if not row:raise ValueError('多策略任务不存在')
    for item in items:
        for key in ('signals','errors','coverage'):
            row['result'].setdefault(key,[]).extend(item['result'].get(key,[]))
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


def run_job(run_id):
    with connect() as lock:
        if not lock.execute('SELECT pg_try_advisory_lock(779925) AS ok').fetchone()['ok']:return
        lock.commit()
        try:
            job=snapshot(run_id)
            if job['status']!='queued':return
            request=MultiScreenRequest.model_validate(job['parameters'])
            effective=date.fromisoformat(job['effective_date'])
            with connect() as conn:
                # Original runner scans all named stocks, including suspended stocks.
                clauses=["s.name<>''"];params=[]
                if request.symbols:clauses.append('s.symbol=ANY(%s)');params.append(request.symbols)
                if request.taxonomy_id:
                    clauses.append('''s.symbol IN (SELECT symbol FROM membership WHERE taxonomy_id=%s AND snapshot_date=(SELECT max(snapshot_date) FROM membership WHERE taxonomy_id=%s AND snapshot_date<=%s))''')
                    params.extend([request.taxonomy_id,request.taxonomy_id,effective])
                stocks=conn.execute('SELECT s.symbol,s.name FROM security s WHERE '+' AND '.join(clauses)+' ORDER BY s.symbol',params).fetchall()
                if request.symbols:
                    missing=set(request.symbols)-{s['symbol'] for s in stocks}
                else:missing=set()
                conn.execute("UPDATE multi_screen_run SET status='running',total=%s,heartbeat_at=now() WHERE id=%s",(len(stocks),run_id))
            output={'signals':[],'errors':[{'symbol':s,'error':'不在所选本地股票池中'} for s in sorted(missing)],'coverage':[],
                    'adjustment':'qfq','volume_unit':'手','lookback':'all_history','fundamental_status':'not_connected',
                    'note':'原7个策略独立触发后汇总；仅保留筛选日信号。基本面未接入。历史成分仅覆盖已采集快照。'}
            processed=0;state='completed';had_errors=bool(output['errors'])
            with connect() as conn:
                conn.execute('UPDATE multi_screen_run SET result=%s WHERE id=%s',(Jsonb(output),run_id))
            for stock in stocks:
                with connect() as conn:
                    cancelled=conn.execute('SELECT cancel_requested FROM multi_screen_run WHERE id=%s',(run_id,)).fetchone()['cancel_requested']
                    conn.execute('UPDATE multi_screen_run SET current_symbol=%s,heartbeat_at=now() WHERE id=%s',(stock['symbol'],run_id))
                if cancelled:state='cancelled';break
                item={'signals':[],'errors':[],'coverage':[]}
                try:
                    data,meta=load_strategy_data(stock['symbol'],effective)
                    if not data:
                        item['errors'].append({'symbol':stock['symbol'],'error':'截至筛选日无日线数据，未执行策略'})
                    else:
                        signals,errors,evaluations=evaluate_stock(stock['symbol'],stock['name'],effective,data,request.strategies)
                        item['signals'].extend(signals);item['errors'].extend(errors)
                        item['coverage'].append({'symbol':stock['symbol'],'daily':len(data['daily']),'weekly':len(data['weekly']),
                          'monthly':len(data['monthly']),'first_date':data['daily']['date'].iloc[0].isoformat(),
                          'last_date':data['daily']['date'].iloc[-1].isoformat(),'evaluations':evaluations})
                except Exception as exc:
                    item['errors'].append({'symbol':stock['symbol'],'error':f'{type(exc).__name__}: {exc}'})
                processed+=1
                had_errors=had_errors or bool(item['errors'])
                with connect() as conn:
                    conn.execute('INSERT INTO multi_screen_item(run_id,ordinal,symbol,result) VALUES (%s,%s,%s,%s)',(run_id,processed,stock['symbol'],Jsonb(item)))
                    conn.execute('UPDATE multi_screen_run SET processed=%s,heartbeat_at=now() WHERE id=%s',(processed,run_id))
            if state=='completed' and had_errors:state='partial'
            with connect() as conn:
                conn.execute('UPDATE multi_screen_run SET status=%s,result=%s,current_symbol=NULL,finished_at=now(),heartbeat_at=now() WHERE id=%s',(state,Jsonb(output),run_id))
        except Exception as exc:
            with connect() as conn:
                conn.execute("UPDATE multi_screen_run SET status='failed',result=jsonb_set(result,'{fatal_error}',%s),finished_at=now() WHERE id=%s",(Jsonb(f'{type(exc).__name__}: {exc}'),run_id))
        finally:
            lock.execute('SELECT pg_advisory_unlock(779925)');lock.commit()
