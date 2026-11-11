"""Keep retrieval rank: the model should read the best passage first."""

from alembic import op

revision = "0011_source_rank"
down_revision = "0010_chat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE workspace.ai_sources ADD COLUMN rank integer NOT NULL DEFAULT 0;")


def downgrade() -> None:
    op.execute("ALTER TABLE workspace.ai_sources DROP COLUMN rank;")
