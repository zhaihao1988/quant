CREATE TABLE multi_screen_item (
    run_id uuid NOT NULL REFERENCES multi_screen_run(id) ON DELETE CASCADE,
    ordinal integer NOT NULL, symbol text NOT NULL,
    result jsonb NOT NULL,
    PRIMARY KEY(run_id,symbol)
);
CREATE INDEX multi_screen_item_order_idx ON multi_screen_item(run_id,ordinal);
