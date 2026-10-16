CREATE TABLE capital_file_revision (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 path text NOT NULL,sha256 text NOT NULL,byte_size bigint NOT NULL,
 source_modified_at timestamptz NOT NULL,observed_at timestamptz NOT NULL DEFAULT now(),
 record_count integer NOT NULL,payload bytea NOT NULL,UNIQUE(path,sha256)
);
CREATE TABLE capital_snapshot (
 file_id bigint NOT NULL REFERENCES capital_file_revision(id),
 symbol text NOT NULL REFERENCES security(symbol),
 total_shares bigint CHECK(total_shares>=0),float_shares bigint CHECK(float_shares>=0),
 b_shares bigint CHECK(b_shares>=0),h_shares bigint CHECK(h_shares>=0),
 source_updated_on date,raw jsonb NOT NULL,PRIMARY KEY(file_id,symbol)
);
CREATE INDEX capital_snapshot_symbol_idx ON capital_snapshot(symbol,file_id DESC);
CREATE TABLE fundamental_refresh_run (
 id uuid PRIMARY KEY,status text NOT NULL,stage text NOT NULL DEFAULT '排队',
 total integer NOT NULL DEFAULT 0,processed integer NOT NULL DEFAULT 0,
 result jsonb NOT NULL DEFAULT '{}',errors jsonb NOT NULL DEFAULT '[]',
 started_at timestamptz NOT NULL DEFAULT now(),finished_at timestamptz,
 heartbeat_at timestamptz NOT NULL DEFAULT now()
);
