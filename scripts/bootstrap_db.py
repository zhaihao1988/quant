"""Initialize an isolated Windows PostgreSQL cluster; secrets stay in ignored files."""
import json
import os
from pathlib import Path
import secrets
import subprocess
from urllib.parse import quote
import psycopg
from psycopg import sql

ROOT=Path(__file__).resolve().parents[1]
RUNTIME=ROOT/'.runtime'
BIN=RUNTIME/'pgsql/bin'
DATA=RUNTIME/'pgdata'
PORT=55432


def run(*args):
    # pg_ctl's Windows child may inherit handles: a real file avoids a pipe that
    # remains open for the lifetime of the server after pg_ctl exits.
    with (RUNTIME/'bootstrap-command.log').open('w+b') as output:
        result=subprocess.run([str(x) for x in args],stdout=output,stderr=subprocess.STDOUT,
                              timeout=120,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        if result.returncode:
            output.seek(0)
            raise RuntimeError(output.read().decode(errors='replace'))


def main():
    if not (BIN/'initdb.exe').exists():
        raise RuntimeError('请先安装官方PostgreSQL二进制到 .runtime/pgsql')
    credentials=RUNTIME/'db_admin.json'
    if credentials.exists():
        secret=json.loads(credentials.read_text())
    else:
        secret={'admin_password':secrets.token_urlsafe(32),'app_password':secrets.token_urlsafe(32)}
        credentials.write_text(json.dumps(secret),encoding='utf-8')
    if not (DATA/'PG_VERSION').exists():
        password_file=RUNTIME/'initdb_password.tmp'
        try:
            password_file.write_text(secret['admin_password'],encoding='utf-8')
            run(BIN/'initdb.exe','-D',DATA,'-U','quant_admin','--encoding=UTF8','--locale=C',
                '--auth-host=scram-sha-256','--auth-local=scram-sha-256',f'--pwfile={password_file}')
        finally:
            password_file.unlink(missing_ok=True)
        with (DATA/'postgresql.conf').open('a',encoding='utf-8') as f:
            f.write("\n# quant private cluster\nlisten_addresses = '127.0.0.1'\nport = 55432\nshared_buffers = '256MB'\n")
    check=subprocess.run([str(BIN/'pg_ctl.exe'),'-D',str(DATA),'status'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
    if check.returncode:
        run(BIN/'pg_ctl.exe','-D',DATA,'-l',RUNTIME/'postgresql.log','-w','start')
    with psycopg.connect(host='127.0.0.1',port=PORT,user='quant_admin',password=secret['admin_password'],dbname='postgres',autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='quant_app'").fetchone():
            conn.execute(sql.SQL('CREATE ROLE quant_app LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE').format(sql.Literal(secret['app_password'])))
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname='quant'").fetchone():
            conn.execute('CREATE DATABASE quant OWNER quant_app')
    env=ROOT/'.env'
    if not env.exists():
        env.write_text(f"QUANT_DATABASE_URL=postgresql://quant_app:{quote(secret['app_password'])}@127.0.0.1:{PORT}/quant\nQUANT_TDX_ROOT=D:/tdx\nQUANT_HOST=127.0.0.1\nQUANT_PORT=8765\nQUANT_MCP_PORT=8766\n",encoding='utf-8')
    print(f'PostgreSQL ready at 127.0.0.1:{PORT}; database=quant; role=quant_app')


if __name__=='__main__':main()
