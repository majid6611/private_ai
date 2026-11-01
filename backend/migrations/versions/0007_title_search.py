"""Searchable document titles: chunks rarely repeat the parties/subject named in the title."""

from alembic import op

revision = "0007_title_search"
down_revision = "0006_ai_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE workspace.documents ADD COLUMN title_vector tsvector
        GENERATED ALWAYS AS (to_tsvector('simple',title)) STORED;
    CREATE INDEX documents_title_search_idx ON workspace.documents USING gin(title_vector);
    """)


def downgrade() -> None:
    op.execute("""DROP INDEX workspace.documents_title_search_idx;
        ALTER TABLE workspace.documents DROP COLUMN title_vector;""")
