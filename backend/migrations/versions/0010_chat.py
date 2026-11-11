"""Private chat threads: each turn is a question plus the AI task that answers it."""

from alembic import op

revision = "0010_chat"
down_revision = "0009_ai_model_label"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE workspace.chat_threads (
        id uuid PRIMARY KEY,
        user_id uuid NOT NULL REFERENCES workspace.users(id),
        project_id uuid NOT NULL REFERENCES workspace.projects(id),
        title varchar(100) NOT NULL DEFAULT 'New chat',
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX chat_threads_owner_idx ON workspace.chat_threads(user_id, project_id, updated_at DESC);
    ALTER TABLE workspace.ai_tasks ADD COLUMN thread_id uuid REFERENCES workspace.chat_threads(id);
    CREATE INDEX ai_tasks_thread_idx ON workspace.ai_tasks(thread_id);
    CREATE TABLE workspace.chat_turns (
        id uuid PRIMARY KEY,
        thread_id uuid NOT NULL REFERENCES workspace.chat_threads(id),
        seq integer NOT NULL CHECK (seq >= 1),
        question varchar(1000) NOT NULL,
        task_id uuid NOT NULL UNIQUE REFERENCES workspace.ai_tasks(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (thread_id, seq)
    );
    """)


def downgrade() -> None:
    op.execute("""DROP TABLE workspace.chat_turns;
        ALTER TABLE workspace.ai_tasks DROP COLUMN thread_id;
        DROP TABLE workspace.chat_threads;""")
