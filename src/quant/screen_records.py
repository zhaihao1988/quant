"""Technical input identities, immutable run archives, portable exports and backups."""
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
from datetime import datetime,timezone
from psycopg.types.json import Jsonb
from .db import connect
from .config import ROOT,settings
from .services import clean


def digest(value):return hashlib.sha256(json.dumps(clean(value),ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def stock_scope(conn,request,effective):
    clauses=["name<>''"];params=[]
    if request.symbols:clauses.append('symbol=ANY(%s)');params.append(request.symbols)
    if request.taxonomy_id:
        clauses.append('symbol IN (SELECT symbol FROM membership WHERE taxonomy_id=%s AND snapshot_date=(SELECT max(snapshot_date) FROM membership WHERE taxonomy_id=%s AND snapshot_date<=%s))')
        params.extend([request.taxonomy_id,request.taxonomy_id,effective])
    return conn.execute('SELECT symbol,name FROM security WHERE '+' AND '.join(clauses)+' ORDER BY symbol',params).fetchall()


def input_identity(conn,request,effective):
    scope=request.model_dump(mode='json');scope.pop('workers',None);scope.pop('as_of',None)
    scope['symbols']=sorted(set(scope['symbols']));scope['strategies']=sorted(scope['strategies'])
    stocks=stock_scope(conn,request,effective)
    files=conn.execute('SELECT path,sha256,record_count,latest_date FROM import_file ORDER BY path').fetchall()
    bars=conn.execute('SELECT count(*) AS rows,max(imported_at) AS modified FROM daily_bar WHERE trade_date<=%s',(effective,)).fetchone()
    shares=conn.execute("""SELECT md5(coalesce(string_agg(concat_ws('|',symbol,effective_date,float_shares,total_shares,source),'/' ORDER BY symbol,effective_date,source),'')) AS digest
        FROM share_observation WHERE effective_date<=%s""",(effective,)).fetchone()
    code=Path(__file__).parent
    versions={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((code/'legacy_strategies').glob('*.py'))}
    for name in ('multiscreener.py','services.py','domain.py','corporate_actions.py'):
        versions[name]=hashlib.sha256((code/name).read_bytes()).hexdigest()
    manifest=clean({'format':1,'date':effective,'scope':scope,'stocks':stocks,'files':files,'bars':bars,'shares':shares,'strategies':versions})
    return digest(manifest),digest(scope),manifest


def archive(run_id,revision=None):
    with connect() as conn:
        row=conn.execute('SELECT * FROM screen_archive WHERE run_id=%s'+(' AND revision=%s' if revision else '')+' ORDER BY revision DESC LIMIT 1',
                         (run_id,revision) if revision else (run_id,)).fetchone()
    if not row:raise ValueError('该记录没有当次归档；旧记录不能用当前财务补造')
    return clean(row)


def save_archive(run_id):
    from .multi_jobs import snapshot
    with connect() as conn:
        run=conn.execute('SELECT * FROM multi_screen_run WHERE id=%s',(run_id,)).fetchone()
        if not run or run['status'] in ('queued','running'):raise ValueError('任务尚未结束')
        old=conn.execute('SELECT revision FROM screen_archive WHERE run_id=%s AND finished_at=%s',(run_id,run['finished_at'])).fetchone()
        if old:return archive(run_id,old['revision'])
        if run['archive_state']=='legacy':raise ValueError('旧任务未保存当次财务，不能补造归档')
    payload=snapshot(run_id,recover=False);payload['view']='archive'
    payload.pop('archive_info',None);payload['archive_error']=None
    payload['archived_at']=datetime.now(timezone.utc).isoformat()
    payload['input_manifest']=run['input_manifest']
    payload['archive_state']='saved'
    comparison={'available':False,'reason':'本次或上次结果不完整，或没有可比较的同方案归档'}
    if run['status']=='completed':
        with connect() as conn:
            prior=conn.execute("""SELECT a.payload FROM screen_archive a JOIN multi_screen_run r ON r.id=a.run_id
                WHERE r.scope_signature=%s AND r.effective_date<%s AND r.status='completed'
                ORDER BY r.effective_date DESC,a.created_at DESC LIMIT 1""",(run['scope_signature'],run['effective_date'])).fetchone()
        if prior:
            current={s['symbol'] for s in payload['result'].get('signals',[])}
            previous={s['symbol'] for s in prior['payload']['result'].get('signals',[])}
            comparison={'available':True,'previous_date':prior['payload']['effective_date'],
                        'added':sorted(current-previous),'retained':sorted(current&previous),'removed':sorted(previous-current),
                        'basis':'上次同方案完整结果；不保证为相邻交易日'}
    payload['comparison']=comparison
    with connect() as conn:
        conn.execute('SELECT id FROM multi_screen_run WHERE id=%s FOR UPDATE',(run_id,))
        old=conn.execute('SELECT revision FROM screen_archive WHERE run_id=%s AND finished_at=%s',(run_id,run['finished_at'])).fetchone()
        if old:return archive(run_id,old['revision'])
        revision=conn.execute('SELECT coalesce(max(revision),0)+1 AS n FROM screen_archive WHERE run_id=%s',(run_id,)).fetchone()['n']
        conn.execute('INSERT INTO screen_archive(run_id,revision,finished_at,payload,sha256) VALUES (%s,%s,%s,%s,%s)',
                     (run_id,revision,run['finished_at'],Jsonb(payload),digest(payload)))
        conn.execute("UPDATE multi_screen_run SET archive_state='saved',archive_error=NULL WHERE id=%s",(run_id,))
    export_archive(run_id,revision)
    if os.getenv('QUANT_BACKUP_ON_ARCHIVE','0')=='1':
        try:backup_database()
        except Exception as exc:
            with connect() as conn:conn.execute('UPDATE screen_archive SET export_error=%s WHERE run_id=%s AND revision=%s',('归档已保存；数据库备份失败：'+type(exc).__name__,run_id,revision))
    return archive(run_id,revision)


def export_archive(run_id,revision=None):
    record=archive(run_id,revision);data=record['payload']
    root=Path(os.getenv('QUANT_ARCHIVE_DIR',str(ROOT/'.runtime/archives')))
    path=root/data['effective_date']/f'{run_id}-r{record["revision"]}.json'
    try:
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf8')
        temporary.replace(path)
        with connect() as conn:conn.execute('UPDATE screen_archive SET export_path=%s,export_error=NULL WHERE run_id=%s AND revision=%s',(str(path),run_id,record['revision']))
    except (OSError,ValueError) as exc:
        with connect() as conn:conn.execute('UPDATE screen_archive SET export_error=%s WHERE run_id=%s AND revision=%s',('本地副本保存失败：'+type(exc).__name__,run_id,record['revision']))
    return str(path)


def csv_export(payload):
    keys=[('symbol','证券'),('name','名称'),('signal_date','信号日期'),('strategy_label','策略'),('signal_level','级别'),
          ('price_date','价格日期'),('financial_report_date','财报期'),('revenue_total','营业总收入'),('net_profit_parent','归母净利润'),
          ('equity_parent','归母净资产'),('pe','PE'),('pb','PB'),('revenue_growth_yoy','营收同比'),('profit_growth_yoy','净利同比'),
          ('peg_like_ratio','PEG'),('net_profit_positive_3y_latest','连续3年及最新净利为正'),('growth_positive','增长为正'),
          ('pe_lt_30','PE<30'),('peg_like_lt_1','PEG<1'),('total_shares','总股本（股）'),('float_shares','流通A股（股）'),
          ('market_cap','总市值（元）'),('float_market_cap','流通A股市值（元）'),('turnover_pct','参考换手率（%）')]
    output=io.StringIO();writer=csv.writer(output);writer.writerow([label for _,label in keys]+['任务ID','交易日','查看口径'])
    def safe(value):
        if isinstance(value,str) and value[:1] in ('=','+','-','@','\t','\r'):return "'"+value
        return value
    for row in payload['result'].get('signals',[]):
        writer.writerow([safe(row.get(k)) for k,_ in keys]+[payload['id'],payload['effective_date'],payload.get('view','latest')])
    return '\ufeff'+output.getvalue()


def backup_database():
    from psycopg.conninfo import conninfo_to_dict
    cfg=conninfo_to_dict(settings().database_url)
    binary=Path(os.getenv('QUANT_PG_DUMP',str(ROOT/'.runtime/pgsql/bin/pg_dump.exe')))
    if not binary.is_file():raise ValueError('未配置可用pg_dump，请设置QUANT_PG_DUMP')
    root=Path(os.getenv('QUANT_BACKUP_DIR',str(ROOT/'.runtime/backups')));root.mkdir(parents=True,exist_ok=True)
    path=root/f'quant-{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")}.dump';temporary=path.with_suffix('.partial')
    env=os.environ.copy();env['PGPASSWORD']=cfg.get('password','')
    args=[str(binary),'-Fc','-h',cfg.get('host','127.0.0.1'),'-p',cfg.get('port','5432'),'-U',cfg.get('user',''),'-d',cfg.get('dbname',''),'-f',str(temporary)]
    result=subprocess.run(args,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if result.returncode:raise ValueError('数据库备份失败，保留已有归档；请检查备份目录和数据库权限')
    temporary.replace(path);return {'path':str(path),'bytes':path.stat().st_size}
