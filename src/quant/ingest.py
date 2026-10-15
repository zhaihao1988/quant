import hashlib
import json
import logging
import re
import struct
import threading
from bisect import bisect_left
from contextlib import contextmanager
import psycopg
from psycopg.rows import dict_row
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from uuid import uuid4
from psycopg.types.json import Jsonb
from .config import settings
from .db import connect
from .tdx.readers import dbf_records, day_records, stable_bytes, gbbq_records, is_a_stock

log = logging.getLogger(__name__)


def reserve_run(symbols=None,mode='incremental',retry_of=None):
    if mode not in ('incremental','verify'):raise ValueError('非法同步模式')
    with connect() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(779923)')
        active=conn.execute("SELECT *,heartbeat_at>now()-interval '120 seconds' AS recent FROM import_run WHERE status IN ('queued','running') ORDER BY started_at DESC LIMIT 1").fetchone()
        if active:
            unlocked=conn.execute('SELECT pg_try_advisory_lock(779922) AS ok').fetchone()['ok']
            if unlocked:conn.execute('SELECT pg_advisory_unlock(779922)')
            if not unlocked or (active['status']=='queued' and active['recent']):return str(active['id']),True
            conn.execute("UPDATE import_run SET status='interrupted',finished_at=now(),stage='进程已退出，可重试' WHERE id=%s",(active['id'],))
        run_id=str(uuid4())
        conn.execute("INSERT INTO import_run(id,status,mode,symbols,retry_of) VALUES (%s,'queued',%s,%s,%s)",(run_id,mode,Jsonb(symbols) if symbols else None,retry_of))
    return run_id,False


def new_run():
    return reserve_run()[0]


def progress(run_id, **values):
    allowed = {'status','stage','processed','total','changed_rows','skipped','errors','parsed_rows'}
    if not values.keys() <= allowed:
        raise ValueError('非法任务字段')
    with connect() as conn:
        params = [Jsonb(v) if k=='errors' else v for k,v in values.items()]
        conn.execute('UPDATE import_run SET heartbeat_at=now(),'+','.join(f'{k}=%s' for k in values)+' WHERE id=%s', params+[run_id])


def metadata(root):
    cache = root / 'T0002/hq_cache'
    rows = list(dbf_records(cache/'base.dbf'))
    names = {}
    for market,file in [('SH','shs.tnf'),('SZ','szs.tnf'),('BJ','bjs.tnf')]:
        path = cache/file
        if path.exists():
            data = path.read_bytes()
            for match in re.finditer(rb'(?<![0-9])([0-9]{6})\x00{4}', data):
                code = match.group(1).decode()
                # This installed version stores the name 31 bytes after the code.
                name = data[match.start()+31:match.start()+63].split(b'\0')[0].decode('gbk',errors='replace').strip()
                if name and '\ufffd' not in name and not any(ord(c)<32 for c in name):
                    names[(market,code)] = name
    source_date = datetime.fromtimestamp((cache/'base.dbf').stat().st_mtime).date()
    securities, shares = {},[]
    market_map = {'0':'SZ','1':'SH','2':'BJ'}
    for row in rows:
        code,market = row.get('GPDM',''),market_map.get(row.get('SC'))
        if not market or not is_a_stock(market,code):
            continue
        symbol = f'{code}.{market}'
        listed = row.get('SSDATE','')
        listing_date = date(int(listed[:4]),int(listed[4:6]),int(listed[6:8])) if len(listed)==8 and listed.isdigit() else None
        securities[symbol] = (symbol,code,market,names.get((market,code),code),listing_date)
        floating,total = row.get('LTAG'),row.get('ZGB')
        if floating and float(floating)>0:
            shares.append((symbol,source_date,round(float(floating)*10000),round(float(total)*10000) if total and float(total)>0 else None,'tdx_base','snapshot'))
    # Retain local historical securities not in the current base snapshot; name remains code if unavailable.
    for market in ('SH','SZ','BJ'):
        for path in (root/'vipdoc'/market.lower()/'lday').glob('*.day'):
            code = path.stem[2:]
            if is_a_stock(market,code):
                symbol=f'{code}.{market}'
                securities.setdefault(symbol,(symbol,code,market,names.get((market,code),code),None))
    with connect() as conn, conn.cursor() as cur:
        cur.executemany('INSERT INTO security(symbol,code,market,name,listing_date) VALUES (%s,%s,%s,%s,%s) ON CONFLICT(symbol) DO UPDATE SET name=EXCLUDED.name,listing_date=COALESCE(EXCLUDED.listing_date,security.listing_date),updated_at=now()',list(securities.values()))
        cur.executemany('INSERT INTO share_observation(symbol,effective_date,float_shares,total_shares,source,quality) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(symbol,effective_date,source) DO UPDATE SET float_shares=EXCLUDED.float_shares,total_shares=EXCLUDED.total_shares,observed_at=now()',shares)
    return securities


