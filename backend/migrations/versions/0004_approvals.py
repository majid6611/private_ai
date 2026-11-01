"""Human approval bound to immutable versions, ACL revision and active generation."""

from alembic import op

revision = "0004_approvals"
down_revision = "0003_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE workspace.users ADD COLUMN auth_revision bigint NOT NULL DEFAULT 1;
    ALTER TABLE workspace.documents ADD COLUMN publication_revision bigint NOT NULL DEFAULT 0;
    CREATE TABLE workspace.approvals (
        id uuid PRIMARY KEY, document_id uuid NOT NULL REFERENCES workspace.documents(id),
        candidate_version_id uuid NOT NULL, candidate_sha256 char(64) NOT NULL,
        expected_active_version_id uuid, expected_publication_revision bigint NOT NULL,
        acl_revision bigint NOT NULL, requester_id uuid NOT NULL REFERENCES workspace.users(id),
        approver_id uuid REFERENCES workspace.users(id),
        requester_auth_revision bigint NOT NULL, approver_auth_revision bigint,
        status text NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','APPROVED','REJECTED','CONSUMED')),
        expires_at timestamptz NOT NULL DEFAULT now()+interval '1 day',
        approved_at timestamptz, consumed_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY(document_id,candidate_version_id) REFERENCES workspace.document_versions(document_id,id),
        FOREIGN KEY(document_id,expected_active_version_id) REFERENCES workspace.document_versions(document_id,id),
        CHECK(approver_id IS NULL OR approver_id <> requester_id)
    );
    CREATE INDEX approvals_document_idx ON workspace.approvals(document_id);
    CREATE TABLE workspace.publication_events (
        id uuid PRIMARY KEY, document_id uuid NOT NULL REFERENCES workspace.documents(id),
        version_id uuid NOT NULL REFERENCES workspace.document_versions(id),
        approval_id uuid NOT NULL UNIQUE REFERENCES workspace.approvals(id),
        actor_id uuid NOT NULL REFERENCES workspace.users(id),
        publication_revision bigint NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(document_id,publication_revision)
    );
    REVOKE UPDATE, DELETE ON workspace.publication_events FROM workspace_app;
    GRANT UPDATE(state) ON workspace.document_versions TO workspace_app;
    """)


def downgrade() -> None:
    op.execute("""
    REVOKE UPDATE(state) ON workspace.document_versions FROM workspace_app;
    DROP TABLE workspace.publication_events;
    DROP TABLE workspace.approvals;
    ALTER TABLE workspace.documents DROP COLUMN publication_revision;
    ALTER TABLE workspace.users DROP COLUMN auth_revision;
    """)
