"""Legacy annual-report formulas over explicitly available, reviewed TDX inputs."""
from datetime import date,datetime,time,timedelta
from contextlib import nullcontext
import hashlib,json,math
from zoneinfo import ZoneInfo
from psycopg.types.json import Jsonb
from .db import connect
from .services import clean

FORMULA_VERSION='annual-observed-v2'
FINANCIAL_KEYS=['pe','pb','revenue_growth_yoy','profit_growth_yoy','peg_like_ratio',
 'net_profit_positive_3y_latest','growth_positive','pe_lt_30','peg_like_lt_1']
LABELS={'available':'可用','not_connected':'未接入','not_collected_as_of':'采集日前不可用',
 'missing_report':'缺财报','missing_field':'缺科目','pending_conflict':'冲突待确认',
 'unreviewed':'尚未核验','missing_announcement':'缺公告日','not_announced':'尚未披露',
 'missing_price':'缺收盘价','missing_shares':'缺当时可见股本','invalid_denominator':'分母不适用',
 'insufficient_annuals':'年报不足3年','missing_comparison':'缺上年同期','not_applicable':'不适用'}


def calculate_metrics(reports,price,shares,default_state='missing_report'):
    """reports are period-sorted candidates; a blocked latest report is never replaced silently."""
    out={key:None for key in ['market_cap','market_cap_formatted']+FINANCIAL_KEYS}
    states={key:default_state for key in out};basis={}
    def value(report,key):
        if report is None:return None,default_state
        if report['state']!='available':return None,report['state']
        field=report['values'].get(key)
        return (field,'available') if field is not None else (None,'missing_field')
    if price is None:cap_state='missing_price'
    elif shares is None or shares<=0:cap_state='missing_shares'
    else:
        out['market_cap']=out['market_cap_formatted']=price*shares;cap_state='available'
    states['market_cap']=states['market_cap_formatted']=cap_state
    latest=reports[0] if reports else None
    annuals=[r for r in reports if r['period'].month==12 and r['period'].day==31]
    annual=annuals[0] if annuals else None
    profit,profit_state=value(annual,'net_profit_parent')
    if cap_state=='available' and profit is not None and profit>1e-6:
        out['pe']=out['market_cap']/profit;out['pe_lt_30']=out['pe']<30
        states['pe']=states['pe_lt_30']='available'
    else:states['pe']=states['pe_lt_30']=profit_state if profit is None else cap_state if cap_state!='available' else 'invalid_denominator'
    equity,equity_state=value(latest,'equity_parent')
    if cap_state=='available' and equity is not None and equity>1e-9:
        out['pb']=out['market_cap']/equity;states['pb']='available'
    else:states['pb']=equity_state if equity is None else cap_state if cap_state!='available' else 'invalid_denominator'
    previous=next((r for r in reports if latest and r['period']==latest['period'].replace(year=latest['period'].year-1)),None)
    for metric,field in [('revenue_growth_yoy','revenue_total'),('profit_growth_yoy','net_profit_parent')]:
        current,current_state=value(latest,field);prior,prior_state=value(previous,field)
        if current is None:states[metric]=current_state
        elif previous is None:states[metric]='missing_comparison'
        elif prior is None:states[metric]=prior_state
        elif abs(prior)>1e-9:out[metric]=(current-prior)/abs(prior);states[metric]='available'
        elif metric=='profit_growth_yoy' and current>1e-9:out[metric]=float('inf');states[metric]='available'
        else:states[metric]='invalid_denominator'
    revenue_growth=out['revenue_growth_yoy'];profit_growth=out['profit_growth_yoy']
    if revenue_growth is not None and profit_growth is not None:
        out['growth_positive']=revenue_growth>1e-6 and profit_growth>1e-6;states['growth_positive']='available'
    else:states['growth_positive']=states['revenue_growth_yoy'] if revenue_growth is None else states['profit_growth_yoy']
    if out['pe'] is not None and profit_growth is not None and math.isfinite(profit_growth) and profit_growth>1e-6:
        out['peg_like_ratio']=out['pe']/(profit_growth*100);out['peg_like_lt_1']=out['peg_like_ratio']<1
        states['peg_like_ratio']=states['peg_like_lt_1']='available'
    else:
        reason=states['pe'] if out['pe'] is None else states['profit_growth_yoy'] if profit_growth is None else 'not_applicable'
        states['peg_like_ratio']=states['peg_like_lt_1']=reason
    latest_profit,latest_profit_state=value(latest,'net_profit_parent')
    if latest_profit is None:states['net_profit_positive_3y_latest']=latest_profit_state
    elif latest_profit<=1e-6:
        out['net_profit_positive_3y_latest']=False;states['net_profit_positive_3y_latest']='available'
    elif len(annuals)<3:states['net_profit_positive_3y_latest']='insufficient_annuals'
    else:
        checked=[value(r,'net_profit_parent') for r in annuals[:3]]
        if any(v is None for v,_ in checked):states['net_profit_positive_3y_latest']=next(s for v,s in checked if v is None)
        else:
            out['net_profit_positive_3y_latest']=all(v>1e-6 for v,_ in checked);states['net_profit_positive_3y_latest']='available'
    for key in out:
        value_=out[key]
        if isinstance(value_,float):out[key]=round(value_,4) if math.isfinite(value_) else 'inf' if value_>0 else '-inf'
    for key,report in [('latest_report',latest),('annual_for_pe',annual),('comparison_report',previous)]:
        if report:basis[key]={'period':report['period'].isoformat(),'report_ids':report['ids'],'state':report['state']}
    out.update(fundamental_states=states,fundamental_labels={key:LABELS.get(state,state) for key,state in states.items()},
               fundamental_status='available' if all(states[k]=='available' for k in FINANCIAL_KEYS) else 'partial',
               fundamental_basis=basis,formula_version=FORMULA_VERSION,pe_basis='最近可见年报归母净利润；非TTM')
    return out


