"""Two-stage tasks: the chat model may choose among a short list of candidate documents.

A task first waits in ROUTING (stage 'route'); the orchestrator picks from the allow-listed
candidates stored on the task, the backend retrieves inside the picked documents, and the
task continues as an ordinary answer task. Document cards also gain a model-written
description (leased to the orchestrator when it is otherwise idle).
"""

from alembic import op

revision = "0014_llm_routing"
down_revision = "0013_semantic_routing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE workspace.ai_tasks DROP CONSTRAINT ai_tasks_status_check;
    ALTER TABLE workspace.ai_tasks ADD CONSTRAINT ai_tasks_status_check
        CHECK (status IN ('ROUTING','PENDING','RUNNING','COMPLETED','FAILED'));
    ALTER TABLE workspace.ai_tasks
        ADD COLUMN stage text NOT NULL DEFAULT 'answer' CHECK (stage IN ('route','answer')),
        ADD COLUMN route_candidates jsonb,
        ADD COLUMN route_effective text;
    ALTER TABLE workspace.document_cards
        ADD COLUMN description text,
        ADD COLUMN lease_hash char(64),
        ADD COLUMN lease_expires_at timestamptz,
        ADD COLUMN attempts integer NOT NULL DEFAULT 0;
    """)


def downgrade() -> None:
    op.execute("""
    ALTER TABLE workspace.document_cards DROP COLUMN description, DROP COLUMN lease_hash,
        DROP COLUMN lease_expires_at, DROP COLUMN attempts;
    ALTER TABLE workspace.ai_tasks DROP COLUMN stage, DROP COLUMN route_candidates,
        DROP COLUMN route_effective;
    ALTER TABLE workspace.ai_tasks DROP CONSTRAINT ai_tasks_status_check;
    ALTER TABLE workspace.ai_tasks ADD CONSTRAINT ai_tasks_status_check
        CHECK (status IN ('PENDING','RUNNING','COMPLETED','FAILED'));
    """)
