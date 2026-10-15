from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pydantic import BaseModel, Field, model_validator


class ScreenRequest(BaseModel):
    as_of: date | None = None
    symbols: list[str] = Field(default_factory=list, max_length=10000)
    taxonomy_id: str | None = None
    adjustment: str = 'qfq'
    min_amount: float = Field(default=0, ge=0)
    min_volume_ratio: float = Field(default=0, ge=0)
    min_turnover: float | None = Field(default=None, ge=0)
    max_turnover: float | None = Field(default=None, ge=0)
    require_uptrend: bool = False
    require_breakout: bool = False
    breakout_days: int = Field(default=20, ge=5, le=120)
    limit: int = Field(default=100, ge=1, le=1000)

    @model_validator(mode='after')
    def valid_ranges(self):
        if self.adjustment not in ('raw','qfq'):
            raise ValueError('adjustment必须为raw或qfq')
        if self.min_turnover is not None and self.max_turnover is not None and self.min_turnover > self.max_turnover:
            raise ValueError('最低换手率不能大于最高换手率')
        return self


def adjust_bars(bars, events, anchor):
    """Ordinary TDX affine QFQ; raw volume/amount remain unchanged."""
    ordered = sorted((e for e in events if e['category']==1 and e['event_date']<=anchor),
                     key=lambda e:e['event_date'], reverse=True)
    a,b = Decimal(1),Decimal(0)
    i, result = 0,[]
    for source in reversed(bars):
        row = dict(source)
        while i < len(ordered) and ordered[i]['event_date'] > row['trade_date']:
            e = ordered[i]
            div,price,bonus,rights = (Decimal(str(e[k])) for k in ('c1','c2','c3','c4'))
            divisor = (Decimal(10)+bonus+rights)/10
            if divisor <= 0:
                raise ValueError('不支持的权息事件：复权分母非正')
            a /= divisor
            b -= a*(div-rights*price)/10
            i += 1
        for field in ('open','high','low','close'):
            row[field] = float((Decimal(str(row[field]))*a+b).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))
        result.append(row)
    return list(reversed(result))


def metrics(bars, window=20):
    if len(bars) < max(60,window+1):
        return None
    last = bars[-1]
    average = lambda field,n: sum(float(x[field]) for x in bars[-n:])/n
    prior_vol = sum(x['volume'] for x in bars[-6:-1])/5
    previous_high = max(float(x['high']) for x in bars[-window-1:-1])
    return {'close':float(last['close']), 'ma5':average('close',5), 'ma20':average('close',20),
            'ma60':average('close',60), 'volume_ratio_5d': last['volume']/prior_vol if prior_vol else None,
            'amount':last['amount'], 'volume_shares':last['volume'],
            'previous_high':previous_high, 'breakout':float(last['close'])>previous_high,
            'turnover_pct':last.get('turnover_pct'), 'turnover_source':last.get('turnover_source')}


def reasons_for(m, request):
    reasons = []
    if m['amount'] < request.min_amount:
        return None
    reasons.append(f'成交额 {m["amount"]/1e8:.2f} 亿元')
    if request.min_volume_ratio:
        if m['volume_ratio_5d'] is None or m['volume_ratio_5d'] < request.min_volume_ratio:
            return None
        reasons.append(f'成交量为此前5日均量的 {m["volume_ratio_5d"]:.2f} 倍')
    if request.require_uptrend:
        if not m['close'] > m['ma5'] > m['ma20'] > m['ma60']:
            return None
        reasons.append('收盘价 > MA5 > MA20 > MA60')
    if request.require_breakout:
        if not m['breakout']:
            return None
        reasons.append(f'收盘突破此前{request.breakout_days}个交易日最高价')
    for bound,lower in [(request.min_turnover,True),(request.max_turnover,False)]:
        if bound is not None:
            v = m['turnover_pct']
            if v is None or (v<bound if lower else v>bound):
                return None
    if request.min_turnover is not None or request.max_turnover is not None:
        reasons.append(f'换手率 {m["turnover_pct"]:.2f}%')
    return reasons
