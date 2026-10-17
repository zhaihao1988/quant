import json
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4
from psycopg.types.json import Jsonb
from .db import connect
from .domain import ScreenRequest, adjust_bars, metrics, reasons_for, price_changes
from .sessions import market_session


def clean(value):
    return json.loads(json.dumps(value, default=lambda x: x.isoformat() if isinstance(x,(date,datetime)) else float(x) if isinstance(x,Decimal) else str(x),ensure_ascii=False))


def status():
    with connect() as conn:
        counts=conn.execute('''SELECT (SELECT count(*) FROM security) AS securities,
          (SELECT coalesce(sum(record_count),0) FROM import_file WHERE path LIKE '%.day') AS daily_records,
          (SELECT max(latest_date) FROM import_file WHERE path LIKE '%.day') AS latest_file_date,
          (SELECT count(*) FROM capital_event) AS capital_events,
          (SELECT count(*) FROM share_observation) AS share_observations,
          (SELECT count(*) FROM taxonomy) AS taxonomies,
          pg_database_size(current_database()) AS database_bytes''').fetchone()
        counts['imports']=conn.execute('SELECT * FROM import_run ORDER BY started_at DESC LIMIT 8').fetchall()
        session=market_session(conn)
        counts['latest_trade_date']=session['trade_date'] if session else None
        counts['session_coverage']=session
        counts['source']='通达信本地；A股；成交量为股，成交额为元；股票池为采集日快照'
        counts['server_date']=date.today()
        counts['freshness']='请按交易日核对；应用不会自动启动通达信下载，也不以文件修改时间代表每项资料均最新'
        return clean(counts)


def securities(query='', limit=30):
    with connect() as conn:
        return clean(conn.execute('''SELECT s.*, b.trade_date AS latest_trade_date,b.close
          FROM security s LEFT JOIN LATERAL(SELECT trade_date,close FROM daily_bar WHERE symbol=s.symbol ORDER BY trade_date DESC LIMIT 1)b ON true
          WHERE s.symbol ILIKE %s OR s.name ILIKE %s ORDER BY s.symbol LIMIT %s''',(f'%{query}%',f'%{query}%',limit)).fetchall())


def bars(symbol, end=None, limit=500, adjustment='qfq', start=None):
    if adjustment not in ('raw','qfq'):
        raise ValueError('adjustment必须为raw或qfq')
    if not 1<=limit<=20000:
        raise ValueError('limit须为1至20000')
    with connect() as conn:
        if end is None:
            session=market_session(conn)
            if not session:
                raise ValueError('尚无覆盖充分的已收盘日线；如需查看盘中缓存，请显式指定截止日')
            end=session['trade_date']
        if start and start>end:
            raise ValueError('起始日不能晚于截止日')
        coverage=conn.execute('''SELECT min(trade_date) AS available_start,max(trade_date) AS available_end,
          count(*) AS available_rows,count(*) FILTER(WHERE trade_date<=%s AND (%s::date IS NULL OR trade_date>=%s)) AS range_rows
          FROM daily_bar WHERE symbol=%s''',(end,start,start,symbol)).fetchone()
        security=conn.execute('SELECT name FROM security WHERE symbol=%s',(symbol,)).fetchone()
        rows=conn.execute('''SELECT b.*,s.float_shares,s.source AS turnover_source,s.quality AS share_quality,
            CASE WHEN s.float_shares>0 THEN 100.0*b.volume/s.float_shares END AS turnover_pct
          FROM (SELECT * FROM daily_bar WHERE symbol=%s AND trade_date<=%s ORDER BY trade_date DESC LIMIT %s)b
          LEFT JOIN LATERAL(SELECT * FROM share_observation WHERE symbol=b.symbol AND effective_date<=b.trade_date
            ORDER BY effective_date DESC,CASE quality WHEN 'event' THEN 0 ELSE 1 END LIMIT 1)s ON true
          ORDER BY b.trade_date''',(symbol,end,limit+1)).fetchall()
        events=conn.execute('SELECT * FROM capital_event WHERE symbol=%s AND event_date<=%s ORDER BY event_date',(symbol,end)).fetchall()
    metadata={'symbol':symbol,'name':security['name'] if security else symbol,'adjustment':adjustment,'as_of':end,
              'available_start':coverage['available_start'],'available_end':coverage['available_end'],
              'available_rows':coverage['available_rows'],'range_rows':coverage['range_rows']}
    if not rows:
        return clean({**metadata,'bars':[],'anchor':None,'returned_rows':0,'truncated':False})
    anchor=rows[-1]['trade_date']
    special=[e for e in events if (e['category'] in (11,12) or e['category']>14) and rows[0]['trade_date']<e['event_date']<=anchor]
    if adjustment=='qfq' and special:
        raise ValueError('所选区间含尚未支持的扩缩股事件；请选择不复权并单独核验')
    if adjustment=='qfq':
        rows=adjust_bars(rows,events,anchor)
    previous=None
    for row in rows:
        row.update(price_changes(row,previous))
        previous=row
    rows=rows[-limit:]
    if start:
        rows=[row for row in rows if row['trade_date']>=start]
    return clean({**metadata,'anchor':anchor,'bars':rows,'returned_rows':len(rows),
                  'truncated':coverage['range_rows']>len(rows),
                  'turnover_note':'按最近可用股本事件向后延续估算；当前快照不回填过去。历史事件覆盖可能不完整。',
                  'price_note':'普通仿射前复权，与实际分红再投资收益不同；涨跌额、涨跌幅、振幅基于所选价格口径的前一交易日收盘计算；金额和成交量保持原始值'})


