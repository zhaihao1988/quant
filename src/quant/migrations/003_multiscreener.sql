CREATE TABLE multi_screen_run (
    id uuid PRIMARY KEY, status text NOT NULL, requested_date date, effective_date date NOT NULL,
    parameters jsonb NOT NULL, total integer NOT NULL DEFAULT 0, processed integer NOT NULL DEFAULT 0,
    result jsonb NOT NULL DEFAULT '{"signals":[],"errors":[],"coverage":[]}',
    current_symbol text, cancel_requested boolean NOT NULL DEFAULT false,
    started_at timestamptz NOT NULL DEFAULT now(), heartbeat_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);
