"""Immutable A-share capital snapshots; source report dates are not effective dates."""
from datetime import datetime,date,timezone
from decimal import Decimal,InvalidOperation
from pathlib import Path
import hashlib
from psycopg.types.json import Jsonb
from .config import settings
from .db import connect
from .tdx.readers import stable_bytes,dbf_records_bytes,is_a_stock

CAPITAL_LOCK=779933


def shares_value(value):
    if value is None or value=='':return None
    try:quantity=Decimal(value)*10000
    except InvalidOperation:raise ValueError('股本字段不是有效数字')
    if not quantity.is_finite() or quantity<0 or quantity!=quantity.to_integral_value() or quantity>9223372036854775807:
        raise ValueError('股本字段超出有效股数范围，未自动修正')
    return int(quantity)


def import_capital(path=None):
    path=Path(path) if path else settings().tdx_root/'T0002/hq_cache/base.dbf'
    stamp=path.stat();data,digest=stable_bytes(path)
    if (stamp.st_size,stamp.st_mtime_ns)!=(path.stat().st_size,path.stat().st_mtime_ns):
        raise ValueError('股本文件正在更新，请稍后重试')
    records=list(dbf_records_bytes(data));mapped={};market_map={'0':'SZ','1':'SH','2':'BJ'}
    for row in records:
        market=market_map.get(row.get('SC'));code=row.get('GPDM','')
        if not is_a_stock(market,code):continue
        symbol=f'{code}.{market}'
        if symbol in mapped and mapped[symbol]!=row:
            raise ValueError(f'{symbol}存在冲突股本记录，保留原版本，等待用户确认')
        mapped[symbol]=row
    if not mapped:raise ValueError('未读到完整A股股本资料，保留数据库原版本')
    with connect() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(%s)',(CAPITAL_LOCK,))
        old=conn.execute('SELECT id,sha256,record_count FROM capital_file_revision WHERE path=%s ORDER BY id DESC LIMIT 1',(str(path.resolve()),)).fetchone()
        if old and old['sha256']==digest:return {'skipped':True,'file_id':old['id'],'records':old['record_count']}
        if old and len(mapped)<old['record_count']:raise ValueError('A股股本记录减少，请等待下载完整后重试')
        if conn.execute('SELECT 1 FROM capital_file_revision WHERE path=%s AND sha256=%s',(str(path.resolve()),digest)).fetchone():
            raise ValueError('股本源文件回退旧版本，未自动采用')
        known={r['symbol'] for r in conn.execute('SELECT symbol FROM security')}
        file_id=conn.execute('''INSERT INTO capital_file_revision(path,sha256,byte_size,source_modified_at,record_count,payload)
          VALUES (%s,%s,%s,%s,%s,%s) RETURNING id''',(str(path.resolve()),digest,len(data),datetime.fromtimestamp(stamp.st_mtime,timezone.utc),len(mapped),data)).fetchone()['id']
        rows=[]
        for symbol,row in mapped.items():
            if symbol not in known:continue
            updated=None
            if len(row.get('GXRQ',''))==8:
                try:updated=date.fromisoformat(row['GXRQ'])
                except ValueError:pass
            values=[shares_value(row.get(k)) for k in ('ZGB','LTAG','BG','HG')]
            rows.append((file_id,symbol,*values,updated,Jsonb(row)))
        with conn.cursor() as cur:
            cur.executemany('''INSERT INTO capital_snapshot(file_id,symbol,total_shares,float_shares,b_shares,h_shares,source_updated_on,raw)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',rows)
        after=path.stat()
        if (after.st_size,after.st_mtime_ns)!=(stamp.st_size,stamp.st_mtime_ns):
            raise ValueError('股本文件导入期间更新，本文件事务已回滚')
    return {'skipped':False,'file_id':file_id,'records':len(mapped),'matched_securities':len(rows),'unmapped_securities':len(mapped)-len(rows)}
