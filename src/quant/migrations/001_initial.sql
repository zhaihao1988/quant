CREATE TABLE security (
    symbol text PRIMARY KEY, code text NOT NULL, market text NOT NULL CHECK(market IN ('SH','SZ','BJ')),
    name text NOT NULL, kind text NOT NULL DEFAULT 'stock', listing_date date,
    updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(market,code)
);
CREATE TABLE daily_bar (
    symbol text NOT NULL REFERENCES security(symbol), trade_date date NOT NULL,
    open numeric(14,4) NOT NULL, high numeric(14,4) NOT NULL, low numeric(14,4) NOT NULL,
    close numeric(14,4) NOT NULL, volume bigint NOT NULL CHECK(volume>=0), amount double precision NOT NULL CHECK(amount>=0),
    source text NOT NULL DEFAULT 'tdx_day', imported_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(symbol,trade_date), CHECK(low<=high), CHECK(low<=open AND open<=high), CHECK(low<=close AND close<=high)
);
CREATE INDEX daily_bar_date_idx ON daily_bar(trade_date,symbol);
CREATE TABLE capital_event (
    symbol text NOT NULL REFERENCES security(symbol), event_date date NOT NULL, category smallint NOT NULL,
    c1 double precision NOT NULL, c2 double precision NOT NULL, c3 double precision NOT NULL, c4 double precision NOT NULL,
    source text NOT NULL DEFAULT 'tdx_gbbq', observed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(symbol,event_date,category)
);
CREATE TABLE share_observation (
    symbol text NOT NULL REFERENCES security(symbol), effective_date date NOT NULL,
    float_shares bigint CHECK(float_shares>0), total_shares bigint CHECK(total_shares>0),
    source text NOT NULL, quality text NOT NULL, observed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(symbol,effective_date,source)
);
CREATE TABLE import_file (
    path text PRIMARY KEY, sha256 text NOT NULL, byte_size bigint NOT NULL, record_count bigint NOT NULL,
    latest_date date, imported_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE import_run (
    id uuid PRIMARY KEY, status text NOT NULL, stage text NOT NULL DEFAULT 'queued',
    processed integer NOT NULL DEFAULT 0, total integer NOT NULL DEFAULT 0,
    changed_rows bigint NOT NULL DEFAULT 0, skipped integer NOT NULL DEFAULT 0,
    errors jsonb NOT NULL DEFAULT '[]', started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz
);
CREATE TABLE taxonomy (
    id text PRIMARY KEY, name text NOT NULL, kind text NOT NULL, source text NOT NULL
);
CREATE TABLE membership (
    taxonomy_id text NOT NULL REFERENCES taxonomy(id), symbol text NOT NULL REFERENCES security(symbol),
    snapshot_date date NOT NULL, source text NOT NULL, PRIMARY KEY(taxonomy_id,symbol,snapshot_date)
);
CREATE TABLE graph_node (
    id text PRIMARY KEY, name text NOT NULL, kind text NOT NULL, aliases jsonb NOT NULL DEFAULT '[]'
);
CREATE TABLE graph_edge (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id text NOT NULL REFERENCES graph_node(id), target_id text NOT NULL REFERENCES graph_node(id),
    relation text NOT NULL, evidence text NOT NULL, evidence_url text,
    observed_at timestamptz NOT NULL DEFAULT now(), UNIQUE(source_id,target_id,relation,evidence)
);
CREATE TABLE news_input (
    id uuid PRIMARY KEY, title text NOT NULL, content text NOT NULL, source_url text,
    published_at timestamptz, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE screen_run (
    id uuid PRIMARY KEY, requested_date date, effective_date date NOT NULL, parameters jsonb NOT NULL,
    result jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
