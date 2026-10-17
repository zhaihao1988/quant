"""Verified annual calendars; unknown years are never guessed from weekdays."""
import json
from datetime import date,time,timedelta
from pathlib import Path
from . import sessions
from .db import connect


def year_days(year):
    with connect() as conn:custom=conn.execute('SELECT days,source FROM calendar_year WHERE year=%s',(year,)).fetchone()
    if custom:return custom['days'],custom['source']
    path=Path(__file__).parent/'data'/f'calendar_{year}.json'
    if not path.exists():return None,None
    record=json.loads(path.read_text(encoding='utf8'));closed=set()
    for start,end in record['closed_ranges']:
        day=date.fromisoformat(start);last=date.fromisoformat(end)
        while day<=last:closed.add(day);day+=timedelta(days=1)
    day=date(year,1,1);days={}
    while day.year==year:
        days[day.isoformat()]=day.weekday()<5 and day not in closed;day+=timedelta(days=1)
    return days,record['source']


def expected_trade_date(now=None):
    now=now or sessions.china_now();day=now.date()
    if now.time()<time(15,45):day-=timedelta(days=1)
    cache={}
    for _ in range(370):
        if day.year not in cache:cache[day.year]=year_days(day.year)
        days,_=cache[day.year]
        if days is None:return None
        if days.get(day.isoformat()):return day
        day-=timedelta(days=1)
    return None


def import_calendar(path):
    from psycopg.types.json import Jsonb
    data=json.loads(Path(path).read_text(encoding='utf8'));year=int(data['year']);days=data['days']
    day=date(year,1,1);expected=set()
    while day.year==year:expected.add(day.isoformat());day+=timedelta(days=1)
    if set(days)!=expected or not all(type(v) is bool for v in days.values()) or not data.get('source'):
        raise ValueError('交易日历必须包含该年每一天的布尔开市状态和来源')
    with connect() as conn:
        conn.execute('INSERT INTO calendar_year(year,source,days) VALUES (%s,%s,%s) ON CONFLICT(year) DO UPDATE SET source=EXCLUDED.source,days=EXCLUDED.days,observed_at=now()',
                     (year,data['source'],Jsonb(days)))
    return {'year':year,'days':len(days),'source':data['source']}
