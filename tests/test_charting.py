from datetime import date,timedelta
import pytest
from quant.charting import aggregate,indicators,period_bounds


def bar(dt,op,hi,lo,cl,volume=100,turnover=1):
    return {'trade_date':dt,'open':op,'high':hi,'low':lo,'close':cl,'volume':volume,
            'amount':volume*cl,'turnover_pct':turnover}


def test_week_month_ohlc_and_missing_turnover():
    rows=[bar('2025-12-31',10,13,9,12),bar('2026-01-02',12,15,11,14,200),bar('2026-01-05',14,16,13,15,300,None)]
    weeks=aggregate(rows,'weekly',date(2026,1,6))
    assert len(weeks)==2
    assert [weeks[0][k] for k in ('open','high','low','close','volume','amount')]==[10,15,9,14,300,4000]
    assert weeks[0]['trade_date']=='2026-01-02' and not weeks[0]['is_partial']
    assert weeks[0]['turnover_pct']==2
    assert weeks[1]['is_partial'] and weeks[1]['turnover_pct'] is None
    months=aggregate(rows,'monthly',date(2026,1,6))
    assert len(months)==2 and months[1]['volume']==500 and months[1]['turnover_pct'] is None
    assert months[1]['period_end']=='2026-01-31'
    assert period_bounds(date(2025,12,31),'weekly')==(date(2025,12,29),date(2026,1,2))


def test_macd_recursive_seed_and_factor_two():
    rows=[bar(f'2020-01-0{i+1}',p,p,p,p) for i,p in enumerate([10,12,11])]
    out=indicators(rows,(2,),2,3,2)
    assert [r['dif'] for r in out]==pytest.approx([0,1/3,1/9])
    assert [r['dea'] for r in out]==pytest.approx([0,2/9,4/27])
    assert [r['macd'] for r in out]==pytest.approx([0,2/9,-2/27])
    assert out[0]['ma']['2'] is None and out[2]['ma']['2']==11.5
    with pytest.raises(ValueError):indicators(rows,(),2,3,2)
    with pytest.raises(ValueError):indicators(rows,(2,),26,12,9)


def test_market_range_does_not_reset_indicators(monkeypatch):
    from quant.charting import market_data
    from quant import services
    rows=[bar((date(2020,1,1)+timedelta(days=i)).isoformat(),10+i,12+i,9+i,11+i) for i in range(100)]
    monkeypatch.setattr(services,'bars',lambda *a,**k:{'bars':rows,'anchor':'2020-04-09','truncated':False})
    full=market_data('test',timeframe='weekly')
    tail=market_data('test',start=date(2020,4,1),timeframe='weekly')
    expected={r['trade_date']:r for r in full['bars']}
    assert tail['bars'] and len(tail['bars'])<len(full['bars'])
    for row in tail['bars']:
        assert row['ma']==expected[row['trade_date']]['ma']
        assert row['dif']==expected[row['trade_date']]['dif']
        assert row['volume']==expected[row['trade_date']]['volume']