def capital(root, known):
    path = root/'T0002/hq_cache/gbbq'
    data,digest = stable_bytes(path)
    with connect() as conn:
        old=conn.execute('SELECT sha256 FROM import_file WHERE path=%s',(str(path),)).fetchone()
        if old and old['sha256']==digest:
            return 0
    events=[e for e in gbbq_records(data) if e[0] in known]
    # Category 5 is the explicit capital-change layout. Other categories remain raw until separately verified.
    shares=[(e[0],e[1],round(e[5]*10000),round(e[6]*10000) if e[6]>0 else None,'tdx_gbbq_cat5','event')
            for e in events if e[2]==5 and e[5]>0]
    with connect() as conn, conn.cursor() as cur:
        cur.executemany('INSERT INTO capital_event(symbol,event_date,category,c1,c2,c3,c4) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(symbol,event_date,category) DO UPDATE SET c1=EXCLUDED.c1,c2=EXCLUDED.c2,c3=EXCLUDED.c3,c4=EXCLUDED.c4,observed_at=now()',events)
        cur.executemany('INSERT INTO share_observation(symbol,effective_date,float_shares,total_shares,source,quality) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(symbol,effective_date,source) DO UPDATE SET float_shares=EXCLUDED.float_shares,total_shares=EXCLUDED.total_shares',shares)
        conn.execute('INSERT INTO import_file(path,sha256,byte_size,record_count) VALUES (%s,%s,%s,%s) ON CONFLICT(path) DO UPDATE SET sha256=EXCLUDED.sha256,byte_size=EXCLUDED.byte_size,record_count=EXCLUDED.record_count,imported_at=now()', (str(path),digest,len(data),len(events)))
    return len(events)


def block_hashes(data):
    return [hashlib.sha256(data[i:i+8192]).hexdigest() for i in range(0,len(data),8192)]


@contextmanager
def file_transaction(connection=None):
    if connection is None:
        with connect() as conn:yield conn
    else:
        with connection.transaction():yield connection


