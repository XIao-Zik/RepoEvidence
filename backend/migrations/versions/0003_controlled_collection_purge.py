"""Permit only the audited purge function to remove obsolete immutable evidence."""
# ruff: noqa: E501

import psycopg
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade(database: str) -> None:
    if database != "knowledge":
        return
    driver = op.get_bind().connection.driver_connection
    if not isinstance(driver, psycopg.Connection):
        raise RuntimeError("Migrations require a PostgreSQL psycopg connection")
    driver.execute("""
    CREATE OR REPLACE FUNCTION guard_collection() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='DELETE' THEN
        IF current_setting('repo_evidence.cleanup', true)='on' THEN
          RETURN OLD;
        END IF;
        RAISE EXCEPTION 'Knowledge snapshots cannot be deleted';
      END IF;
      IF OLD.status='ready' THEN
        RAISE EXCEPTION 'Ready collection is immutable';
      END IF;
      IF (to_jsonb(NEW)-'status'-'ready_at') IS DISTINCT FROM (to_jsonb(OLD)-'status'-'ready_at') THEN
        RAISE EXCEPTION 'Build configuration is immutable';
      END IF;
      IF NEW.status='ready' THEN
        IF NOT EXISTS(SELECT 1 FROM documents WHERE collection_id=NEW.collection_id)
          OR EXISTS(SELECT 1 FROM documents d WHERE d.collection_id=NEW.collection_id AND NOT EXISTS
              (SELECT 1 FROM chunks c WHERE c.collection_id=d.collection_id AND c.doc_id=d.doc_id))
          OR EXISTS(SELECT 1 FROM chunks WHERE collection_id=NEW.collection_id AND embedding IS NULL)
          OR EXISTS(SELECT 1 FROM chunks WHERE collection_id=NEW.collection_id GROUP BY doc_id
                    HAVING min(chunk_no)<>1 OR max(chunk_no)<>count(*)) THEN
          RAISE EXCEPTION 'Collection incomplete';
        END IF;
      END IF;
      RETURN NEW;
    END $$;
    CREATE OR REPLACE FUNCTION guard_knowledge_row() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE collection collections%ROWTYPE;
    BEGIN
      IF TG_OP='DELETE' THEN
        IF current_setting('repo_evidence.cleanup', true)='on' THEN
          RETURN OLD;
        END IF;
        RAISE EXCEPTION 'Knowledge rows cannot be deleted';
      END IF;
      SELECT * INTO STRICT collection FROM collections WHERE collection_id=NEW.collection_id FOR UPDATE;
      IF collection.status<>'building' THEN
        RAISE EXCEPTION 'Ready collection is immutable';
      END IF;
      IF TG_TABLE_NAME='documents' AND TG_OP='UPDATE' THEN
        RAISE EXCEPTION 'Document is immutable';
      END IF;
      IF TG_TABLE_NAME='chunks' THEN
        IF TG_OP='UPDATE' AND ((to_jsonb(NEW)-'embedding') IS DISTINCT FROM (to_jsonb(OLD)-'embedding')
                              OR OLD.embedding IS NOT NULL) THEN
          RAISE EXCEPTION 'Only missing embeddings may be filled';
        END IF;
        IF NEW.embedding IS NOT NULL AND
           (vector_dims(NEW.embedding)<>collection.dimensions OR vector_norm(NEW.embedding)=0) THEN
          RAISE EXCEPTION 'Invalid vector dimensions or norm';
        END IF;
      END IF;
      RETURN NEW;
    END $$;
    CREATE FUNCTION purge_obsolete_collection(target_collection_id text) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
    BEGIN
      IF target_collection_id IS NULL OR target_collection_id='' THEN RAISE EXCEPTION 'Collection ID is required'; END IF;
      PERFORM set_config('repo_evidence.cleanup', 'on', true);
      DELETE FROM chunks WHERE collection_id=target_collection_id;
      DELETE FROM documents WHERE collection_id=target_collection_id;
      DELETE FROM collections WHERE collection_id=target_collection_id;
      IF NOT FOUND THEN RAISE EXCEPTION 'Unknown collection'; END IF;
    END $$;
    REVOKE ALL ON FUNCTION purge_obsolete_collection(text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION purge_obsolete_collection(text) TO repo_evidence_knowledge_builder;
    """)


def downgrade(database: str) -> None:
    _ = database
    raise RuntimeError("Restore from backup instead of destructive downgrade")
