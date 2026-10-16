CREATE OR REPLACE VIEW financial_current_report AS
 SELECT r.id,r.file_id,r.code,r.raw_tag,r.symbol,r.report_date,r.announced_on,
 r.field_count,r.payload,r.standard,r.mapping_version,f.observed_at,r.source_ordinal
 FROM financial_report r JOIN financial_file_revision f ON r.file_id=f.id WHERE f.is_current;
