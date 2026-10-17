ALTER TABLE evidence_analysis ADD COLUMN status text NOT NULL DEFAULT 'ready' CHECK(status IN ('ready','failed'));
ALTER TABLE evidence_analysis ADD COLUMN error text;
CREATE INDEX evidence_analysis_news ON evidence_analysis(news_revision,created_at DESC);
-- Retain coverage investigations even if a process dies during pagination.
ALTER TABLE news_poll ADD COLUMN stopped_at_page integer;
ALTER TABLE evidence_queue ADD COLUMN priority integer NOT NULL DEFAULT 5;
UPDATE evidence_queue SET priority=CASE kind WHEN 'news' THEN 0 WHEN 'research' THEN 1 ELSE 5 END;
UPDATE evidence_queue SET priority=2 WHERE kind='company' AND split_part(doc_key,':',1) IN (
 SELECT symbol FROM multi_screen_item WHERE run_id=(SELECT id FROM multi_screen_run ORDER BY started_at DESC NULLS LAST LIMIT 1)
 AND jsonb_array_length(coalesce(result->'signals','[]'::jsonb))>0
);
UPDATE evidence_queue SET priority=2 WHERE kind='company' AND doc_key LIKE '300138.SZ:%';
CREATE OR REPLACE FUNCTION enqueue_evidence(k text,d text) RETURNS void LANGUAGE sql AS $$
 INSERT INTO evidence_queue(kind,doc_key,priority) VALUES(k,d,CASE k WHEN 'news' THEN 0 WHEN 'research' THEN 1 ELSE 5 END)
 ON CONFLICT(kind,doc_key) DO UPDATE SET generation=evidence_queue.generation+1,state='pending',attempts=0,
 next_at=now(),updated_at=now(),error=NULL,priority=least(evidence_queue.priority,excluded.priority)
$$;
CREATE INDEX evidence_queue_priority ON evidence_queue(priority,updated_at) WHERE state IN ('pending','failed','running');
