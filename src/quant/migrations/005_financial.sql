CREATE TABLE financial_import_run (
 id uuid PRIMARY KEY,status text NOT NULL,total integer NOT NULL DEFAULT 0,processed integer NOT NULL DEFAULT 0,
 reports bigint NOT NULL DEFAULT 0,skipped integer NOT NULL DEFAULT 0,errors jsonb NOT NULL DEFAULT '[]',
 started_at timestamptz NOT NULL DEFAULT now(),finished_at timestamptz
);
CREATE TABLE financial_file_revision (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,path text NOT NULL,sha256 text NOT NULL,
 report_date date NOT NULL,byte_size bigint NOT NULL,record_count integer NOT NULL,field_count integer NOT NULL,
 format_version integer NOT NULL,observed_at timestamptz NOT NULL DEFAULT now(),is_current boolean NOT NULL DEFAULT true,
 UNIQUE(path,sha256)
);
CREATE UNIQUE INDEX financial_file_current_idx ON financial_file_revision(report_date) WHERE is_current;
CREATE TABLE financial_report (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,file_id bigint NOT NULL REFERENCES financial_file_revision(id),
 code text NOT NULL,raw_tag smallint NOT NULL,symbol text REFERENCES security(symbol),report_date date NOT NULL,
 announced_on date,field_count integer NOT NULL,payload bytea NOT NULL,standard jsonb NOT NULL,
 mapping_version text NOT NULL,UNIQUE(file_id,code,raw_tag)
);
CREATE INDEX financial_report_symbol_idx ON financial_report(symbol,report_date DESC,file_id DESC);
CREATE INDEX financial_report_announcement_idx ON financial_report(symbol,announced_on,report_date);
CREATE VIEW financial_current_report AS SELECT r.*,f.observed_at FROM financial_report r
 JOIN financial_file_revision f ON r.file_id=f.id WHERE f.is_current;
