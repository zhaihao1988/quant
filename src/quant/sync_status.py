"""Fast, read-only source/DB comparison, persisted for inexpensive UI polling."""
import struct
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
from psycopg.types.json import Jsonb
from .config import settings
from .db import connect
from .tdx.readers import int_date,is_a_stock


def inspect_file(symbol,name,path,db_date,db_records,old):
    market=symbol.split('.')[1]
    row={'symbol':symbol,'name':name,'market':market,'path':str(path),'db_date':db_date,
         'local_date':None,'local_records':None,'db_records':db_records,'source_mtime_ns':None,
         'state':'missing','reason':'未找到本地日线文件'}
    try:
        before=path.stat();row['source_mtime_ns']=before.st_mtime_ns
        if before.st_size%32 or not before.st_size:
            row.update(state='busy',reason='日线文件为空或长度不完整，等待下载完成');return row
        with path.open('rb') as f:
            f.seek(-32,2);dt=struct.unpack('<I',f.read(4))[0]
            tail_matches=True
            if old and old.get('block_hashes') and before.st_size==old['byte_size']:
                f.seek((len(old['block_hashes'])-1)*8192)
                tail_matches=hashlib.sha256(f.read()).hexdigest()==old['block_hashes'][-1]
        after=path.stat()
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
            row.update(state='busy',reason='文件正在写入，稍后重试');return row
        row.update(local_date=int_date(dt),local_records=before.st_size//32)
        if db_date is None:row.update(state='new',reason='数据库尚无行情')
        elif row['local_date']<db_date or (old and row['local_records']<old['record_count']):
            row.update(state='older',reason='本地文件较旧或记录变少，不覆盖数据库历史')
        elif row['local_date']>db_date or (old and before.st_size>old['byte_size']):
            row.update(state='new',reason='发现较新日期或新增记录')
        elif not old or old.get('source_mtime_ns')!=before.st_mtime_ns or not tail_matches:
            row.update(state='changed',reason='日期相同但文件已变化或尚未建立指纹，需校验')
        else:row.update(state='aligned',reason='日期、大小、修改标记及尾部校验一致')
    except FileNotFoundError:pass
    except (OSError,ValueError,struct.error) as exc:row.update(state='busy',reason=f'暂不可读：{type(exc).__name__}')
    return row


def scan_sources():
    with connect() as lock:
        if not lock.execute('SELECT pg_try_advisory_lock(779924) AS ok').fetchone()['ok']:return
        try:
            with connect() as conn:
                conn.execute("INSERT INTO sync_scan(id,status,started_at) VALUES (1,'running',now()) ON CONFLICT(id) DO UPDATE SET status='running',started_at=now(),error=NULL")
                securities=conn.execute('''SELECT s.symbol,s.name,b.trade_date AS db_date FROM security s LEFT JOIN LATERAL
                  (SELECT trade_date FROM daily_bar WHERE symbol=s.symbol ORDER BY trade_date DESC LIMIT 1)b ON true''').fetchall()
                files=conn.execute('SELECT * FROM import_file').fetchall()
            old={f['path']:f for f in files};known={s['symbol']:s for s in securities};root=settings().tdx_root
            for market in ('SH','SZ','BJ'):
                for p in (root/'vipdoc'/market.lower()/'lday').glob('*.day'):
                    code=p.stem[2:]
                    if is_a_stock(market,code):known.setdefault(f'{code}.{market}',{'symbol':f'{code}.{market}','name':code,'db_date':None})
            if not (root/'vipdoc').is_dir():raise ValueError('通达信vipdoc目录不可用')
            def inspect(s):
                code,market=s['symbol'].split('.')
                path=root/'vipdoc'/market.lower()/'lday'/f'{market.lower()}{code}.day'
                manifest=old.get(str(path))
                return inspect_file(s['symbol'],s['name'],path,s['db_date'],manifest['record_count'] if manifest else 0,manifest)
            with ThreadPoolExecutor(max_workers=8) as pool:rows=list(pool.map(inspect,known.values()))
            with connect() as conn,conn.cursor() as cur:
                cur.executemany('''INSERT INTO source_inventory(symbol,name,market,path,db_date,local_date,local_records,db_records,source_mtime_ns,state,reason)
                  VALUES (%(symbol)s,%(name)s,%(market)s,%(path)s,%(db_date)s,%(local_date)s,%(local_records)s,%(db_records)s,%(source_mtime_ns)s,%(state)s,%(reason)s)
                  ON CONFLICT(symbol) DO UPDATE SET name=EXCLUDED.name,db_date=EXCLUDED.db_date,local_date=EXCLUDED.local_date,
                  local_records=EXCLUDED.local_records,db_records=EXCLUDED.db_records,source_mtime_ns=EXCLUDED.source_mtime_ns,
                  state=EXCLUDED.state,reason=EXCLUDED.reason,scanned_at=now()''',rows)
                conn.execute("UPDATE sync_scan SET status='completed',finished_at=now() WHERE id=1")
        except Exception as exc:
            with connect() as conn:conn.execute("UPDATE sync_scan SET status='failed',finished_at=now(),error=%s WHERE id=1",(f'{type(exc).__name__}: {str(exc)[:200]}',))
        finally:lock.execute('SELECT pg_advisory_unlock(779924)')


def comparison(state='',query='',offset=0,limit=30):
    from .services import clean
    with connect() as conn:
        scan=conn.execute('SELECT * FROM sync_scan WHERE id=1').fetchone()
        counts=conn.execute('SELECT state,count(*) AS n FROM source_inventory GROUP BY state').fetchall()
        dates=conn.execute('SELECT max(db_date) AS db_date,max(local_date) AS local_date FROM source_inventory').fetchone()
        clauses=['(symbol ILIKE %s OR name ILIKE %s)'];params=[f'%{query}%',f'%{query}%']
        if state=='pending':clauses.append("state IN ('new','changed','busy')")
        elif state:
            if state not in ('new','changed','aligned','missing','busy','older'):raise ValueError('非法对比状态')
            clauses.append('state=%s');params.append(state)
        where=' AND '.join(clauses)
        total=conn.execute('SELECT count(*) AS n FROM source_inventory WHERE '+where,params).fetchone()['n']
        rows=conn.execute('SELECT * FROM source_inventory WHERE '+where+' ORDER BY symbol LIMIT %s OFFSET %s',params+[limit,offset]).fetchall()
        active=conn.execute("SELECT id,status,stage,processed,total,mode,heartbeat_at<now()-interval '120 seconds' AS stale FROM import_run WHERE status IN ('queued','running') ORDER BY started_at DESC LIMIT 1").fetchone()
        if active and active['stale']:
            unlocked=conn.execute('SELECT pg_try_advisory_lock(779922) AS ok').fetchone()['ok']
            if unlocked:
                conn.execute('SELECT pg_advisory_unlock(779922)')
                conn.execute("UPDATE import_run SET status='interrupted',finished_at=now(),stage='进程已退出，可重试' WHERE id=%s",(active['id'],))
                active=None
    cache=settings().tdx_root/'T0002/hq_cache';sources=[]
    for name in ('base.dbf','gbbq','tdxhy.cfg','spblock.dat'):
        p=cache/name
        sources.append({'name':name,'updated_at':datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec='seconds') if p.exists() else None})
    stale=not scan or scan['status']=='failed' or not scan['finished_at'] or datetime.now(timezone.utc)-scan['finished_at']>timedelta(seconds=60)
    return clean({'scan':scan,'stale':stale,'counts':{x['state']:x['n'] for x in counts},'dates':dates,
                  'items':rows,'total':total,'offset':offset,'limit':limit,'active':active,'auxiliary_sources':sources,
                  'scope':'仅沪深北A股；不扫描或导入扩展行情。日期相同不等于内容相同，完整校验可检查所有文件哈希。'})
