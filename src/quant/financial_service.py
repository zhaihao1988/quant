from .db import connect
from .services import clean
from .tdx.financial import decode_fields
from .financial_fields import FIELDS,company_profile,OFFICIAL,MAPPING_VERSION


def profile(symbol):
    with connect() as conn:
        rows=conn.execute('''SELECT DISTINCT t.name,m.snapshot_date FROM membership m JOIN taxonomy t ON t.id=m.taxonomy_id
          WHERE m.symbol=%s AND t.kind='industry' AND m.snapshot_date=(SELECT max(snapshot_date) FROM membership WHERE symbol=%s)''',(symbol,symbol)).fetchall()
    names=sorted({r['name'] for r in rows})
    return clean({'type':company_profile(names),'industry_names':names,'snapshot_date':max((r['snapshot_date'] for r in rows),default=None),
                  'basis':'当前通达信行业快照，仅用于展示模板，不冒充历史企业类型'})


def financial_status():
    with connect() as conn:
        result=conn.execute('''SELECT (SELECT count(*) FROM financial_file_revision) AS file_versions,
          (SELECT count(*) FROM financial_report) AS stored_report_versions,
          (SELECT count(*) FROM financial_current_report) AS current_reports,
          (SELECT count(DISTINCT symbol) FROM financial_current_report WHERE symbol IS NOT NULL) AS covered_securities,
          (SELECT max(report_date) FROM financial_current_report) AS latest_report_date,
          (SELECT count(*) FROM financial_current_report WHERE announced_on IS NULL) AS missing_announcement,
          (SELECT count(*) FROM financial_current_report WHERE review_state='pending_conflict') AS quarantined_source_records,
          (SELECT count(*) FROM financial_current_report WHERE symbol IS NULL) AS unmapped_records''').fetchone()
        result['imports']=conn.execute('SELECT * FROM financial_import_run ORDER BY started_at DESC LIMIT 5').fetchall()
        result['missing_securities']=conn.execute('''SELECT symbol,name FROM security s WHERE NOT EXISTS
          (SELECT 1 FROM financial_current_report f WHERE f.symbol=s.symbol) ORDER BY symbol LIMIT 100''').fetchall()
    return clean({**result,'mapping_version':MAPPING_VERSION,'dictionary_fields':len(FIELDS),
      'note':'完整原始编号与float32载荷已保存；未知字段不猜名称。空报告包不代表已有财报。当前下载版可能包含历史修订。'})


def reports(symbol,limit=12,field_ids=None):
    if not 1<=limit<=200:raise ValueError('财务报告条数须为1至200')
    requested=field_ids or []
    if any(not 1<=code<=10000 for code in requested):raise ValueError('财务字段编号越界')
    with connect() as conn:
        rows=conn.execute('''SELECT id,source_ordinal,report_date,announced_on,observed_at,field_count,standard,mapping_version,review_state,
           payload FROM financial_current_report WHERE symbol=%s ORDER BY report_date DESC,source_ordinal LIMIT %s''',(symbol,limit)).fetchall()
    template=profile(symbol)
    for row in rows:
        payload=row.pop('payload')
        if row['review_state']!='verified':row['standard']={}
        if requested:
            values=decode_fields(payload);fields=[]
            for code in requested:
                name,unit,applicability=FIELDS.get(code,(f'FN{code}','未确认','unknown'))
                value=values.get(code)
                fields.append({'id':code,'name':name,'raw_unit':unit,'raw_value':value,
                  'state':'field_absent' if code not in values else 'missing' if value is None else 'available',
                  'applicability':applicability})
            row['fields']=fields
    return clean({'symbol':symbol,'profile':template,'reports':rows,'field_reference':OFFICIAL,
       'note':'金额按各字段单位解释；利润和现金流核心字段为年初累计。零值原样保留，不据此推断行业适用性；当前包不是首次披露历史快照。'})