def cutoff_for(day):
    return datetime.combine(day+timedelta(days=1),time.min,ZoneInfo('Asia/Shanghai'))


def load_inputs(symbol,as_of,connection=None):
    cutoff=cutoff_for(as_of)
    with (nullcontext(connection) if connection is not None else connect()) as conn:
        if not conn.execute('SELECT 1 FROM security WHERE symbol=%s',(symbol,)).fetchone():
            raise ValueError('证券不在本地数据库中')
        rows=conn.execute('''SELECT r.id,r.report_date,r.announced_on,r.standard,r.review_state,r.reviewed_at,
          f.id AS file_id,f.observed_at FROM financial_report r JOIN financial_file_revision f ON r.file_id=f.id
          WHERE r.symbol=%s AND r.report_date<=%s AND f.observed_at<%s ORDER BY r.report_date DESC,f.observed_at DESC,f.id DESC,r.source_ordinal''',(symbol,as_of,cutoff)).fetchall()
        exists=conn.execute('SELECT 1 FROM financial_report WHERE symbol=%s LIMIT 1',(symbol,)).fetchone()
        price=conn.execute('SELECT close,trade_date FROM daily_bar WHERE symbol=%s AND trade_date BETWEEN %s AND %s ORDER BY trade_date DESC LIMIT 1',(symbol,as_of-timedelta(days=7),as_of)).fetchone()
        shares=conn.execute('''SELECT total_shares,effective_date,source FROM share_observation WHERE symbol=%s
          AND effective_date<=%s AND observed_at<%s AND total_shares>0 ORDER BY effective_date DESC,
          CASE quality WHEN 'event' THEN 0 ELSE 1 END LIMIT 1''',(symbol,as_of,cutoff)).fetchone()
    chosen={};grouped={}
    for row in rows:
        period=row['report_date']
        if period not in chosen:chosen[period]=row['file_id']
        if row['file_id']==chosen[period]:grouped.setdefault(period,[]).append(row)
    reports=[]
    for period,group in grouped.items():
        first=group[0]
        if any(r['review_state']=='pending_conflict' for r in group):state='pending_conflict'
        elif any(r['review_state']!='verified' for r in group):state='unreviewed'
        elif any(r['reviewed_at'] is None or r['reviewed_at']>=cutoff for r in group):state='not_collected_as_of'
        elif first['announced_on'] is None:state='missing_announcement'
        elif first['announced_on']>as_of:state='not_announced'
        else:state='available'
        facts={key:field.get('value') for key,field in first['standard'].items()} if state=='available' else {}
        reports.append({'period':period,'state':state,'values':facts,'ids':[r['id'] for r in group]})
    default='not_collected_as_of' if exists and not rows else 'missing_report'
    result=calculate_metrics(reports,float(price['close']) if price else None,shares['total_shares'] if shares else None,default)
    result.update(symbol=symbol,as_of=as_of.isoformat(),availability_policy='actual_collection_only',
       valuation_price_basis='不复权收盘价×当时可见总股本',price_date=str(price['trade_date']) if price else None,
       shares_source=shares['source'] if shares else None,shares_effective_date=str(shares['effective_date']) if shares else None,
       original_revision_note='只使用实际采集并核验后可见版本；未还原采集前首次披露数据；冲突报告不参与指标。')
    return result


def signature():
    with connect() as conn:
        row=conn.execute('''SELECT (SELECT max(id) FROM financial_file_revision) AS financial,
         (SELECT max(imported_at) FROM import_file) AS quotes,(SELECT max(observed_at) FROM share_observation) AS shares,
         (SELECT max(reviewed_at) FROM financial_report) AS review''').fetchone()
    return hashlib.sha256((FORMULA_VERSION+json.dumps(row,default=str,sort_keys=True)).encode()).hexdigest()


def metrics_many(symbols,as_of):
    if not symbols:return {}
    token=signature()
    with connect() as conn:
        existing=conn.execute('SELECT symbol,result FROM financial_metric_cache WHERE symbol=ANY(%s) AND as_of=%s AND signature=%s',(list(symbols),as_of,token)).fetchall()
        result={r['symbol']:r['result'] for r in existing}
        for symbol in sorted(set(symbols)-set(result)):
            computed=load_inputs(symbol,as_of,conn)
            conn.execute('INSERT INTO financial_metric_cache(symbol,as_of,signature,result) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING',(symbol,as_of,token,Jsonb(computed)))
            result[symbol]=computed
    return result


def attach(signals,as_of):
    with connect() as conn:
        ready=conn.execute('SELECT 1 FROM financial_file_revision LIMIT 1').fetchone()
    if not ready:return signals
    by_symbol=metrics_many({s['symbol'] for s in signals},as_of)
    return [{**s,**by_symbol[s['symbol']]} for s in signals]
