"""Record which model produced each answer and whether it had passed its evaluation."""

from alembic import op

revision = "0009_ai_model_label"
down_revision = "0008_pdf_text"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE workspace.ai_tasks ADD COLUMN model_id text, ADD COLUMN model_evaluated boolean;")


def downgrade() -> None:
    op.execute("ALTER TABLE workspace.ai_tasks DROP COLUMN model_id, DROP COLUMN model_evaluated;")
