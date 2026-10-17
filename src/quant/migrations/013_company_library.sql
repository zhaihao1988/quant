CREATE TABLE company_collection_job (
 id uuid PRIMARY KEY, status text NOT NULL CHECK(status IN ('queued','running','paused','completed','partial','failed')),
 mode text NOT NULL CHECK(mode IN ('due','retry')),total integer NOT NULL DEFAULT 0,
 pause_requested boolean NOT NULL DEFAULT false,created_at timestamptz NOT NULL DEFAULT now(),
 started_at timestamptz,heartbeat_at timestamptz,finished_at timestamptz,message text
);
CREATE TABLE company_collection_item (
 job_id uuid NOT NULL REFERENCES company_collection_job(id),symbol text NOT NULL REFERENCES security(symbol),
 status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','saved','cached','no_content','failed')),
 attempts integer NOT NULL DEFAULT 0,report_count integer NOT NULL DEFAULT 0,error text,
 started_at timestamptz,finished_at timestamptz,
 PRIMARY KEY(job_id,symbol)
);
CREATE INDEX company_collection_recent ON company_collection_item(symbol,finished_at DESC);
CREATE INDEX company_collection_pending ON company_collection_item(job_id,status,symbol);
