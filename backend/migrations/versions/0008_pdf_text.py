"""Derived PDF text: extracted by the isolated parser, bound to the immutable original."""

from alembic import op

revision = "0008_pdf_text"
down_revision = "0007_title_search"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE workspace.version_texts (
        version_id uuid PRIMARY KEY REFERENCES workspace.document_versions(id),
        extractor text NOT NULL, text text NOT NULL,
        page_count integer NOT NULL CHECK(page_count >= 0),
        created_at timestamptz NOT NULL DEFAULT now()
    );
    ALTER TABLE workspace.text_chunks ADD COLUMN page_start integer, ADD COLUMN page_end integer;
    ALTER TABLE workspace.intake_jobs ADD COLUMN failure_code text;
    """)


def downgrade() -> None:
    op.execute("""ALTER TABLE workspace.intake_jobs DROP COLUMN failure_code;
        ALTER TABLE workspace.text_chunks DROP COLUMN page_start, DROP COLUMN page_end;
        DROP TABLE workspace.version_texts;""")
