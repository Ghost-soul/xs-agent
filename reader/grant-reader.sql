-- Run manually as database administrator in the existing application database.
-- This creates no tables and does not modify novel content. Choose the login
-- password interactively afterwards with psql's: \password novel_reader
BEGIN;
CREATE ROLE novel_reader LOGIN;
ALTER ROLE novel_reader SET default_transaction_read_only = on;
GRANT USAGE ON SCHEMA public TO novel_reader;
GRANT SELECT (id, title, current_version_id, archived_at, created_at)
  ON public.story_projects TO novel_reader;
GRANT SELECT (id, project_id, number, chapter_revisions, chapter_titles)
  ON public.state_versions TO novel_reader;
GRANT SELECT (id, project_id, ordinal, display_ordinal, title)
  ON public.chapters TO novel_reader;
GRANT SELECT (id, chapter_id, body) ON public.chapter_revisions TO novel_reader;
COMMIT;
