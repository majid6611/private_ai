"""User-bound, expiring AI tasks and immutable retrieval provenance."""

from alembic import op

revision = "0006_ai_tasks"
down_revision = "0005_text_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE workspace.ai_tasks (
        id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES workspace.users(id),
        session_hash char(64) NOT NULL, auth_revision bigint NOT NULL,
        project_id uuid NOT NULL REFERENCES workspace.projects(id), acl_revision bigint NOT NULL,
        question varchar(1000) NOT NULL,
        status text NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','RUNNING','COMPLETED','FAILED')),
        lease_hash char(64), answer text, error_code text,
        citation_ids uuid[] NOT NULL DEFAULT '{}',
        expires_at timestamptz NOT NULL DEFAULT now()+interval '5 minutes',
        created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE workspace.ai_sources (
        task_id uuid NOT NULL REFERENCES workspace.ai_tasks(id),
        chunk_id uuid NOT NULL REFERENCES workspace.text_chunks(id),
        publication_revision bigint NOT NULL,
        PRIMARY KEY(task_id,chunk_id)
    );
    CREATE INDEX ai_tasks_pending_idx ON workspace.ai_tasks(status,created_at);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE workspace.ai_sources; DROP TABLE workspace.ai_tasks;")
