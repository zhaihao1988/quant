from datetime import date,timedelta
import struct
import pytest
from quant.domain import ScreenRequest,adjust_bars,metrics,reasons_for
from quant.tdx.readers import day_records,is_a_stock


def test_day_units_and_truncated_download():
    data=struct.pack('<IIIIIfII',20261009,1000,1200,900,1100,123456.0,345600,0)
    row=list(day_records(data))[0]
    assert row[1:]==(10,12,9,11,345600,123456)
    with pytest.raises(ValueError):list(day_records(data[:-1]))
    with pytest.raises(ValueError):list(day_records(data+data))


def test_qfq_mixed_actions_chronology_and_anchor():
    bars=[{'trade_date':date(2020,1,1),'open':20,'high':20,'low':20,'close':20,'volume':100,'amount':2000},
          {'trade_date':date(2020,2,1),'open':15,'high':15,'low':15,'close':15,'volume':100,'amount':1500}]
    events=[{'event_date':date(2020,1,15),'category':1,'c1':10,'c2':0,'c3':0,'c4':0},
            {'event_date':date(2020,2,15),'category':1,'c1':0,'c2':0,'c3':10,'c4':0}]
    adjusted=adjust_bars(bars,events,date(2020,2,1))
    assert adjusted[0]['close']==19 and adjusted[1]['close']==15
    later=adjust_bars(bars,events,date(2020,3,1))
    assert later[0]['close']==9.5 and later[1]['close']==7.5
    assert later[0]['volume']==100 and later[0]['amount']==2000
    assert bars[0]['close']==20


def test_breakout_uses_prior_bars_and_volume_excludes_today():
    series=[{'trade_date':date(2020,1,1)+timedelta(days=i),'close':10,'high':11,'volume':100,'amount':1000} for i in range(60)]
    series[-1].update(close=12,high=13,volume=300)
    m=metrics(series)
    assert m['breakout'] and m['previous_high']==11 and m['volume_ratio_5d']==3
    assert reasons_for(m,ScreenRequest(require_breakout=True,min_volume_ratio=2))
    assert reasons_for(m,ScreenRequest(min_turnover=1)) is None
    assert metrics(series[:10]) is None


def test_scope_and_validation():
    assert is_a_stock('SH','601318') and is_a_stock('SZ','300138') and is_a_stock('BJ','920001')
    assert not is_a_stock('SH','000001') and not is_a_stock('SH','510300')
    with pytest.raises(ValueError):ScreenRequest(min_turnover=5,max_turnover=1)