def import_day(path, symbol, mode='incremental',details=False,connection=None):
    def outcome(changed,skipped,parsed=0):
        return {'changed':changed,'skipped':skipped,'parsed':parsed} if details else (changed,skipped)
    with file_transaction(connection) as conn:
        old=conn.execute('SELECT * FROM import_file WHERE path=%s',(str(path),)).fetchone()
        latest=conn.execute('SELECT trade_date FROM daily_bar WHERE symbol=%s ORDER BY trade_date DESC LIMIT 1',(symbol,)).fetchone()
        db_date=latest['trade_date'] if latest else None
        matches=bool(old and old['latest_date']==db_date)
        before=path.stat()
        if mode=='incremental' and matches and old.get('source_mtime_ns')==before.st_mtime_ns and old['byte_size']==before.st_size and old.get('block_hashes'):
            with path.open('rb') as f:f.seek((len(old['block_hashes'])-1)*8192);tail=f.read()
            after=path.stat()
            if hashlib.sha256(tail).hexdigest()==old['block_hashes'][-1] and (before.st_size,before.st_mtime_ns)==(after.st_size,after.st_mtime_ns):
                return outcome(0,True)
        data,digest = stable_bytes(path)
        if not data:raise ValueError('源文件记录减少或为空，请等待下载完成')
        stamp=path.stat()
        if (stamp.st_size,stamp.st_mtime_ns)!=(before.st_size,before.st_mtime_ns):
            raise ValueError('文件正在写入，请等待下载完成后重试')
        blocks=block_hashes(data)
        if old and old['sha256']==digest and matches:
            conn.execute('UPDATE import_file SET source_mtime_ns=%s,block_hashes=%s WHERE path=%s',(stamp.st_mtime_ns,Jsonb(blocks),str(path)))
            return outcome(0,True)
        if len(data)%32:raise ValueError('日线文件尚未完整写入，请稍后重试')
        count=len(data)//32
        if old and count<old['record_count']:
            raise ValueError('源文件记录减少，拒绝自动截断；请检查下载完整性')
        start=0
        if old and old.get('block_hashes') and mode=='incremental':
            previous=old['block_hashes']
            first=next((i for i,h in enumerate(blocks) if i>=len(previous) or previous[i]!=h),len(blocks))
            start=max(0,min(first*256,old['record_count']-20))
        if not matches:
            cutoff=int(db_date.strftime('%Y%m%d')) if db_date else 0
            missing_start=bisect_left(range(count),cutoff,key=lambda i:struct.unpack_from('<I',data,i*32)[0])
            start=min(start,max(0,missing_start-20))
        bars=list(day_records(data[start*32:]))
        if start and bars and bars[0][0]<=int_date_value(data,(start-1)*32):
            raise ValueError('增量边界日期重复或乱序')
        if bars and ((old and old['latest_date'] and bars[-1][0]<old['latest_date']) or (db_date and bars[-1][0]<db_date)):
            raise ValueError('源文件最新日期落后，不覆盖已有历史')
        conn.execute('CREATE TEMP TABLE incoming (LIKE daily_bar INCLUDING DEFAULTS) ON COMMIT DROP')
        with conn.cursor().copy('COPY incoming(symbol,trade_date,open,high,low,close,volume,amount) FROM STDIN') as copy:
            for row in bars:
                copy.write_row((symbol,*row))
        changed=conn.execute('''INSERT INTO daily_bar(symbol,trade_date,open,high,low,close,volume,amount)
          SELECT symbol,trade_date,open,high,low,close,volume,amount FROM incoming
          ON CONFLICT(symbol,trade_date) DO UPDATE SET open=EXCLUDED.open,high=EXCLUDED.high,low=EXCLUDED.low,
          close=EXCLUDED.close,volume=EXCLUDED.volume,amount=EXCLUDED.amount,imported_at=now()
          WHERE (daily_bar.open,daily_bar.high,daily_bar.low,daily_bar.close,daily_bar.volume,daily_bar.amount)
          IS DISTINCT FROM (EXCLUDED.open,EXCLUDED.high,EXCLUDED.low,EXCLUDED.close,EXCLUDED.volume,EXCLUDED.amount)''').rowcount
        after=path.stat()
        if (stamp.st_size,stamp.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
            raise ValueError('导入期间文件仍在更新，本文件事务已回滚，请重试')
        conn.execute('''INSERT INTO import_file(path,sha256,byte_size,record_count,latest_date,source_mtime_ns,block_hashes)
          VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(path) DO UPDATE SET sha256=EXCLUDED.sha256,byte_size=EXCLUDED.byte_size,
          record_count=EXCLUDED.record_count,latest_date=EXCLUDED.latest_date,source_mtime_ns=EXCLUDED.source_mtime_ns,
          block_hashes=EXCLUDED.block_hashes,imported_at=now()''', (str(path),digest,len(data),count,bars[-1][0] if bars else None,stamp.st_mtime_ns,Jsonb(blocks)))
    return outcome(changed,False,len(bars))


def int_date_value(data,offset):
    from .tdx.readers import int_date
    return int_date(struct.unpack_from('<I',data,offset)[0])


def taxonomy(root, known):
    cache=root/'T0002/hq_cache'
    day=date.today()  # Snapshot observation date, never a fabricated historical membership date.
    names={}
    for p in (root/'incon.dat',cache/'incon.dat'):
        if p.exists():
            for line in p.read_text(encoding='gbk',errors='replace').splitlines():
                parts=line.split('|')
                if len(parts)>=2:
                    names[parts[0]]=parts[1]
    groups, members={},set()
    path=cache/'tdxhy.cfg'
    if path.exists():
        for line in path.read_text(encoding='gbk').splitlines():
            parts=line.split('|')
            if len(parts)<3 or parts[0] not in ('0','1','2'):
                continue
            symbol=parts[1]+'.'+{'0':'SZ','1':'SH','2':'BJ'}[parts[0]]
            if symbol not in known:
                continue
            for code in (parts[2],parts[5] if len(parts)>5 else ''):
                if code:
                    key='industry:'+code
                    groups[key]=(key,names.get(code,code),'industry','tdxhy.cfg')
                    members.add((key,symbol,day,'tdxhy.cfg'))
    path=cache/'spblock.dat'
    if path.exists():
        key=None
        for line in path.read_text(encoding='gbk').splitlines():
            if line.startswith('#'):
                name=line[1:].strip();key='pool:'+name
                groups[key]=(key,name,'pool','spblock.dat')
            elif key and len(line.strip())==7 and line[0] in ('0','1','2'):
                symbol=line.strip()[1:]+'.'+{'0':'SZ','1':'SH','2':'BJ'}[line[0]]
                if symbol in known:
                    members.add((key,symbol,day,'spblock.dat'))
    with connect() as conn, conn.cursor() as cur:
        cur.executemany('INSERT INTO taxonomy(id,name,kind,source) VALUES (%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET name=EXCLUDED.name',list(groups.values()))
        # Replace today's snapshot atomically, retain prior days.
        conn.execute('DELETE FROM membership WHERE snapshot_date=%s',(day,))
        cur.executemany('INSERT INTO membership(taxonomy_id,symbol,snapshot_date,source) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING',list(members))
        conn.execute("INSERT INTO graph_node(id,name,kind) SELECT symbol,name,'company' FROM security ON CONFLICT(id) DO UPDATE SET name=EXCLUDED.name")
        conn.execute('INSERT INTO graph_node(id,name,kind) SELECT id,name,kind FROM taxonomy ON CONFLICT(id) DO UPDATE SET name=EXCLUDED.name')
        cur.executemany("INSERT INTO graph_edge(source_id,target_id,relation,evidence) VALUES (%s,%s,'member_of',%s) ON CONFLICT DO NOTHING",[(s,t,f'{src} 快照 {dt}；仅表示分类，不表示上下游') for t,s,dt,src in members])
    return len(groups),len(members)


def run_import(run_id, symbols=None,mode='incremental'):
    errors=[];changed=skipped=processed=parsed=0;items=[]
    def save_items():
        if not items:return
        with connect() as conn,conn.cursor() as cur:
            cur.executemany('''INSERT INTO import_item(run_id,symbol,state,changed_rows,parsed_rows,error) VALUES (%s,%s,%s,%s,%s,%s)
              ON CONFLICT(run_id,symbol) DO UPDATE SET state=EXCLUDED.state,changed_rows=EXCLUDED.changed_rows,parsed_rows=EXCLUDED.parsed_rows,error=EXCLUDED.error''',items)
        items.clear()
    with connect() as lock_conn:
        acquired=lock_conn.execute('SELECT pg_try_advisory_lock(779922) AS locked').fetchone()['locked']
        if not acquired:
            progress(run_id,status='failed',errors=['已有导入任务运行，请稍后重试'])
            with connect() as conn:conn.execute('UPDATE import_run SET finished_at=now() WHERE id=%s',(run_id,))
            return
        try:
            root=settings().tdx_root
            progress(run_id,status='running',stage='基础资料')
            known=metadata(root)
            progress(run_id,stage='权息与股本')
            capital(root,known)
            progress(run_id,stage='行业与股票池')
            taxonomy(root,known)
            selected=set(symbols or known)
            invalid=selected-known.keys()
            if invalid:
                raise ValueError(f'证券不在本地A股目录中：{sorted(invalid)[:5]}')
            paths=[]
            for symbol in sorted(selected):
                code,market=symbol.split('.')
                p=root/'vipdoc'/market.lower()/'lday'/f'{market.lower()}{code}.day'
                if p.exists():
                    paths.append((symbol,p))
                else:
                    errors.append(f'{symbol}: 缺少日线文件')
                    items.append((run_id,symbol,'failed',0,0,'缺少日线文件'))
            save_items()
            progress(run_id,total=len(paths),stage='日线')
            # Bounded local I/O workers; each file retains its own transaction.
            local=threading.local();connections=[]
            def worker(path,symbol):
                if not hasattr(local,'conn') or local.conn.closed:
                    local.conn=psycopg.connect(settings().database_url,row_factory=dict_row,autocommit=True,connect_timeout=8)
                    connections.append(local.conn)
                return import_day(path,symbol,mode,True,local.conn)
            try:
                with ThreadPoolExecutor(max_workers=4,thread_name_prefix='tdx') as pool:
                    futures={pool.submit(worker,path,symbol):symbol for symbol,path in paths}
                    for future in as_completed(futures):
                        symbol=futures[future]
                        try:
                            result=future.result()
                            changed+=result['changed'];skipped+=int(result['skipped']);parsed+=result['parsed']
                            items.append((run_id,symbol,'skipped' if result['skipped'] else 'completed',result['changed'],result['parsed'],None))
                        except Exception as exc:
                            log.exception('Import failed: %s',symbol)
                            errors.append(f'{symbol}: {type(exc).__name__}: {str(exc)[:150]}')
                            items.append((run_id,symbol,'failed',0,0,f'{type(exc).__name__}: {str(exc)[:150]}'))
                        processed+=1
                        if processed%25==0 or processed==len(paths):
                            save_items()
                            progress(run_id,processed=processed,changed_rows=changed,skipped=skipped,parsed_rows=parsed,errors=errors)
                            log.info('Import %d/%d; changed=%d; errors=%d',processed,len(paths),changed,len(errors))
            finally:
                for conn in connections:conn.close()
            progress(run_id,status='partial' if errors else 'completed',stage='完成',errors=errors)
        except Exception as exc:
            log.exception('Import task failed')
            progress(run_id,status='failed',errors=errors+[f'{type(exc).__name__}: {str(exc)[:200]}'])
        finally:
            save_items()
            with connect() as conn:
                conn.execute('UPDATE import_run SET finished_at=now() WHERE id=%s',(run_id,))
            lock_conn.execute('SELECT pg_advisory_unlock(779922)')
    from .sync_status import scan_sources
    scan_sources()
