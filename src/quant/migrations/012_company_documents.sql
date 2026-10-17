CREATE TABLE company_page_revision (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    symbol text NOT NULL REFERENCES security(symbol),
    page text NOT NULL CHECK(page IN ('operate','company')),
    source_url text NOT NULL,
    sha256 text NOT NULL,
    body bytea NOT NULL,
    encoding text NOT NULL,
    collected_at timestamptz NOT NULL DEFAULT now(),
    parser_version text NOT NULL,
    UNIQUE(symbol,page,sha256)
);
CREATE TABLE company_document (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    page_id bigint NOT NULL REFERENCES company_page_revision(id),
    symbol text NOT NULL REFERENCES security(symbol),
    kind text NOT NULL CHECK(kind IN ('business','discussion','company','executives')),
    report_key text NOT NULL DEFAULT '',
    report_date date,
    title text NOT NULL,
    content text NOT NULL,
    details jsonb NOT NULL DEFAULT '{}',
    is_excerpt boolean NOT NULL DEFAULT false,
    UNIQUE(page_id,kind,report_key)
);
CREATE INDEX company_document_lookup ON company_document(symbol,kind,report_date DESC);
CREATE TABLE company_refresh (
    symbol text PRIMARY KEY REFERENCES security(symbol),
    job_id uuid NOT NULL,
    status text NOT NULL CHECK(status IN ('queued','running','completed','partial','failed')),
    requested_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    last_success_at timestamptz,
    error text,
    pages_saved integer NOT NULL DEFAULT 0
);
