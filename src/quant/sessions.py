from datetime import datetime,time,timedelta
from zoneinfo import ZoneInfo


def china_now():
    return datetime.now(ZoneInfo('Asia/Shanghai'))


def choose_session(coverage, requested=None, now=None):
    """Default to a closed, sufficiently covered A-share daily session.

    This is a data coverage policy, not an exchange holiday calendar.
    Explicit historical dates remain selectable and expose their completeness.
    """
    now=now or china_now()
    cutoff=requested or now.date()
    rows=sorted((r for r in coverage if r['trade_date']<=cutoff),key=lambda r:r['trade_date'],reverse=True)
    if not rows:return None
    baseline=max(r['securities'] for r in rows)
    def annotate(row):
        ratio=row['securities']/baseline if baseline else 0
        intraday=row['trade_date']==now.date() and now.time()<time(15,45)
        return {**row,'baseline_securities':baseline,'coverage_ratio':ratio,
                'partial':intraday or ratio<0.8,'intraday':intraday}
    if requested:
        return annotate(rows[0])
    return next((r for r in map(annotate,rows) if not r['partial']),None)


def market_session(conn, requested=None):
    cutoff=requested or china_now().date()
    latest=conn.execute('SELECT max(trade_date) AS d FROM daily_bar WHERE trade_date<=%s',(cutoff,)).fetchone()['d']
    if not latest:return None
    rows=conn.execute('SELECT trade_date,count(*) AS securities FROM daily_bar WHERE trade_date BETWEEN %s AND %s GROUP BY trade_date ORDER BY trade_date DESC',(latest-timedelta(days=45),latest)).fetchall()
    return choose_session(rows,requested)
