# Implementation status — document workspace, publication and text search

Implemented:
- Containerized FastAPI/PostgreSQL with separate migration/API roles, immutable
  file storage and explicit project/document access. Non-root/read-only application
  containers; only loopback Nginx ingress is published.
- Local users, memory-only browser bearer sessions, salted scrypt password hashes,
  one-hour expiry, logout/account revocation and failed-login cooldown.
- Durable bounded uploads, project quota reservation, immutable versions, checksummed
  downloads, fingerprint-bound retries, stale-parent conflict checks and orphan
  reconciliation excluding in-flight writes.
- Human approval bound to version/hash, ACL revision, account authorization revision
  and publication generation. Independent approver; expiry/rejection/replay checks;
  atomic publication and rollback; append-only audit/publication events.
- English React UI: login, project/user/membership setup, uploads, versions, editing,
  review of active/proposed text, approval/rejection, publication, document grants
  and lexical search with source version/character-offset references.
- Isolated credential-free text parser. Indexer has only the internal job identity,
  no DB credentials/storage mount. Backend reconstructs text from validated source
  offsets; expiring bounded leases and retry limits; no parser-provided invented text.
- PostgreSQL lexical search restricted to current published versions and current
  project/document access. No shared answer caches or model-side authorization.
- Coordinated local DB/file snapshots and isolated tmpfs restore verification.
- AI task scaffold (migration 0006, `app/ai.py`, `orchestrator/`): user/session/ACL-
  and publication-revision-bound tasks, source provenance, service-keyed leasing,
  citation validation and revalidation at lease, completion and read. The
  orchestrator refuses to call a model without a passed evaluation record. No model
  service is deployed; the UI Ask panel (project page) submits, polls and shows
  answers, citations and failure states; retrieval is the same lexical
  search with an any-word (OR) query over passage text and document title, ranked by
  PostgreSQL cover-density (migration 0007 adds a searchable title vector).

Verification:
- Full suite: 32 PostgreSQL tests passed (2026-10-31), with ruff and mypy clean.
  Includes a regression asserting that commit failure returns 500 rather than a
  success response. Two dependency deprecation warnings in the test client remain.
- Permission failures, input redaction, token hashing/expiry/logout, revocation,
  upload failures/retries, concurrent drafts/publications, immutable rows, one-use
  approval, expiry, rejection, independent review, rollback/ABA protection, failed
  publication rollback, source-bound chunks, stale leases and search revocation.
- Docker builds, strict backend/worker typing, lint and frontend TypeScript/build checks.
- Live ingress returns healthy API; parser HTTP smoke succeeds; parser has neither
  service/DB secret mounts nor backend DNS discovery on its isolated network.
- Actual document volume survived container recreation.
- Initial empty application snapshot restored successfully. A separate populated
  fixture recovery drill also passed: 2 documents, 3 immutable versions, 4 publication
  events, 1 source-bound chunk and 4 fixture accounts. Blob checksums, approval hashes,
  active pointer/journal consistency and restored immutable/audit privileges passed.
  Verification never wrote to the application DB or application document volume.
  Customer-scale disaster recovery and off-host failure testing remain pilot gates.
- npm scan reported zero known vulnerabilities after the build-tool update. The
  refreshed Python lock audit scanned 33 locked packages and reported zero known
  vulnerabilities. This does not cover OS/base-image vulnerabilities or unknown bugs.
- Transaction dependencies use function scope to commit before response delivery.
- Frontend/backend updates use Docker DNS resolution at ingress; the web health check
  covers backend readiness, not merely serving the static login page.

Hardening added 2026-11-01: password change and admin reset with tests; project-member
picker for document grants; orchestrator injection/boundary tests (`orchestrator/
test_generate.py`: no model call without a passed evaluation record, foreign or
excessive citations rejected, tools/extra fields dropped, redirects not followed,
response size bounded; unusable model output reports `INVALID_OUTPUT`); and
`scripts/check-boundaries.py` for Compose trust boundaries (verified to catch eight
deliberately broken configs). A headless-Chromium run (Playwright, desktop and mobile
widths) exercised login, project, search, Ask states, admin panel, document access
picker and password change; it caught and fixed a polling bug in the Ask panel. This
is a scripted smoke pass, not full accessibility or cross-browser certification.

Retrieval evaluation (`backend/tests/test_retrieval_eval.py`, synthetic corpus in
`backend/tests/eval/`): 7 real-answer documents plus 100 generated near-duplicate
distractors (119 chunks), 42 answerable questions (22 direct, 8 paraphrase, 12 hard
paraphrase), and 10 no-answer questions. Measured 2026-10-31: recall@5 95% (40/42),
recall@3 90%, recall@1 71%. The old all-terms query scored 0/30 on the easy set. The test
fails below 90% recall@5. This is a small synthetic smoke gate, not proof of general
retrieval quality; no-answer questions still retrieve chunks (declining is the model
layer's job and is untested). English stemming and vectors have not been evaluated.

Limits / remaining work:
- Browser QA is a scripted Chromium smoke pass only; accessibility audit, Firefox/
  Safari and a full visual review have not been done.
- Model inference, embeddings/pgvector, hybrid RAG, meeting transcription and model
  benchmarks are not implemented (only the AI task scaffold above). Text search is explicitly labeled, not simulated AI.
- PDF stays quarantined. PDF parser/malware workflow, DOCX and OCR remain pending.
- Current host has about 7.1 GiB RAM, nearly full swap and no registered NVIDIA
  runtime; no weaker model was silently installed. See local-hardware.md.
- Coordinated backups currently stay on the host and are not encrypted. Off-host
  protected copies, secrets recovery and a customer-scale restore drill remain pilot gates.
- TLS/production browser identity, password changes/recovery, cleanup/retention,
  deployment-wide abuse limits, full egress enforcement, image/OS auditing,
  production image split, SBOM/license review and offline installation bundles remain.
- API-role append-only logs do not protect against a database administrator.
- The UI currently uses a coworker's UUID for document grants; account recovery and
  richer team administration are deliberately not represented as completed features.

The architecture is the target contract, not a claim that all controls have shipped.
