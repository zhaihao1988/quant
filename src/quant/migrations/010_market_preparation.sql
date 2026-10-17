CREATE TABLE market_prepare_run (
 id uuid PRIMARY KEY,status text NOT NULL,stage text NOT NULL DEFAULT '等待核对',
 parameters jsonb,import_id uuid REFERENCES import_run(id),
 screen_id uuid REFERENCES multi_screen_run(id),
 result jsonb NOT NULL DEFAULT '{}',error text,
 started_at timestamptz NOT NULL DEFAULT now(),
 heartbeat_at timestamptz NOT NULL DEFAULT now(),finished_at timestamptz
);
