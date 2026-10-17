CREATE TABLE research_document (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 source text NOT NULL DEFAULT 'cninfo', source_id text NOT NULL,
 title text NOT NULL, published_at timestamptz NOT NULL,
 source_url text NOT NULL, pdf_url text NOT NULL,
 first_seen_at timestamptz NOT NULL DEFAULT now(), last_seen_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(source,source_id)
);
CREATE TABLE research_security (
 document_id bigint NOT NULL REFERENCES research_document(id),
 symbol text NOT NULL REFERENCES security(symbol),
 PRIMARY KEY(document_id,symbol)
);
CREATE INDEX research_security_symbol ON research_security(symbol);
CREATE TABLE research_revision (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 document_id bigint NOT NULL REFERENCES research_document(id),
 sha256 text NOT NULL, body bytea NOT NULL, pages jsonb NOT NULL,
 parser_version text NOT NULL, model text NOT NULL,
 status text NOT NULL CHECK(status IN ('pending','ready','no_qa','needs_ocr','failed')),
 extraction jsonb NOT NULL DEFAULT '{}', error text,
 collected_at timestamptz NOT NULL DEFAULT now(), parsed_at timestamptz,
 UNIQUE(document_id,sha256,parser_version,model)
);
CREATE INDEX research_revision_document ON research_revision(document_id,id DESC);
CREATE TABLE research_job (
 id uuid PRIMARY KEY, status text NOT NULL CHECK(status IN ('queued','running','paused','completed','partial','failed')),
 start_date date NOT NULL, end_date date NOT NULL, total integer NOT NULL,
 pause_requested boolean NOT NULL DEFAULT false, message text,
 created_at timestamptz NOT NULL DEFAULT now(), started_at timestamptz,
 heartbeat_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz
);
CREATE TABLE research_item (
 job_id uuid NOT NULL REFERENCES research_job(id), symbol text NOT NULL REFERENCES security(symbol),
 ordinal integer NOT NULL,
 status text NOT NULL CHECK(status IN ('pending','running','saved','no_records','failed')),
 documents integer NOT NULL DEFAULT 0, ready integer NOT NULL DEFAULT 0,
 attempts integer NOT NULL DEFAULT 0, error text, started_at timestamptz, finished_at timestamptz,
 PRIMARY KEY(job_id,symbol)
);
CREATE INDEX research_item_pending ON research_item(job_id,status,ordinal);
