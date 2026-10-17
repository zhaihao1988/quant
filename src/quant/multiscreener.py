"""Adapters for the user's unchanged MultiStrategyScreener technical algorithms."""
import math
from dataclasses import asdict
from datetime import date

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, model_validator

from .legacy_strategies.base_strategy import StrategyContext
from .legacy_strategies.ma_pullback_strategy import AdaptedMAPullbackStrategy
from .legacy_strategies.multi_level_cross_refactored_strategy import RefactoredMultiLevelCrossStrategy
from .legacy_strategies.breakout_strategy import BreakoutStrategy
from .legacy_strategies.weekly_ma_pullback_strategy import WeeklyMAPullbackStrategy
from .legacy_strategies.monthly_ma_pullback_strategy import MonthlyMAPullbackStrategy
from .legacy_strategies.weekly_breakout_strategy import WeeklyBreakoutStrategy
from .legacy_strategies.monthly_breakout_strategy import MonthlyBreakoutStrategy

# Original CONFIG order. Adding an implementation here does not change existing strategies.
REGISTRY = [
    (AdaptedMAPullbackStrategy, '日线均线回调'),
    (RefactoredMultiLevelCrossStrategy, '多级别一阳穿四线'),
    (BreakoutStrategy, '日线突破'),
    (WeeklyMAPullbackStrategy, '周线均线回调'),
    (MonthlyMAPullbackStrategy, '月线均线回调'),
    (WeeklyBreakoutStrategy, '周线突破'),
    (MonthlyBreakoutStrategy, '月线突破'),
]


class MultiScreenRequest(BaseModel):
    as_of: date | None = None
    symbols: list[str] = Field(default_factory=list, max_length=10000)
    taxonomy_id: str | None = None
    workers: int = Field(default=4, ge=1, le=4)
    strategies: list[str] = Field(default_factory=lambda: [cls.__name__ for cls, _ in REGISTRY])

    @model_validator(mode='after')
    def validate_scope(self):
        known={cls.__name__ for cls,_ in REGISTRY}
        if not self.strategies or len(set(self.strategies))!=len(self.strategies) or set(self.strategies)-known:
            raise ValueError('请选择有效且不重复的技术策略')
        return self


