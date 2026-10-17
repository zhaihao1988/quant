"""Presentation-only problem categories; never rewrite stored strategy results."""
from collections import Counter


def classify(message):
    text=str(message or '')
    if any(v in text for v in ('前复权待核验','扩缩股事件','不支持的权息','不支持的复权')):
        return 'corporate_action','复权待支持','整股技术选股未执行；需核验特殊事件复权口径',False
    if '无日线数据' in text:return 'missing_bars','缺少日线','补齐通达信本地日线后再同步重算',False
    if any(v in text for v in ('isoformat','报告期日期','日期格式')):
        return 'source_date','来源日期格式','核对原日期并规范格式后重试',False
    if any(v in text for v in ('HTTP 403','HTTP 429','证券代码不匹配')):
        return 'source_blocked','来源访问受限','稍后重试，连续受限时暂停采集',True
    if any(v.lower() in text.lower() for v in ('SSL','EOF','timed out','timeout','ConnectError','连接','超时','HTTP 500','HTTP 502','HTTP 503','HTTP 504')):
        return 'network','网络连接失败','自动有限重试；仍失败可稍后重试',True
    if any(v in text for v in ('未识别到有效正文','未提供经营评述全文')):
        return 'no_content','来源暂无全文','保留已有资料，后续再次核对',True
    if 'IndexError' in text or 'KeyError' in text:
        return 'strategy_runtime','策略运行异常','仅影响对应策略，需定位输入边界',False
    return 'other','其他问题','查看原始问题并核验',False


def collection_issue(row):
    key,label,action,retry=classify(row.get('error'))
    return {**row,'problem_category':key,'problem_label':label,'next_action':action,'retryable':retry}


def screen_diagnostics(result,effective):
    rows=[];coverage={c['symbol']:c for c in result.get('coverage',[])};errors=result.get('errors',[])
    whole=set();partial=set();warnings=set()
    for error in errors:
        symbol=error.get('symbol');strategy=error.get('strategy_id');text=error.get('error','')
        key,label,action,retry=classify(text)
        if symbol and not strategy:whole.add(symbol)
        elif symbol:partial.add(symbol)
        last=coverage.get(symbol,{}).get('last_date')
        if strategy=='MonthlyMAPullbackStrategy' and last and str(last)[:7]!=str(effective)[:7]:
            key,label,action,retry='missing_period','缺当前月行情','核对停牌或本地缺口；原策略条件保留，不能补造当月K线',False
        rows.append({**error,'category':key,'label':label,'action':action,'retryable':retry})
    for symbol,c in coverage.items():
        for ev in c.get('evaluations',[]):
            if ev.get('status')=='skipped':
                rows.append({'symbol':symbol,'strategy_id':ev.get('strategy_id'),'category':'missing_period','label':'缺当前月行情',
                  'error':ev.get('reason',''),'action':'本次跳过该策略，核对源行情日期','retryable':False})
            for warning in ev.get('history_warnings',[]):
                warnings.add(symbol)
                rows.append({'symbol':symbol,'strategy_id':ev.get('strategy_id'),'category':'history_short','label':'历史长度不足',
                  'error':warning,'action':'按原条件跳过；仅在历史确实缺失时补下载','retryable':False})
    counts=Counter(r['label'] for r in rows)
    return {'whole_stock_uncomputed':len(whole),'partial_strategy_error_stocks':len(partial-whole),
      'history_short_stocks':len(warnings),'counts':dict(counts),'rows':rows}
