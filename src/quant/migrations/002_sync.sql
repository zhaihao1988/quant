ALTER TABLE import_file ADD COLUMN source_mtime_ns bigint;
ALTER TABLE import_file ADD COLUMN block_hashes jsonb;
ALTER TABLE import_run ADD COLUMN mode text NOT NULL DEFAULT 'incremental';
ALTER TABLE import_run ADD COLUMN symbols jsonb;
ALTER TABLE import_run ADD COLUMN retry_of uuid REFERENCES import_run(id);
ALTER TABLE import_run ADD COLUMN heartbeat_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE import_run ADD COLUMN parsed_rows bigint NOT NULL DEFAULT 0;
CREATE TABLE import_item (
    run_id uuid NOT NULL REFERENCES import_run(id), symbol text NOT NULL,
    state text NOT NULL, changed_rows bigint NOT NULL DEFAULT 0,
    parsed_rows bigint NOT NULL DEFAULT 0, error text,
    PRIMARY KEY(run_id,symbol)
);
CREATE TABLE source_inventory (
    symbol text PRIMARY KEY, name text NOT NULL, market text NOT NULL, path text,
    db_date date, local_date date, local_records bigint, db_records bigint,
    source_mtime_ns bigint, state text NOT NULL, reason text,
    scanned_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE sync_scan (
    id integer PRIMARY KEY CHECK(id=1), status text NOT NULL,
    started_at timestamptz, finished_at timestamptz, error text
);
