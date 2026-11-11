"""Meaning-based retrieval: passage vectors, one 'card' per document, and chat focus.

Vectors are plain real[] (no pgvector): a project's documents and one document's passages are
small enough to compare in the application. The embedder service fills them in the background.
"""

from alembic import op

revision = "0013_semantic_routing"
down_revision = "0012_forgiving_search"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE workspace.text_chunks ADD COLUMN embedding real[], ADD COLUMN embedding_model text;
    CREATE INDEX text_chunks_unembedded_idx ON workspace.text_chunks(version_id) WHERE embedding IS NULL;
    CREATE TABLE workspace.document_cards (
        version_id uuid PRIMARY KEY REFERENCES workspace.document_versions(id),
        card_text text NOT NULL,
        embedding real[],
        embedding_model text,
        created_at timestamptz NOT NULL DEFAULT now()
    );
    ALTER TABLE workspace.chat_threads
        ADD COLUMN focus_document_ids uuid[] NOT NULL DEFAULT '{}';
    ALTER TABLE workspace.ai_tasks ADD COLUMN clarification jsonb;
    """)


def downgrade() -> None:
    op.execute("""
    ALTER TABLE workspace.ai_tasks DROP COLUMN clarification;
    ALTER TABLE workspace.chat_threads DROP COLUMN focus_document_ids;
    DROP TABLE workspace.document_cards;
    DROP INDEX workspace.text_chunks_unembedded_idx;
    ALTER TABLE workspace.text_chunks DROP COLUMN embedding, DROP COLUMN embedding_model;
    """)