def screen(request: ScreenRequest):
    from .data_guard import market_access
    with market_access():return _screen(request)


def _screen(request: ScreenRequest):
    with connect() as conn:
        session=market_session(conn,request.as_of)
        if not session:
            raise ValueError('尚无覆盖充分的已收盘日线，请先完成通达信下载与导入')
        effective=session['trade_date']
        params=[effective,effective]
        clauses=[]
        if request.symbols:
            clauses.append('s.symbol=ANY(%s)');params.append(request.symbols)
        if request.taxonomy_id:
            clauses.append('''s.symbol IN (SELECT symbol FROM membership WHERE taxonomy_id=%s AND snapshot_date=(SELECT max(snapshot_date) FROM membership WHERE taxonomy_id=%s AND snapshot_date<=%s))''')
            params += [request.taxonomy_id,request.taxonomy_id,request.as_of or date.today()]
        extra=' AND '+' AND '.join(clauses) if clauses else ''
        data=conn.execute('''SELECT s.symbol,s.name,b.*,
          sh.source AS turnover_source,CASE WHEN sh.float_shares>0 THEN 100.0*b.volume/sh.float_shares END AS turnover_pct
          FROM security s CROSS JOIN LATERAL (
            SELECT trade_date,open,high,low,close,volume,amount FROM daily_bar
            WHERE symbol=s.symbol AND trade_date<=%s ORDER BY trade_date DESC LIMIT 121
          )b LEFT JOIN LATERAL(
            SELECT float_shares,source FROM share_observation WHERE symbol=s.symbol AND effective_date<=b.trade_date
            ORDER BY effective_date DESC,CASE quality WHEN 'event' THEN 0 ELSE 1 END LIMIT 1
          )sh ON true WHERE EXISTS (SELECT 1 FROM daily_bar WHERE symbol=s.symbol AND trade_date=%s)'''+extra+' ORDER BY s.symbol,b.trade_date',params).fetchall()
        events=conn.execute('SELECT * FROM capital_event WHERE event_date<=%s',(effective,)).fetchall()
    by_symbol={};by_event={}
    for row in data:
        by_symbol.setdefault(row['symbol'],[]).append(row)
    for e in events:
        by_event.setdefault(e['symbol'],[]).append(e)
    results=[];insufficient=unsupported=0
    for symbol,series in by_symbol.items():
        es=by_event.get(symbol,[])
        if request.adjustment=='qfq':
            if any((e['category'] in (11,12) or e['category']>14) and series[0]['trade_date']<e['event_date']<=effective for e in es):
                unsupported+=1;continue
            series=adjust_bars(series,es,effective)
        m=metrics(series,request.breakout_days)
        if m is None:
            insufficient+=1;continue
        reasons=reasons_for(m,request)
        if reasons is not None:
            results.append({'symbol':symbol,'name':series[-1]['name'],'trade_date':effective,'metrics':m,'reasons':reasons})
    results.sort(key=lambda r:r['metrics']['amount'],reverse=True)
    run_id=str(uuid4())
    result=clean({'id':run_id,'effective_date':effective,'adjustment':request.adjustment,'anchor':effective,
                  'scanned':len(by_symbol),'matched':len(results),'insufficient_history':insufficient,
                  'unsupported_corporate_actions':unsupported,'results':results[:request.limit],
                  'session_coverage':session,
                  'note':'历史筛选只使用截至所选交易日的价格和事件；成分股历史仅限已采集快照。成交量倍数为日线指标，非盘中量比。'})
    if session['partial']:
        result['note']+=' 所选日期含盘中或低覆盖数据，请勿视为完整盘后扫描。'
    with connect() as conn:
        conn.execute('INSERT INTO screen_run(id,requested_date,effective_date,parameters,result) VALUES (%s,%s,%s,%s,%s)',
                     (run_id,request.as_of,effective,Jsonb(request.model_dump(mode='json')),Jsonb(result)))
    return result


