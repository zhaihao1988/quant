"""Pure daily/weekly/monthly aggregation and TDX-style indicator math."""
import calendar
from datetime import date,timedelta,time
from .domain import price_changes
from .sessions import china_now


def day(value):
    return value if isinstance(value,date) else date.fromisoformat(value)


def period_bounds(dt,timeframe):
    if timeframe=='daily':return dt,dt
    if timeframe=='weekly':
        start=dt-timedelta(days=dt.weekday())
        return start,start+timedelta(days=4)
    if timeframe=='monthly':return dt.replace(day=1),dt.replace(day=calendar.monthrange(dt.year,dt.month)[1])
    raise ValueError('timeframe必须为daily、weekly或monthly')


def aggregate(rows,timeframe,as_of):
    groups={};as_of=day(as_of)
    for source in rows:
        row=dict(source);dt=day(row['trade_date'])
        if dt>as_of:continue
        start,end=period_bounds(dt,timeframe)
        group=groups.setdefault(start,{'rows':[],'end':end})
        group['rows'].append(row)
    result=[];previous=None
    for start,group in sorted(groups.items()):
        items=sorted(group['rows'],key=lambda r:r['trade_date'])
        first,last=items[0],items[-1]
        known_turnover=[r['turnover_pct'] for r in items if r.get('turnover_pct') is not None]
        dt=day(last['trade_date']);now=china_now()
        partial=group['end']>as_of or (dt==now.date() and now.time()<time(15,45))
        row={**last,'trade_date':dt.isoformat(),'first_trade_date':first['trade_date'],
             'period_start':start.isoformat(),'period_end':group['end'].isoformat(),
             'is_partial':partial,'trading_days':len(items),
             'open':float(first['open']),'close':float(last['close']),
             'high':max(float(r['high']) for r in items),'low':min(float(r['low']) for r in items),
             'volume':sum(r['volume'] for r in items),'amount':sum(r['amount'] for r in items),
             'turnover_pct':sum(known_turnover) if len(known_turnover)==len(items) else None,
             'turnover_known_days':len(known_turnover),
             'turnover_source':last.get('turnover_source') if timeframe=='daily' else 'sum_daily_estimates'}
        # Daily changes may have a previous close outside a requested display window.
        if timeframe!='daily':row.update(price_changes(row,previous))
        result.append(row);previous=row
    return result


def moving_average(values,period):
    if period<1:raise ValueError('均线周期必须为正整数')
    total=0.0;result=[]
    for i,value in enumerate(values):
        total+=float(value)
        if i>=period:total-=float(values[i-period])
        result.append(total/period if i+1>=period else None)
    return result


def ema(values,period):
    result=[];previous=None;alpha=2/(period+1)
    for value in values:
        previous=float(value) if previous is None else alpha*float(value)+(1-alpha)*previous
        result.append(previous)
    return result


def indicators(rows,ma_periods=(5,10,20,30,60,120,250),fast=12,slow=26,signal=9):
    if not 1<=fast<slow<=300 or not 1<=signal<=300:
        raise ValueError('MACD参数须满足1≤快线<慢线≤300，1≤信号线≤300')
    if not 1<=len(ma_periods)<=8 or any(not 1<=p<=500 for p in ma_periods):
        raise ValueError('支持1至8条均线，周期范围1至500')
    close=[r['close'] for r in rows];volume=[r['volume'] for r in rows]
    averages={p:moving_average(close,p) for p in dict.fromkeys(ma_periods)}
    fast_ema,slow_ema=ema(close,fast),ema(close,slow)
    dif=[f-s for f,s in zip(fast_ema,slow_ema)];dea=ema(dif,signal)
    v5,v10=moving_average(volume,5),moving_average(volume,10)
    return [{**row,'ma':{str(p):vs[i] for p,vs in averages.items()},
             'dif':dif[i],'dea':dea[i],'macd':2*(dif[i]-dea[i]),
             'volume_ma5':v5[i],'volume_ma10':v10[i]} for i,row in enumerate(rows)]


def market_data(symbol,end=None,start=None,limit=20000,adjustment='qfq',timeframe='daily',
                ma_periods=(5,10,20,30,60,120,250),fast=12,slow=26,signal=9):
    from .services import bars
    if not 1<=limit<=20000:raise ValueError('limit须为1至20000')
    period_bounds(date.today(),timeframe)
    # Calculate from all available preceding history. Display range never resets EMA.
    source=bars(symbol,end,20000,adjustment)
    if not source['bars']:
        return {**source,'timeframe':timeframe,'indicator_parameters':{'ma':ma_periods,'macd':[fast,slow,signal]}}
    if start and end and day(start)>day(end):
        raise ValueError('起始日不能晚于截止日')
    calculated=indicators(aggregate(source['bars'],timeframe,source.get('as_of',source['anchor'])),ma_periods,fast,slow,signal)
    selected=[r for r in calculated if not start or day(r['trade_date'])>=day(start)]
    available_periods=len(calculated);range_periods=len(selected)
    selected=selected[-limit:]
    return {**source,'bars':selected,'timeframe':timeframe,'available_periods':available_periods,
            'range_rows':range_periods,'returned_rows':len(selected),
            'truncated':source['truncated'] or range_periods>len(selected),
            'indicator_parameters':{'ma':list(ma_periods),'macd':[fast,slow,signal],'ema_seed':'first_close'},
            'period_note':'先按统一基准复权日线，再聚合周/月OHLC与量额，再计算该周期指标；不补停牌日。周期结束标记使用日历边界，不含完整休市日历。',
            'turnover_note':'周/月换手率为每日估算换手率之和；任一天缺股本依据则整期留空。',
            'view_start':selected[0]['trade_date'] if selected else None,
            'view_end':selected[-1]['trade_date'] if selected else None}
