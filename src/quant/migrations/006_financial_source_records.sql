-- Vendor files may repeat a security within one report period; retain each source row.
ALTER TABLE financial_report ADD COLUMN source_ordinal integer;
WITH numbered AS (SELECT id,row_number() OVER(PARTITION BY file_id ORDER BY id) AS ordinal FROM financial_report)
UPDATE financial_report r SET source_ordinal=n.ordinal FROM numbered n WHERE r.id=n.id;
ALTER TABLE financial_report ALTER COLUMN source_ordinal SET NOT NULL;
ALTER TABLE financial_report DROP CONSTRAINT financial_report_file_id_code_raw_tag_key;
ALTER TABLE financial_report ADD UNIQUE(file_id,source_ordinal);
