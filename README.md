# Private AI Workspace

Local document workspace: authentication, project/document permissions, immutable
versions, independent human approval, atomic publication/rollback, isolated text
ingestion and permission-filtered lexical search. Includes an English React UI.

**AI answering is scaffolded but inactive:** the delegated task API and orchestrator
exist, but no model service or passed evaluation record is configured, so questions
cannot yet be answered by a model. Embeddings, PDF parsing, DOCX/OCR and production
readiness remain pending. Text search is not represented as an AI answer. PDFs stay
quarantined. No cloud services are used by the running application.

## Run

Linux with Docker Engine and Compose; no host Python, Node or PostgreSQL needed.

```sh
make init
make up
```

Or use the equivalent commands in [Makefile](Makefile). Open
**http://127.0.0.1:8080**. Only this loopback Nginx ingress is published; API,
PostgreSQL and workers have no host ports. This development setup uses HTTP on
loopback; add reviewed TLS ingress before any remote deployment.

Create the first administrator interactively, with a hidden password:

```sh
docker compose run --rm backend python -m app.cli
```

No default account is installed. Bootstrap refuses once an administrator exists.
Usernames: lowercase ASCII, 3–64 characters. Passwords: 16–256 characters.
The browser keeps the one-hour bearer session in memory; refreshing requires login.
There is no public registration or token storage in localStorage.

Inside the UI, administrators can create users/projects and explicitly assign
membership. Project creation offers an explicit checkbox to join as a document
manager. An administrator without membership cannot read project documents.
A different authorized reviewer must approve the author's/requester's change.
Document-specific grants use the coworker's UUID, available from the admin user API.

## Checks

```sh
make check
```

Tests use a separate tmpfs PostgreSQL database and refuse the application database.
To remove disposable test records/services:

```sh
docker compose --profile test rm -sf test-migrate test-db
```

Frontend TypeScript checks run during its Docker build. Browser QA is not yet
verified: no browser provider was available in the development tool environment.

## Document API

Send the human bearer token in `Authorization: Bearer <token>`. Login accepts JSON
at `POST /api/v1/auth/login`; `/auth/me` and `/auth/logout` inspect/revoke the session.
Admins can POST/GET `/users`, PATCH `/users/{id}` with `enabled`, POST `/projects`,
and PUT/DELETE `/projects/{id}/members/{user_id}`. Membership flags are independent
`can_edit`, `can_approve`, `can_publish`, `can_history`. GET `/projects` is member-only.

Uploads have raw bodies, `Content-Type: text/plain` or `application/pdf`, exact
`Content-Length` and an `Idempotency-Key` of 8–128 ASCII letters/digits/underscore/
dot/hyphen. Limit: 2 MiB/version and 100 MiB/project by default. Same-key retries
return the original version; changed payloads conflict. Quota is reserved before
intake and rolled back on failure. Text must be UTF-8. PDF signature checking is
only triage: PDFs cannot be downloaded, indexed, reviewed or published yet.

| Operation | Endpoint |
|---|---|
| Create first draft | `POST /api/v1/documents?project_id=<uuid>&title=<title>&restricted=false` |
| Append immutable draft | `POST /api/v1/documents/{id}/versions?parent_version_id=<uuid>` |
| List authorized documents | `GET /api/v1/documents?project_id=<uuid>&limit=50&offset=0` |
| Metadata/current publication generation | `GET /api/v1/documents/{id}` |
| Permitted history | `GET /api/v1/documents/{id}/versions` |
| Authorized bytes | `GET /api/v1/documents/{id}/content?version_id=<uuid>` |
| Restrict document | `PATCH /api/v1/documents/{id}/access` with `restricted` |
| Grant/revoke document access | `PUT/DELETE /api/v1/documents/{id}/access/{user_id}` |

Document owners with edit rights manage grants; grants never bypass project
membership/capabilities. Presence grants read, plus optional edit/history flags.
Draft reads require edit rights; older versions require history. Dedicated review
access lets approvers inspect the exact candidate without general draft edit rights.
Downloads verify the stored hash and use attachment/no-store/nosniff headers.

## Approval and publication

1. POST `/api/v1/documents/{id}/approvals` with `candidate_version_id`,
   `expected_active_version_id` (null for first publication), and
   `expected_publication_revision` from metadata.
2. A separate authorized reviewer GETs `/api/v1/approvals/{id}/review`, then POSTs
   `/api/v1/approvals/{id}/approve` or `/reject`.
