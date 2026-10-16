"""File-transaction financial ingestion. Original records are immutable revisions."""
import hashlib
import json
from pathlib import Path
from uuid import uuid4
import zipfile
from psycopg.types.json import Jsonb
from .config import settings
from .db import connect
from .tdx.financial import financial_header,financial_records,decode_fields,announcement_date
from .financial_fields import standard_facts,MAPPING_VERSION
from .financial_review import conflict_ordinals

IMPORT_LOCK=779930


def signature(path):
    stat=path.stat();return stat.st_size,stat.st_mtime_ns


def read_package(path):
    if path.suffix.lower()=='.dat':return path.read_bytes()
    with zipfile.ZipFile(path) as archive:
        members=[m for m in archive.infolist() if Path(m.filename).name==path.stem+'.dat']
        if len(members)!=1 or members[0].file_size>128*1024*1024:raise ValueError('财务压缩包内容无效或过大')
        return archive.read(members[0])


def import_financial_file(path,symbols=None):
    path=Path(path);before=signature(path);data=read_package(path)
    if signature(path)!=before:raise ValueError('财务文件正在更新，请稍后重试')
    header=financial_header(data)
    if path.stem!='gpcw'+header['report_date'].strftime('%Y%m%d'):raise ValueError('财务文件名与报告期不一致')
    digest=hashlib.sha256(data).hexdigest()
    with connect() as conn:
        if symbols is None:
            rows=conn.execute('SELECT code,array_agg(symbol) AS symbols FROM security GROUP BY code').fetchall()
            symbols={r['code']:r['symbols'][0] for r in rows if len(r['symbols'])==1}
        current=conn.execute('SELECT * FROM financial_file_revision WHERE report_date=%s AND is_current FOR UPDATE',(header['report_date'],)).fetchone()
        if current and current['sha256']==digest:
            if signature(path)!=before:raise ValueError('财务文件正在更新')
            return {'skipped':True,'reports':0,'field_count':header['field_count']}
        if current and current['record_count']>header['record_count']:raise ValueError('财务记录减少，保留原版本，请核验下载完整性')
        existing=conn.execute('SELECT id FROM financial_file_revision WHERE path=%s AND sha256=%s',(str(path.resolve()),digest)).fetchone()
        if existing:raise ValueError('源文件回退到旧版本，未自动回退当前财报')
        records=list(financial_records(data,header))
        blocked=conflict_ordinals(records)
        file_id=conn.execute('''INSERT INTO financial_file_revision(path,sha256,report_date,byte_size,record_count,field_count,format_version,is_current)
          VALUES (%s,%s,%s,%s,%s,%s,%s,false) RETURNING id''',
          (str(path.resolve()),digest,header['report_date'],len(data),header['record_count'],header['field_count'],header['format_version'])).fetchone()['id']
        with conn.cursor().copy('''COPY financial_report(file_id,code,raw_tag,symbol,report_date,announced_on,field_count,payload,standard,mapping_version,source_ordinal,review_state,reviewed_at) FROM STDIN''') as copy:
            from datetime import datetime,timezone
            reviewed_at=datetime.now(timezone.utc)
            for ordinal,(code,tag,payload) in enumerate(records,1):
                values=decode_fields(payload)
                state='pending_conflict' if ordinal in blocked else 'verified'
                standard={} if ordinal in blocked else standard_facts(values)
                copy.write_row((file_id,code,tag,symbols.get(code),header['report_date'],announcement_date(values,header['report_date']),
                                header['field_count'],payload,Jsonb(standard),MAPPING_VERSION,ordinal,state,reviewed_at))
        if signature(path)!=before:raise ValueError('财务文件导入期间发生变化，本文件已回滚')
        conn.execute('UPDATE financial_file_revision SET is_current=false WHERE report_date=%s AND is_current',(header['report_date'],))
        conn.execute('UPDATE financial_file_revision SET is_current=true WHERE id=%s',(file_id,))
    return {'skipped':False,'reports':header['record_count'],'field_count':header['field_count']}


def import_financials(directory=None):
    root=Path(directory) if directory else settings().tdx_root/'vipdoc'/'cw'
    candidates={p.stem:p for p in root.glob('gpcw????????.zip')}
    candidates.update({p.stem:p for p in root.glob('gpcw????????.dat')})
    files=[candidates[key] for key in sorted(candidates)]
    if not files:raise ValueError('未找到通达信专业财务文件')
    with connect() as lock:
        if not lock.execute('SELECT pg_try_advisory_lock(%s) AS ok',(IMPORT_LOCK,)).fetchone()['ok']:raise ValueError('财务导入正在运行')
        lock.commit();run_id=str(uuid4());errors=[];reports=skipped=0
        try:
            with connect() as conn:
                conn.execute("UPDATE financial_import_run SET status='interrupted',finished_at=now() WHERE status='running'")
                conn.execute("INSERT INTO financial_import_run(id,status,total) VALUES (%s,'running',%s)",(run_id,len(files)))
                stocks=conn.execute('SELECT code,array_agg(symbol) AS symbols FROM security GROUP BY code').fetchall()
            symbols={r['code']:r['symbols'][0] for r in stocks if len(r['symbols'])==1}
            for i,path in enumerate(files,1):
                try:
                    result=import_financial_file(path,symbols);reports+=result['reports'];skipped+=int(result['skipped'])
                except Exception as exc:errors.append({'file':path.name,'error':f'{type(exc).__name__}: {exc}'})
                with connect() as conn:
                    conn.execute('UPDATE financial_import_run SET processed=%s,reports=%s,skipped=%s,errors=%s WHERE id=%s',(i,reports,skipped,Jsonb(errors),run_id))
            with connect() as conn:
                # A newly added security may match an already-imported immutable payload.
                conn.execute('''UPDATE financial_report r SET symbol=s.symbol FROM
                  (SELECT code,min(symbol) AS symbol FROM security GROUP BY code HAVING count(*)=1)s
                  WHERE r.symbol IS NULL AND r.code=s.code''')
                conn.execute('UPDATE financial_import_run SET status=%s,finished_at=now() WHERE id=%s',('partial' if errors else 'completed',run_id))
                return conn.execute('SELECT * FROM financial_import_run WHERE id=%s',(run_id,)).fetchone()
        finally:
            lock.execute('SELECT pg_advisory_unlock(%s)',(IMPORT_LOCK,));lock.commit()
