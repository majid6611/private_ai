"""Stemmed full-text search plus trigram matching, so word forms and typos still match.

Requires the pg_trgm extension. It is installed in the workspace schema by the migration
role, which needs CREATE on the database (granted by deploy/init-db.sh; for a database
created before this migration run once as the DB owner:
    GRANT CREATE ON DATABASE <name> TO workspace_migration;
)
"""

from alembic import op

revision = "0012_forgiving_search"
down_revision = "0011_source_rank"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE EXTENSION IF NOT EXISTS pg_trgm SCHEMA workspace;
    ALTER TABLE workspace.text_chunks ADD COLUMN search_en tsvector
        GENERATED ALWAYS AS (to_tsvector('english', content)) STORED;
    CREATE INDEX text_chunks_search_en_idx ON workspace.text_chunks USING gin(search_en);
    CREATE INDEX text_chunks_trgm_idx ON workspace.text_chunks
        USING gin(content workspace.gin_trgm_ops);
    ALTER TABLE workspace.documents ADD COLUMN title_en tsvector
        GENERATED ALWAYS AS (to_tsvector('english', title)) STORED;
    CREATE INDEX documents_title_en_idx ON workspace.documents USING gin(title_en);
    """)


def downgrade() -> None:
    op.execute("""
    DROP INDEX workspace.documents_title_en_idx;
    ALTER TABLE workspace.documents DROP COLUMN title_en;
    DROP INDEX workspace.text_chunks_trgm_idx;
    DROP INDEX workspace.text_chunks_search_en_idx;
    ALTER TABLE workspace.text_chunks DROP COLUMN search_en;
    DROP EXTENSION pg_trgm;
    """)
