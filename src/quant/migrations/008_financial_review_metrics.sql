ALTER TABLE financial_report ADD COLUMN review_state text NOT NULL DEFAULT 'unreviewed';
ALTER TABLE financial_report ADD COLUMN reviewed_at timestamptz;
CREATE INDEX financial_report_reviewed_at_idx ON financial_report(reviewed_at DESC);
ALTER TABLE financial_report ADD CONSTRAINT financial_review_state_check CHECK(review_state IN ('unreviewed','verified','pending_conflict'));
CREATE OR REPLACE VIEW financial_current_report AS
 SELECT r.id,r.file_id,r.code,r.raw_tag,r.symbol,r.report_date,r.announced_on,
 r.field_count,r.payload,r.standard,r.mapping_version,f.observed_at,r.source_ordinal,r.review_state,r.reviewed_at
 FROM financial_report r JOIN financial_file_revision f ON r.file_id=f.id WHERE f.is_current;
CREATE TABLE financial_metric_cache (
 symbol text NOT NULL REFERENCES security(symbol),as_of date NOT NULL,signature text NOT NULL,
 result jsonb NOT NULL,computed_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(symbol,as_of,signature)
);
