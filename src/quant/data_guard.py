"""Shared technical readers and exclusive quote/ex-rights imports across entry points."""
from contextlib import contextmanager
from .db import connect

DATA_LOCK=779938


@contextmanager
def market_access(write=False):
    suffix='' if write else '_shared'
    with connect() as conn:
        ok=conn.execute(f'SELECT pg_try_advisory_lock{suffix}(%s) AS ok',(DATA_LOCK,)).fetchone()['ok']
        if not ok:raise ValueError('行情更新与选股不能同时运行，请等待当前任务完成')
        conn.commit()
        try:yield
        finally:
            conn.execute(f'SELECT pg_advisory_unlock{suffix}(%s)',(DATA_LOCK,));conn.commit()
