"""Leased ingestion jobs and source-bound lexical chunks; vectors follow evaluation."""

from alembic import op

revision = "0005_text_index"
down_revision = "0004_approvals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE workspace.intake_jobs ADD COLUMN attempts integer NOT NULL DEFAULT 0,
        ADD COLUMN lease_hash char(64), ADD COLUMN lease_expires_at timestamptz;
    CREATE TABLE workspace.text_chunks (
        id uuid PRIMARY KEY, version_id uuid NOT NULL REFERENCES workspace.document_versions(id),
        ordinal integer NOT NULL CHECK(ordinal >= 0),
        start_offset integer NOT NULL CHECK(start_offset >= 0),
        end_offset integer NOT NULL CHECK(end_offset > start_offset),
        content text NOT NULL,
        search_vector tsvector GENERATED ALWAYS AS (to_tsvector('simple',content)) STORED,
        UNIQUE(version_id,ordinal)
    );
    CREATE INDEX text_chunks_search_idx ON workspace.text_chunks USING gin(search_vector);
    CREATE INDEX intake_jobs_status_idx ON workspace.intake_jobs(status,lease_expires_at);
    """)


def downgrade() -> None:
    op.execute("""DROP TABLE workspace.text_chunks;
        DROP INDEX workspace.intake_jobs_status_idx;
        ALTER TABLE workspace.intake_jobs DROP COLUMN attempts,
            DROP COLUMN lease_hash, DROP COLUMN lease_expires_at;""")
