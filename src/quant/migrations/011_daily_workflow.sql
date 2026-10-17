ALTER TABLE multi_screen_run ADD COLUMN input_signature text;
ALTER TABLE multi_screen_run ADD COLUMN scope_signature text;
ALTER TABLE multi_screen_run ADD COLUMN input_manifest jsonb;
ALTER TABLE multi_screen_run ADD COLUMN parent_run_id uuid REFERENCES multi_screen_run(id);
ALTER TABLE multi_screen_run ADD COLUMN version integer NOT NULL DEFAULT 1;
ALTER TABLE multi_screen_run ADD COLUMN archive_state text NOT NULL DEFAULT 'legacy';
ALTER TABLE multi_screen_run ADD COLUMN archive_error text;
CREATE INDEX multi_screen_signature_idx ON multi_screen_run(input_signature,finished_at DESC);
CREATE TABLE screen_archive (
 run_id uuid NOT NULL REFERENCES multi_screen_run(id),revision integer NOT NULL,
 finished_at timestamptz NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
 payload jsonb NOT NULL,sha256 text NOT NULL,export_path text,export_error text,
 PRIMARY KEY(run_id,revision),UNIQUE(run_id,finished_at)
);
ALTER TABLE market_prepare_run ADD COLUMN force_recompute boolean NOT NULL DEFAULT false;
ALTER TABLE market_prepare_run ADD COLUMN cancel_requested boolean NOT NULL DEFAULT false;
ALTER TABLE import_run ADD COLUMN cancel_requested boolean NOT NULL DEFAULT false;
CREATE INDEX multi_screen_scope_idx ON multi_screen_run(scope_signature,effective_date DESC);
CREATE TABLE calendar_year (
 year integer PRIMARY KEY,source text NOT NULL,days jsonb NOT NULL,
 observed_at timestamptz NOT NULL DEFAULT now()
);
