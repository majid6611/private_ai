"""Bootstrap application metadata; domain schema follows the permission contract."""

import sqlalchemy as sa
from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "installation",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("id = 1", name="single_installation"),
        schema="workspace",
    )
    op.execute("INSERT INTO workspace.installation (id) VALUES (1)")


def downgrade() -> None:
    op.drop_table("installation", schema="workspace")