def json_safe(value):
    if isinstance(value, dict):return {str(k):json_safe(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [json_safe(v) for v in value]
    if isinstance(value,np.generic):return json_safe(value.item())
    if isinstance(value,(date,pd.Timestamp)):return value.isoformat()
    if isinstance(value,float) and not math.isfinite(value):return None
    return value


def strategy_catalog():
    result=[]
    for cls,label in REGISTRY:
        instance=cls(StrategyContext(None,date.today(),{}))
        parameters=getattr(instance,'params',None)
        if parameters is None:
            parameters={key:getattr(instance,key) for key in ('ma_list_map','check_ma30_daily','check_volume_daily','check_volume_weekly','check_ma30_trend_weekly') if hasattr(instance,key)}
        result.append({'id':cls.__name__,'name':label,'strategy_name':instance.strategy_name,
                       'parameters':json_safe(parameters)})
    return {'strategies':result,'lookback':'all_history','adjustment':'qfq','volume_unit':'手',
            'fundamental_status':'not_connected','source_commit':'e7dcdb2819ede83220f423ed7563acd1e1fdc63f'}


def legacy_periods(daily, as_of, timeframe):
    """Old aggregator: historical W-FRI/BME labels and current dynamic processing date.

    Keep this separate from the chart's actual-last-trade-day labels. The
    MultiLevelCross implementation retains its OWN resampling, unmodified.
    """
    if daily.empty:return daily.copy()
    frame=daily.copy()
    frame['date']=pd.to_datetime(frame['date'])
    cutoff=pd.Timestamp(as_of)
    frame=frame[frame['date']<=cutoff].set_index('date')
    boundary=cutoff-pd.Timedelta(days=as_of.weekday()) if timeframe=='weekly' else cutoff.replace(day=1)
    rule='W-FRI' if timeframe=='weekly' else 'BME'
    agg={'open':'first','high':'max','low':'min','close':'last','volume':'sum','turnover':'sum','amount':'sum'}
    past=frame[frame.index<boundary].resample(rule).agg(agg).dropna(subset=['open','close'])
    past=past[past.index<boundary]
    current=frame[(frame.index>=boundary)&(frame.index<=cutoff)]
    if not current.empty:
        current_values={k:getattr(current[k],op)() if op not in ('first','last') else current[k].iloc[0 if op=='first' else -1] for k,op in agg.items()}
        past=pd.concat([past,pd.DataFrame([current_values],index=pd.DatetimeIndex([cutoff],name='date'))])
    past=past.reset_index()
    past['date']=past['date'].dt.date
    if 'symbol' in daily and not daily.empty:past['symbol']=daily['symbol'].iloc[0]
    return past.reset_index(drop=True)


def load_strategy_data(symbol, as_of):
    from .services import bars
    result=bars(symbol,end=as_of,limit=20000,adjustment='qfq')
    if result['truncated']:raise ValueError('历史超过读取上限，不能将截断数据当作全部历史')
    daily=pd.DataFrame(result['bars'])
    if daily.empty:return {},result
    daily=daily.rename(columns={'trade_date':'date','turnover_pct':'turnover'})
    daily['date']=pd.to_datetime(daily['date']).dt.date
    daily=daily.sort_values('date').reset_index(drop=True)
    daily['volume']=daily['volume']/100  # DB shares -> legacy lots. Amount stays yuan.
    daily['symbol']=symbol
    return {'daily':daily,'weekly':legacy_periods(daily,as_of,'weekly'),
            'monthly':legacy_periods(daily,as_of,'monthly')},result


def evaluate_stock(symbol, name, as_of, data, selected):
    """Preserve per-strategy invocation and original same-date/NO_SIGNAL filter."""
    signals=[];errors=[];evaluations=[]
    for cls,label in REGISTRY:
        if cls.__name__ not in selected:continue
        try:
            instance=cls(StrategyContext(None,as_of,{}))
            monthly=data.get('monthly')
            if cls.__name__=='MonthlyMAPullbackStrategy' and monthly is not None and not monthly.empty and not any(pd.to_datetime(monthly['date']).dt.date==as_of):
                evaluations.append({'strategy_id':cls.__name__,'status':'skipped','reason':'缺当前月行情；未使用旧月K线代替筛选日','signals':0,'history_warnings':[]})
                continue
            raw=instance.run_for_stock(symbol=symbol,current_date=as_of,data=data)
            accepted=[sig for sig in (raw or []) if sig.signal_date==as_of and sig.signal_type!='NO_SIGNAL']
            for sig in accepted:
                row=asdict(sig)
                level='Daily'
                if sig.strategy_name in ('WeeklyMAPullbackStrategy','WeeklyChanBreakoutStrategyV1'):level='Weekly'
                elif sig.strategy_name in ('MonthlyMAPullbackStrategy','MonthlyChanBreakoutStrategyV1'):level='Monthly'
                row.update(name=name,strategy_id=cls.__name__,strategy_label=label,signal_level=level,
                           fundamental_status='not_connected')
                signals.append(json_safe(row))
            warnings=[]
            p=getattr(instance,'params',{})
            # Diagnostic only: the unchanged strategy itself decides whether to return.
            guards={
                'AdaptedMAPullbackStrategy':('daily',max(p.get('ma_long',0),p.get('ma_long_for_peak_qualify_period',0),p.get('peak_recent_gain_days',0))+p.get('fractal_definition_lookback',1)*2+5),
                'WeeklyMAPullbackStrategy':('weekly',max(p.get('weekly_ma_long',0),p.get('weekly_ma_peak_qualify_period',0),p.get('weekly_peak_recent_gain_periods',0),p.get('weekly_fractal_definition_lookback',1)*2+1)),
                'MonthlyMAPullbackStrategy':('monthly',max(p.get('monthly_ma_long',0),p.get('monthly_ma_peak_qualify_period',0),p.get('monthly_peak_recent_gain_periods',0),p.get('monthly_fractal_definition_lookback',1)*2+1)),
            }
            if cls.__name__ in guards:
                tf,minimum=guards[cls.__name__]
                available=len(data.get(tf,[]))
                if available<minimum:warnings.append(f'{tf}历史{available}根，少于原策略初始检查所需{minimum}根')
            evaluations.append({'strategy_id':cls.__name__,'signals':len(accepted),'status':'evaluated','history_warnings':warnings})
        except Exception as exc:
            # Keep scanning other strategies as in the original runner, expose errors separately.
            errors.append({'symbol':symbol,'strategy_id':cls.__name__,'error':f'{type(exc).__name__}: {exc}'})
            evaluations.append({'strategy_id':cls.__name__,'status':'error'})
    return signals,errors,evaluations
