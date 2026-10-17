ALTER TABLE news_article ADD COLUMN current_seen_at timestamptz NOT NULL DEFAULT now();
UPDATE news_article a SET current_seen_at=r.collected_at FROM news_revision r WHERE r.id=a.current_revision;
CREATE TABLE news_revision_observation (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,article_id bigint NOT NULL REFERENCES news_article(id),
 revision_id bigint NOT NULL REFERENCES news_revision(id),observed_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO news_revision_observation(article_id,revision_id,observed_at)
 SELECT id,current_revision,current_seen_at FROM news_article WHERE current_revision IS NOT NULL;
CREATE FUNCTION news_current_seen() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN NEW.current_seen_at=now(); RETURN NEW; END $$;
CREATE FUNCTION news_current_observed() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 INSERT INTO news_revision_observation(article_id,revision_id,observed_at) VALUES(NEW.id,NEW.current_revision,NEW.current_seen_at);
 RETURN NEW;
END $$;
CREATE TRIGGER news_revision_seen BEFORE UPDATE OF current_revision ON news_article FOR EACH ROW
 WHEN(OLD.current_revision IS DISTINCT FROM NEW.current_revision) EXECUTE FUNCTION news_current_seen();
CREATE TRIGGER news_revision_observed AFTER UPDATE OF current_revision ON news_article FOR EACH ROW
 WHEN(OLD.current_revision IS DISTINCT FROM NEW.current_revision) EXECUTE FUNCTION news_current_observed();
