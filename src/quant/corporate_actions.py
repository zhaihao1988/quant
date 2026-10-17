"""TDX price-event policy, checked against client closes on 2026-10-17.

1: ordinary ex-right/ex-dividend; 11: expand/shrink (price / c3).
12 and 15 remain capital metadata: no additional price transform.
The latter must not be converted into an ordinary bonus-share event.
"""
import math

NAMES={11:'扩缩股',12:'非流通股缩股',15:'重整调整'}


def pending_actions(events,start,anchor):
    result=[]
    price_dates={e['event_date'] for e in events if e['category']==1}
    for event in events:
        if not start<event['event_date']<=anchor:continue
        category=event['category']
        unsupported=category not in range(1,16)
        if category==11:
            try:ratio=float(event['c3'])
            except (TypeError,ValueError,KeyError):ratio=float('nan')
            # No verified same-date ordering for combined ordinary + shrink events.
            unsupported=not math.isfinite(ratio) or ratio<=0 or event['event_date'] in price_dates
        if unsupported:result.append(event)
    return result


def pending_message(events):
    labels='；'.join(f"类别{e['category']}（{NAMES.get(e['category'],'未知事件')}），{e['event_date']}" for e in events)
    return '前复权待核验：'+labels+'。原始行情保留；该股不以未核验价格参与技术选股，重复重试不会解决。'
