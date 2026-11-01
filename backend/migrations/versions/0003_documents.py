"""Immutable draft versions, document restrictions, idempotency and intake outbox."""

from alembic import op

revision = "0003_documents"
down_revision = "0002_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE workspace.projects ADD COLUMN storage_bytes bigint NOT NULL DEFAULT 0
        CHECK(storage_bytes >= 0), ADD COLUMN quota_bytes bigint NOT NULL DEFAULT 104857600
        CHECK(quota_bytes > 0);
    CREATE TABLE workspace.documents (
        id uuid PRIMARY KEY, project_id uuid NOT NULL REFERENCES workspace.projects(id),
        title varchar(200) NOT NULL, owner_id uuid NOT NULL REFERENCES workspace.users(id),
        restricted boolean NOT NULL DEFAULT false, latest_version_id uuid,
        active_version_id uuid, created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX documents_project_idx ON workspace.documents(project_id);
    CREATE TABLE workspace.document_grants (
        document_id uuid NOT NULL REFERENCES workspace.documents(id),
        user_id uuid NOT NULL REFERENCES workspace.users(id),
        can_edit boolean NOT NULL DEFAULT false, can_history boolean NOT NULL DEFAULT false,
        PRIMARY KEY(document_id,user_id)
    );
    CREATE TABLE workspace.document_versions (
        id uuid PRIMARY KEY, document_id uuid NOT NULL REFERENCES workspace.documents(id),
        parent_version_id uuid, blob_key uuid NOT NULL UNIQUE,
        sha256 char(64) NOT NULL, size integer NOT NULL CHECK(size > 0 AND size <= 2097152),
        mime_type text NOT NULL CHECK(mime_type IN ('text/plain','application/pdf')),
        author_id uuid NOT NULL REFERENCES workspace.users(id),
        state text NOT NULL DEFAULT 'DRAFT' CHECK(state IN ('DRAFT','PENDING_APPROVAL','PUBLISHED','REJECTED')),
        created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(document_id,id),
        FOREIGN KEY(document_id,parent_version_id) REFERENCES workspace.document_versions(document_id,id)
    );
    ALTER TABLE workspace.documents ADD FOREIGN KEY(id,latest_version_id)
        REFERENCES workspace.document_versions(document_id,id);
    ALTER TABLE workspace.documents ADD FOREIGN KEY(id,active_version_id)
        REFERENCES workspace.document_versions(document_id,id);
    REVOKE UPDATE,DELETE ON workspace.document_versions FROM workspace_app;
    CREATE TABLE workspace.document_requests (
        actor_id uuid NOT NULL REFERENCES workspace.users(id), operation text NOT NULL,
        request_key varchar(128) NOT NULL, fingerprint char(64) NOT NULL,
        document_id uuid NOT NULL REFERENCES workspace.documents(id),
        version_id uuid NOT NULL REFERENCES workspace.document_versions(id),
        PRIMARY KEY(actor_id,operation,request_key)
    );
    CREATE TABLE workspace.intake_jobs (
        id uuid PRIMARY KEY, version_id uuid NOT NULL UNIQUE REFERENCES workspace.document_versions(id),
        status text NOT NULL DEFAULT 'PENDING', created_at timestamptz NOT NULL DEFAULT now()
    );
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE workspace.intake_jobs;
    DROP TABLE workspace.document_requests;
    ALTER TABLE workspace.documents DROP CONSTRAINT documents_id_latest_version_id_fkey;
    ALTER TABLE workspace.documents DROP CONSTRAINT documents_id_active_version_id_fkey;
    DROP TABLE workspace.document_versions;
    DROP TABLE workspace.document_grants;
    DROP TABLE workspace.documents;
    ALTER TABLE workspace.projects DROP COLUMN storage_bytes, DROP COLUMN quota_bytes;
    """)
