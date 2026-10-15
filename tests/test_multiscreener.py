import ast
from dataclasses import asdict
from datetime import date,timedelta
import hashlib
import importlib
import logging
from pathlib import Path
import sys
import types

import numpy as np
import pandas as pd
import pytest
from quant.multiscreener import (REGISTRY,MultiScreenRequest,legacy_periods,
    evaluate_stock,load_strategy_data,json_safe)
from quant.legacy_strategies.base_strategy import StrategyContext,StrategyResult

ROOT=Path(__file__).resolve().parents[1]
REFERENCE=ROOT/'reference/multiscreener'


def fixture_data(n=850):
    dates=pd.bdate_range('2020-01-01',periods=n)
    x=np.arange(n)
    close=10+np.sin(x/13)*2+x/400
    frame=pd.DataFrame({'date':dates.date,'open':close-.1,'high':close+.3,'low':close-.3,
                        'close':close,'volume':1000+(x%23)*50,'turnover':1.,'amount':close*100000,'symbol':'300138.SZ'})
    return {'daily':frame,'weekly':legacy_periods(frame,dates[-1].date(),'weekly'),
            'monthly':legacy_periods(frame,dates[-1].date(),'monthly')}


def original_classes():
    package=types.ModuleType('original_strategy_baseline')
    package.__path__=[str(REFERENCE/'strategies')]
    sys.modules[package.__name__]=package
    base=types.ModuleType(package.__name__+'.base_strategy')
    sys.modules[base.__name__]=base
    source=(REFERENCE/'strategies/base_strategy.py').read_text(encoding='utf-8')
    source=source.replace('from sqlalchemy.orm import Session # 用于 StrategyContext','from typing import Any\nSession = Any')
    source=source.replace('    notes: Optional[str] = None','    notes: Optional[str] = None\n    timeframe: Optional[str] = None')
    exec(compile(source,str(REFERENCE/'strategies/base_strategy.py'),'exec'),base.__dict__)
    return [(getattr(importlib.import_module(package.__name__+'.'+cls.__module__.split('.')[-1]),cls.__name__),label) for cls,label in REGISTRY]


def test_seven_algorithm_sources_byte_identical():
    import json
    manifest=json.loads((REFERENCE/'manifest.json').read_text(encoding='utf-8'))
    for cls,_ in REGISTRY:
        relative='strategies/'+cls.__module__.split('.')[-1]+'.py'
        expected=manifest['files'][relative]
        assert hashlib.sha256((REFERENCE/relative).read_bytes()).hexdigest()==expected
        assert hashlib.sha256(Path(importlib.import_module(cls.__module__).__file__).read_bytes()).hexdigest()==expected


@pytest.mark.parametrize('n',[8,90,850])
def test_original_strategy_output_parity(n):
    data=fixture_data(n);cutoff=data['daily']['date'].iloc[-1]
    for (new,_),(old,_) in zip(REGISTRY,original_classes()):
        def invoke(cls):
            try:
                result=cls(StrategyContext(None,cutoff,{})).run_for_stock('300138.SZ',cutoff,{k:v.copy(deep=True) for k,v in data.items()})
                return {'signals':json_safe([asdict(s) for s in result])}
            except Exception as exc:
                return {'error':type(exc).__name__+': '+str(exc)}
        assert invoke(new)==invoke(old),new.__name__


@pytest.mark.parametrize('tf',['weekly','monthly'])
def test_adapter_matches_original_aggregator(tf):
    # Execute original pure helpers, isolating them from old ORM imports.
    import typing
    tree=ast.parse((REFERENCE/'data_processing/aggregators.py').read_text(encoding='utf-8'))
    names={'_aggregate_single_period_dynamically','_resample_completed_periods'}
    nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
    scope={'pd':pd,'date':date,'logger':logging.getLogger('test'),**vars(typing)}
    exec(compile(ast.Module(nodes,type_ignores=[]),'original-aggregators','exec'),scope)
    daily=fixture_data(850)['daily']
    for cutoff in (date(2023,1,4),date(2022,12,30),date(2022,1,3)):
        frame=daily[pd.to_datetime(daily.date).dt.date<=cutoff].copy()
        frame['date']=pd.to_datetime(frame['date']);frame=frame.set_index('date',drop=False)
        boundary=cutoff-timedelta(days=cutoff.weekday()) if tf=='weekly' else cutoff.replace(day=1)
        columns=['symbol','date','open','high','low','close','volume','turnover','amount']
        rows=scope['_resample_completed_periods'](frame,tf,'300138.SZ',columns,boundary)
        current=frame[frame['date'].dt.date>=boundary]
        dynamic=scope['_aggregate_single_period_dynamically'](current,'300138.SZ',cutoff,tf,columns)
        if dynamic:rows.append(dynamic)
        expected=pd.DataFrame(rows)[columns]
        actual=legacy_periods(daily,cutoff,tf)[columns]
        pd.testing.assert_frame_equal(actual,expected,check_dtype=False)


def test_positive_cross_and_original_same_day_filter():
    data=fixture_data(100)
    df=data['daily']
    for col in ('open','high','low','close'):df[col]=10.
    df.loc[df.index[-1],['open','high','low','close','volume']]=[10,13,9.9,12,5000]
    cutoff=df.date.iloc[-1]
    selected=['RefactoredMultiLevelCrossStrategy']
    signals,errors,checks=evaluate_stock('300138.SZ','晨光生物',cutoff,data,selected)
    assert not errors
    assert signals and all(s['signal_date']==cutoff.isoformat() for s in signals)
    assert signals[0]['details']['level']=='daily' and signals[0]['details']['volume']=='5000'
    assert signals[0]['fundamental_status']=='not_connected'
    old=original_classes()[1][0](StrategyContext(None,cutoff,{})).run_for_stock('300138.SZ',cutoff,data)
    assert [s['details'] for s in signals]==[json_safe(s.details) for s in old if s.signal_date==cutoff and s.signal_type!='NO_SIGNAL']


def test_adapter_volume_and_no_display_window_reset(monkeypatch):
    data=fixture_data(850)['daily'];cutoff=data.date.iloc[-1]
    raw=data.rename(columns={'date':'trade_date','turnover':'turnover_pct'}).to_dict('records')
    for r in raw:r['volume']*=100
    calls=[]
    def mock_bars(symbol,**kwargs):
        calls.append(kwargs)
        return {'bars':raw,'truncated':False}
    monkeypatch.setattr('quant.services.bars',mock_bars)
    result,_=load_strategy_data('300138.SZ',cutoff)
    assert len(result['daily'])==850 and len(result['monthly'])>=30
    assert result['daily'].volume.iloc[-1]==data.volume.iloc[-1]
    assert calls==[{'end':cutoff,'limit':20000,'adjustment':'qfq'}]
    assert StrategyResult('300138.SZ',cutoff,'test',timeframe='monthly').timeframe=='monthly'
    with pytest.raises(ValueError):MultiScreenRequest(strategies=[])


def test_strategy_error_visible_and_other_strategies_continue():
    data=fixture_data(5)
    data['monthly']=pd.DataFrame({'date':[date(2020,1,1)]*40})
    signals,errors,evaluations=evaluate_stock('300138.SZ','测试',date(2020,1,1),data,[c.__name__ for c,_ in REGISTRY])
    assert errors and len(evaluations)==7
