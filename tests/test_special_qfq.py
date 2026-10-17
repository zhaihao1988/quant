from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import pytest
from quant.domain import adjust_bars
from quant.corporate_actions import pending_actions


def bar(day,price):
    return dict(trade_date=date.fromisoformat(day),open=price,high=price,low=price,close=price,volume=100,amount=1000)


def event(day,category,**values):
    return dict(event_date=date.fromisoformat(day),category=category,c1=values.get('c1',0),c2=values.get('c2',0),c3=values.get('c3',0),c4=values.get('c4',0))


def test_expand_shrink_with_dividend_and_bonus_in_both_orders():
    bars=[bar('2020-01-01',20),bar('2020-02-01',8),bar('2020-03-01',5)]
    events=[event('2020-01-15',11,c3=.5),event('2020-02-15',1,c1=10,c3=10)]
    original=deepcopy(bars)
    # Before Jan15: (20 / .5 - 1) / 2 = 19.5, not (20 - 1) / 2 / .5.
    adjusted=adjust_bars(bars,events,date(2020,3,1))
    assert [b['close'] for b in adjusted]==[19.5,3.5,5]
    assert adjusted[0]['volume']==100 and adjusted[0]['amount']==1000 and bars==original
    assert adjust_bars(bars[:1],events,date(2020,1,1))[0]['close']==20
    reversed_events=[event('2020-01-15',1,c1=10,c3=10),event('2020-02-15',11,c3=.5)]
    assert adjust_bars(bars,reversed_events,date(2020,3,1))[0]['close']==19


def test_event_day_already_uses_new_units_and_capital_metadata_not_applied_twice():
    bars=[bar('2020-01-01',20),bar('2020-01-15',40)]
    events=[event('2020-01-15',11,c3=.5),event('2020-01-10',12,c3=.58),event('2020-01-12',15,c3=15.69)]
    assert [b['close'] for b in adjust_bars(bars,events,date(2020,1,15))]==[40,40]


@pytest.mark.parametrize('ratio',[0,-1,float('nan'),float('inf')])
def test_bad_ratios_stay_quarantined(ratio):
    with pytest.raises(ValueError,match='前复权待核验'):
        adjust_bars([bar('2020-01-01',20)],[event('2020-01-15',11,c3=ratio)],date(2020,2,1))


def test_unknown_and_unverified_same_date_ordering_stay_quarantined():
    assert pending_actions([event('2020-01-15',16)],date(2020,1,1),date(2020,2,1))
    events=[event('2020-01-15',11,c3=.5),event('2020-01-15',1,c1=1)]
    assert pending_actions(events,date(2020,1,1),date(2020,2,1))


def test_three_actual_tdx_client_closes_supplied_by_user():
    samples=json.loads((Path(__file__).parent/'fixtures'/'special_qfq_client_samples.json').read_text(encoding='utf-8'))
    for sample in samples:
        events=[{**e,'event_date':date.fromisoformat(e['event_date'])} for e in sample['events']]
        result=adjust_bars([bar(sample['date'],sample['raw_close'])],events,date.fromisoformat(sample['anchor']))
        assert result[0]['close']==sample['expected'],sample['symbol']
