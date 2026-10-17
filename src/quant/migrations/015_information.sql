-- Extension is provisioned by the cluster administrator, never elevate quant_app.
CREATE TABLE news_source_state (
 source text PRIMARY KEY, watermark timestamptz, checked_at timestamptz, next_at timestamptz NOT NULL DEFAULT now(),
 failures integer NOT NULL DEFAULT 0, error text, coverage text NOT NULL DEFAULT '尚未采集'
);
INSERT INTO news_source_state(source) VALUES ('ths'),('cls'),('sina');
CREATE TABLE news_article (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, source text NOT NULL, source_id text NOT NULL,
 first_seen_at timestamptz NOT NULL DEFAULT now(), last_seen_at timestamptz NOT NULL DEFAULT now(),
 current_revision bigint, UNIQUE(source,source_id)
);
CREATE TABLE news_revision (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, article_id bigint NOT NULL REFERENCES news_article(id),
 sha256 text NOT NULL,title text NOT NULL,content text NOT NULL,source_url text NOT NULL,
 published_at timestamptz NOT NULL,collected_at timestamptz NOT NULL DEFAULT now(),raw jsonb NOT NULL,
 UNIQUE(article_id,sha256)
);
ALTER TABLE news_article ADD FOREIGN KEY(current_revision) REFERENCES news_revision(id);
CREATE INDEX news_revision_time ON news_revision(published_at DESC);
CREATE TABLE news_poll (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,source text NOT NULL, started_at timestamptz NOT NULL DEFAULT now(),
 finished_at timestamptz,target timestamptz,oldest timestamptz,newest timestamptz, pages integer NOT NULL DEFAULT 0,
 saved integer NOT NULL DEFAULT 0,status text NOT NULL DEFAULT 'running',error text
);
CREATE TABLE evidence_queue (
 kind text NOT NULL CHECK(kind IN ('news','company','research')), doc_key text NOT NULL,
 generation bigint NOT NULL DEFAULT 1, state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','running','ready','failed')),
 lease uuid, lease_until timestamptz,attempts integer NOT NULL DEFAULT 0, next_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),error text,PRIMARY KEY(kind,doc_key)
);
CREATE INDEX evidence_queue_due ON evidence_queue(next_at) WHERE state IN ('pending','failed','running');
CREATE FUNCTION enqueue_evidence(k text,d text) RETURNS void LANGUAGE sql AS $$
 INSERT INTO evidence_queue(kind,doc_key) VALUES(k,d) ON CONFLICT(kind,doc_key) DO UPDATE SET
 generation=evidence_queue.generation+1,state='pending',attempts=0,next_at=now(),updated_at=now(),error=NULL
$$;
CREATE FUNCTION evidence_changed() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_TABLE_NAME='news_article' THEN
  PERFORM enqueue_evidence('news',NEW.id::text);
 ELSIF TG_TABLE_NAME='company_document' THEN
  IF NEW.kind IN ('discussion','business','company') THEN PERFORM enqueue_evidence('company',NEW.symbol||':'||NEW.kind||':'||NEW.report_key); END IF;
 ELSIF TG_TABLE_NAME='research_revision' THEN
  PERFORM enqueue_evidence('research',NEW.document_id::text);
 ELSIF TG_TABLE_NAME='research_document' THEN
  PERFORM enqueue_evidence('research',NEW.id::text);
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER news_index_changed AFTER UPDATE OF current_revision ON news_article FOR EACH ROW
 WHEN(OLD.current_revision IS DISTINCT FROM NEW.current_revision) EXECUTE FUNCTION evidence_changed();
CREATE TRIGGER company_index_changed AFTER INSERT ON company_document FOR EACH ROW EXECUTE FUNCTION evidence_changed();
CREATE TRIGGER research_index_changed AFTER INSERT OR UPDATE OF status,extraction ON research_revision FOR EACH ROW EXECUTE FUNCTION evidence_changed();
CREATE TABLE embedding_cache (
 profile text NOT NULL,sha256 text NOT NULL,embedding public.vector(1024) NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(profile,sha256)
);
CREATE TABLE evidence_chunk (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,kind text NOT NULL,doc_key text NOT NULL,revision text NOT NULL,
 ordinal integer NOT NULL,profile text NOT NULL,sha256 text NOT NULL,symbol text,title text NOT NULL,content text NOT NULL,
 source_url text NOT NULL,published_at timestamptz,known_at timestamptz NOT NULL,indexed_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(profile,sha256) REFERENCES embedding_cache(profile,sha256),UNIQUE(kind,doc_key,ordinal)
);
CREATE INDEX evidence_chunk_symbol ON evidence_chunk(symbol,kind);
CREATE INDEX embedding_cache_hnsw ON embedding_cache USING hnsw(embedding public.vector_cosine_ops);
CREATE TABLE evidence_analysis (
 id uuid PRIMARY KEY,created_at timestamptz NOT NULL DEFAULT now(),query text NOT NULL,as_of timestamptz NOT NULL,
 profile text NOT NULL,model text NOT NULL,prompt_version text NOT NULL,news_revision bigint REFERENCES news_revision(id),
 evidence jsonb NOT NULL,result jsonb NOT NULL
);
CREATE TABLE information_runtime (
 id integer PRIMARY KEY CHECK(id=1),enabled boolean NOT NULL DEFAULT false,heartbeat_at timestamptz,
 error text,index_profile text,created_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO information_runtime(id) VALUES(1);
CREATE TABLE research_checkpoint (
 symbol text PRIMARY KEY REFERENCES security(symbol),checked_through date,full_checked_at timestamptz
);
ALTER TABLE research_document ADD COLUMN metadata_sha256 text;
ALTER TABLE research_document ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE research_document ADD COLUMN content_checked_at timestamptz;
ALTER TABLE research_document ADD COLUMN content_url text;
CREATE TRIGGER research_metadata_index_changed AFTER UPDATE OF metadata_sha256 ON research_document FOR EACH ROW
 WHEN(OLD.metadata_sha256 IS DISTINCT FROM NEW.metadata_sha256) EXECUTE FUNCTION evidence_changed();
CREATE TABLE research_metadata_revision (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,document_id bigint NOT NULL REFERENCES research_document(id),
 sha256 text NOT NULL,metadata jsonb NOT NULL,collected_at timestamptz NOT NULL DEFAULT now(),UNIQUE(document_id,sha256)
);
UPDATE research_document d SET content_checked_at=r.collected_at,content_url=d.pdf_url
 FROM (SELECT document_id,max(collected_at) AS collected_at FROM research_revision GROUP BY document_id) r WHERE r.document_id=d.id;
-- Triggers precede bootstrap so concurrent writes cannot fall into a scan gap.
INSERT INTO evidence_queue(kind,doc_key)
 SELECT DISTINCT 'company',symbol||':'||kind||':'||report_key FROM company_document WHERE kind IN ('discussion','business','company') ON CONFLICT DO NOTHING;
INSERT INTO evidence_queue(kind,doc_key)
 SELECT DISTINCT 'research',document_id::text FROM research_revision WHERE status='ready' ON CONFLICT DO NOTHING;
INSERT INTO research_checkpoint(symbol,checked_through,full_checked_at)
 SELECT DISTINCT ON(i.symbol) i.symbol,j.end_date,i.finished_at FROM research_item i JOIN research_job j ON j.id=i.job_id
 WHERE i.status IN ('saved','no_records') AND i.finished_at IS NOT NULL ORDER BY i.symbol,i.finished_at DESC;
