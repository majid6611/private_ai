"""Users, opaque sessions, project membership and append-only security events."""

from alembic import op

revision = "0002_identity"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE workspace.users (
        id uuid PRIMARY KEY, username varchar(64) NOT NULL UNIQUE,
        password_hash text NOT NULL, is_admin boolean NOT NULL DEFAULT false,
        enabled boolean NOT NULL DEFAULT true,
        created_at timestamptz NOT NULL DEFAULT now(),
        CHECK (username = lower(username)),
        CHECK (username ~ '^[a-z0-9][a-z0-9_.-]{2,63}$')
    );
    CREATE TABLE workspace.sessions (
        token_hash char(64) PRIMARY KEY,
        user_id uuid NOT NULL REFERENCES workspace.users(id),
        expires_at timestamptz NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX sessions_user_idx ON workspace.sessions(user_id);
    CREATE TABLE workspace.login_limits (
        key char(64) PRIMARY KEY, failures integer NOT NULL DEFAULT 0,
        window_start timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE workspace.projects (
        id uuid PRIMARY KEY, name varchar(200) NOT NULL,
        acl_revision bigint NOT NULL DEFAULT 1 CHECK (acl_revision > 0),
        created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE workspace.memberships (
        project_id uuid NOT NULL REFERENCES workspace.projects(id),
        user_id uuid NOT NULL REFERENCES workspace.users(id),
        can_edit boolean NOT NULL DEFAULT false,
        can_approve boolean NOT NULL DEFAULT false,
        can_publish boolean NOT NULL DEFAULT false,
        can_history boolean NOT NULL DEFAULT false,
        PRIMARY KEY(project_id, user_id)
    );
    CREATE TABLE workspace.security_events (
        id uuid PRIMARY KEY, actor_id uuid REFERENCES workspace.users(id),
        action varchar(64) NOT NULL, target_id uuid,
        created_at timestamptz NOT NULL DEFAULT now()
    );
    REVOKE UPDATE, DELETE ON workspace.security_events FROM workspace_app;
    REVOKE INSERT, UPDATE, DELETE ON workspace.alembic_version FROM workspace_app;
    REVOKE INSERT, UPDATE, DELETE ON workspace.installation FROM workspace_app;
    """)


def downgrade() -> None:
    for table in (
        "security_events",
        "memberships",
        "projects",
        "login_limits",
        "sessions",
        "users",
    ):
        op.execute(f"DROP TABLE workspace.{table}")
