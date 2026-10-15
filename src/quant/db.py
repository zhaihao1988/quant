import hashlib
from pathlib import Path
from contextlib import contextmanager
import psycopg
from psycopg.rows import dict_row
from .config import settings


@contextmanager
def connect():
    with psycopg.connect(settings().database_url, row_factory=dict_row, connect_timeout=8) as conn:
        yield conn


def migrate():
    with connect() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(779921)')
        conn.execute('CREATE TABLE IF NOT EXISTS schema_migration (name text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())')
        for path in sorted((Path(__file__).parent / 'migrations').glob('*.sql')):
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            old = conn.execute('SELECT sha256 FROM schema_migration WHERE name=%s', (path.name,)).fetchone()
            if old:
                if old['sha256'] != digest:
                    raise RuntimeError(f'已应用迁移内容发生变化：{path.name}')
                continue
            conn.execute(path.read_text(encoding='utf-8'))
            conn.execute('INSERT INTO schema_migration(name,sha256) VALUES (%s,%s)', (path.name, digest))
