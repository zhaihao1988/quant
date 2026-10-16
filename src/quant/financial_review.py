"""Quarantine whole conflicting reports. This module never resolves a conflict."""
from collections import defaultdict
from psycopg.types.json import Jsonb
from .db import connect
from .tdx.financial import decode_fields

REVIEW_LOCK=779930


def conflict_ordinals(records):
    grouped=defaultdict(list)
    for ordinal,(code,tag,payload) in enumerate(records,1):grouped[code].append((ordinal,payload))
    blocked=set()
    for group in grouped.values():
        if len(group)<2:continue
        first=decode_fields(group[0][1])
        if any(decode_fields(payload)!=first for _,payload in group[1:]):blocked.update(ordinal for ordinal,_ in group)
    return blocked


def quarantine_existing():
    with connect() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(%s)',(REVIEW_LOCK,))
        rows=conn.execute('''SELECT id,file_id,code,payload FROM financial_report WHERE (file_id,code) IN
          (SELECT file_id,code FROM financial_report GROUP BY file_id,code HAVING count(*)>1) ORDER BY file_id,code,id''').fetchall()
        grouped=defaultdict(list)
        for row in rows:grouped[(row['file_id'],row['code'])].append(row)
        blocked=[];groups=0
        for group in grouped.values():
            first=decode_fields(group[0]['payload'])
            if any(decode_fields(row['payload'])!=first for row in group[1:]):
                groups+=1;blocked.extend(row['id'] for row in group)
        # Keep payloads unchanged, remove only unapproved derived facts.
        if blocked:
            conn.execute("UPDATE financial_report SET review_state='pending_conflict',reviewed_at=now(),standard='{}' WHERE id=ANY(%s) AND review_state<>'pending_conflict'",(blocked,))
        verified=conn.execute("UPDATE financial_report SET review_state='verified',reviewed_at=now() WHERE review_state='unreviewed'").rowcount
    return {'quarantined_reports':groups,'quarantined_source_records':len(blocked),'newly_verified_records':verified,
            'note':'冲突报告全量隔离；没有选取任何冲突候选值。'}