3. A user with publication permission POSTs `/api/v1/documents/{id}/publish` with
   `approval_id`. GET `/documents/{id}/approvals` lists permitted workflow records.

Approval expires after one day, binds the exact version/hash, ACL revision and
publication generation, and is consumed once. Permission changes or account
revocation invalidate old approvals even if access is restored. Publication pointer,
version state, approval consumption and append-only publication/audit events commit
together before a success response is sent. Replays and stale publication attempts
conflict. Rollback uses the same review/publish flow targeting an older published
version; generation prevents stale approvals surviving a version-pointer round trip.

## Text ingestion and search

The indexer leases only active published text through a narrow internal service API.
It has no DB credentials or document mount. The parser has no secrets and shares
only a private network with the indexer. It returns source offsets, not invented
content. Backend validates complete bounded source coverage and rebuilds chunk text
from immutable original bytes. Leases expire and retry at most three times.

`GET /api/v1/search?project_id=<uuid>&q=<query>` returns authorized active-version
snippets with immutable version IDs and character offsets. It is PostgreSQL lexical
search with the `simple` configuration, not semantic retrieval or model inference.
There are no shared search/answer caches. PDF/OCR and vector/hybrid ranking await
their own quality and isolation tests.

Internal ingestion routes are denied at Nginx and require a separate service key at
the backend. A human token cannot substitute for that key, and the key does not
substitute for human approval. Never publish parser/indexer/backend/DB ports.

## AI question tasks (scaffold, no model installed)

`POST /api/v1/ai/tasks` (`project_id`, `question`) retrieves up to 8 authorized
published chunks by lexical search (any content word of the question may match the
passage or its document title; stopwords are dropped) and records an expiring, user-bound task with
the chunk provenance; `GET /api/v1/ai/tasks/{id}` returns status, answer and only
the cited sources, revalidating session, project ACL revision and publication
revision on every read. The orchestrator leases tasks through an internal,
service-keyed route and calls an OpenAI-compatible model only when `MODEL_ID` and a
`MODEL_EVALUATION_FILE` with `passed: true` for that model are configured. Neither
exists yet, so tasks with matching evidence end as `MODEL_UNAVAILABLE`; tasks with
no matching evidence complete with "Insufficient evidence". No UI uses this yet.

## Backup and restore verification

```sh
sh scripts/backup.sh
sh scripts/verify-backup.sh /absolute/path/printed/by/backup
```

Backup briefly stops this project's existing backend/web/indexer containers, captures
DB plus document files and checksums under a protected `.backups/snapshot-*`
directory, and restarts those same containers. Failure leaves an incomplete snapshot;
only snapshots with the expected FORMAT and valid checksums may be restored.
Verification restores into a separate tmpfs PostgreSQL project, validates hashes,
version/approval/chunk provenance and API database role privilege, then removes only
that disposable project. The application DB and files are never overwritten.
The initial verification scratch is bounded to 256 MiB; increase deliberately for
larger backups. A populated fixture drill is available with `make test-recovery`; customer-scale
and off-host recovery remain pilot gates.

Local snapshots are not an off-host or encrypted backup strategy. Copy them to a
separate protected failure domain; manage secrets separately. Do not put backups or
`.secrets` into version control. Normal `docker compose down` preserves application
volumes; **`docker compose down -v` deletes them**.

Inspect orphaned blobs (default read-only):

```sh
docker compose run --rm --no-deps backend python -m app.maintenance
```

An operator can add `--apply` to remove only unreferenced UUID blobs older than one
hour. A DB advisory lock excludes in-flight uploads. Referenced versions are never
collected; orphan reconciliation does not replace backup.

## Scope and operations

Secret files are generated once under a 0700 directory; individual read-only mounts
are readable by the non-root containers. Changing a password file does not rotate
an existing DB role; use an explicit credential rotation procedure. Release images
and dependencies are pinned. Current backend images include checking tools; a
separate production target, SBOM/license review, image/OS audit, full egress policy,
TLS, global abuse limits and offline release bundle remain release work.

See [architecture](private-ai-platform-architecture.md),
[permission contract](docs/permissions.md), [implementation status](docs/implementation-status.md)
and [local hardware observations](docs/local-hardware.md).

Implementation references: [FastAPI dependency lifecycle](https://fastapi.tiangolo.com/advanced/advanced-dependencies/)
and [SQLAlchemy transactions](https://docs.sqlalchemy.org/en/20/core/connections.html).
