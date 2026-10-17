"""Explicit quarantine for events whose price adjustment has not been verified."""
NAMES={11:'扩缩股',12:'非流通股缩股',15:'重整调整'}


def pending_actions(events,start,anchor):
    return [e for e in events if (e['category'] in (11,12) or e['category']>14) and start<e['event_date']<=anchor]


def pending_message(events):
    labels='；'.join(f"类别{e['category']}（{NAMES.get(e['category'],'未知事件')}），{e['event_date']}" for e in events)
    return '前复权待核验：'+labels+'。原始行情保留；该股不以未核验价格参与技术选股，重复重试不会解决。'
