DROP TRIGGER research_metadata_index_changed ON research_document;
CREATE TRIGGER research_metadata_index_changed AFTER UPDATE OF metadata_sha256,content_url ON research_document FOR EACH ROW
 WHEN(OLD.metadata_sha256 IS DISTINCT FROM NEW.metadata_sha256 OR OLD.content_url IS DISTINCT FROM NEW.content_url)
 EXECUTE FUNCTION evidence_changed();