def taxonomies():
    with connect() as conn:
        return clean(conn.execute('SELECT t.*,max(m.snapshot_date) AS snapshot_date,count(DISTINCT m.symbol) AS members FROM taxonomy t LEFT JOIN membership m ON t.id=m.taxonomy_id GROUP BY t.id ORDER BY kind,name').fetchall())


def graph(query):
    with connect() as conn:
        nodes=conn.execute('SELECT * FROM graph_node WHERE name ILIKE %s OR id=%s ORDER BY kind,name LIMIT 30',(f'%{query}%',query)).fetchall()
        ids=[n['id'] for n in nodes]
        edges=conn.execute('SELECT * FROM graph_edge WHERE source_id=ANY(%s) OR target_id=ANY(%s) ORDER BY observed_at DESC LIMIT 100',(ids,ids)).fetchall()
        linked={e['source_id'] for e in edges}|{e['target_id'] for e in edges}
        if linked:
            nodes=conn.execute('SELECT * FROM graph_node WHERE id=ANY(%s)',(list(linked|set(ids)),)).fetchall()
        return clean({'nodes':nodes,'edges':edges,'note':'member_of仅表示分类归属；上下游关系需有来源证据后独立录入'})


def analyze_news(title, content, source_url=None):
    text=title+'\n'+content
    with connect() as conn:
        news_id=str(uuid4())
        conn.execute('INSERT INTO news_input(id,title,content,source_url) VALUES (%s,%s,%s,%s)',(news_id,title,content,source_url))
        nodes=conn.execute('SELECT id,name,kind FROM graph_node').fetchall()
        matched=[n for n in nodes if len(n['name'])>=2 and (n['name'] in text or n['id'] in text)]
        company_ids={n['id'] for n in matched if n['kind']=='company'}
        topics=[n['id'] for n in matched if n['kind']!='company']
        if topics:
            members=conn.execute('''SELECT DISTINCT m.symbol FROM membership m WHERE m.taxonomy_id=ANY(%s)
              AND m.snapshot_date=(SELECT max(x.snapshot_date) FROM membership x WHERE x.taxonomy_id=m.taxonomy_id)''',(topics,)).fetchall()
            company_ids.update(x['symbol'] for x in members)
    technical=screen(ScreenRequest(symbols=sorted(company_ids),limit=100)) if company_ids else None
    return {'news_id':news_id,'matched_entities':matched,'candidate_count':len(company_ids),
            'technical':technical,'method':'本地名称精确匹配与已采集分类关联；尚未接入大模型或自动新闻源',
            'analysis':'候选仅表示主题关联，尚不构成受益或投资判断；需核对主营业务与新闻原文。'}
