# Private AI Business Workspace — Architecture & Security Blueprint

**Status:** Draft v0.3 (Python implementation baseline and resource/quality policy)  
**Date:** 2026-10-25  
**Target:** Self-hosted, offline-capable platform for small and medium-sized businesses  
**Approach:** API-first, security-first, modular, open-source components where commercially permissible

## 1. Product Vision

Build a private AI workspace that organizations can deploy on their own hardware. The **Document Core** is the system of record for documents and versions. AI-assisted document work, meeting analysis, writing, and task proposals operate as clients of narrowly scoped backend APIs—not as privileged processes with direct access to storage.

Initial delivery is an installable product and demonstrable MVP. Commercial positioning can later support a community edition, paid deployments, support, and enterprise features; the distribution model and all third-party licenses require review before publication.

## 2. Non-Negotiable Principles

1. **Treat all model outputs and ingested content as untrusted.** A language model cannot grant itself permission by producing a convincing instruction.
2. **No direct AI access to sensitive infrastructure.** Model-serving processes have no mounts containing business documents, database credentials, application `.env` files, secrets, host filesystem paths, or the Docker socket.
3. **Backends enforce authorization.** Every read, create, version, approval, and publish operation is checked independently of model decisions.
4. **Least privilege by default.** Services receive narrowly scoped identities, network routes, API permissions, and filesystem access.
5. **Preserve original data.** No AI-initiated deletion; draft-first edits; explicit, authorized human approval for publishing or replacing active versions.
6. **Versioning over destructive overwrite.** Keep recoverable prior versions and an audit trail; define retention and backup policies.
7. **Offline-capable operation.** Normal processing, inference, document indexing, authentication, and licensing should not require cloud access. Provide an offline installation and signed update path.
8. **No implicit trust between containers.** Docker Compose is a deployment mechanism, not a complete security boundary.
9. **Resource efficiency must preserve measured AI quality.** Reduce infrastructure overhead and concurrency before accepting a model or configuration that fails the quality gates in §20.

## 3. Logical Architecture

```text
                       Users / Administrators
                                |
                         Web UI / Client
                                |
                    API Gateway / Auth Layer
                                |
                +---------------+---------------+
                |                               |
         Document Service                 AI Orchestrator
        (source of truth)                      |
                |                     Restricted tools / APIs
         +------+--------+                      |
         |               |                Model Runtime
    Metadata DB      File/Object Store    (no file/DB mounts,
         |            + Version Store      no secrets, no shell)
         |
    Approval & Audit Service
                ^
                |
        Meeting Service  ----> Document API (draft creation)
        Task Service     <---- approved action items
```

The diagram is conceptual. The MVP may use a modular backend deployment rather than many independently deployed microservices. **Enforce the same ownership and authorization boundaries regardless of process layout.**

## 4. Component Responsibilities

### Document Service
- Owns document metadata, controlled access to content, folders/projects, versions, and publication state.
- Provides authenticated APIs for file creation, authorized retrieval, draft versions, version listing, and approval-based publication.
- Holds write permission to its managed storage; other services do not mount that storage for writing.
- For imported NAS/SMB content, uses a restricted connector. Read access follows source permissions; publishing back to the source requires a separately privileged, approved write flow.

### Identity & Authorization
- Validates user and service identities, tenant/project membership, and per-document permissions.
- Enforces authorization at **every** endpoint and at retrieval time, including search and RAG results.
- Uses short-lived credentials and service-specific scopes; a service token never substitutes for human approval.

### AI Orchestrator
- Presents a small allowlist of tools to the model, each mapped to a specific backend operation.
- Validates tool arguments, limits output size, rate-limits requests, and requires a verified user context.
- Treats retrieved text, transcripts, and generated tool parameters as untrusted input. No arbitrary filesystem or SQL tools.
- Does not possess broad storage or database credentials.

### Model Runtime
- Runs local models for generation, embeddings, and later transcription.
- Has no mounts for confidential files, `.env` files, database storage, credentials, or Docker socket.
- Receives only task-specific content approved for the current user and purpose. If it receives authorized confidential content in a prompt, that content is visible to the model process; prevent unauthorized content from being sent in the first place.
- Restrict outbound network access, disable unnecessary capabilities, use non-root execution, and prefer read-only root filesystems where compatible.

### Meeting Service (later phase)
- Imports a user-provided recording with appropriate participant notice/consent.
- Generates a transcript, summary, decisions, and proposed action items locally.
- Creates a **draft** minutes document through Document API. Never directly edits original files.
- Sends suggested tasks to the Task Service for review; only an authorized user can approve changes.

## 5. API Contract (Illustrative)

All endpoints require authenticated identity and authorization. Paths are illustrative, not a frozen API specification.

| Method | Endpoint | Purpose | Required checks |
|---|---|---|---|
| `POST` | `/api/v1/documents` | Create a new draft document | Create permission in target project |
| `GET` | `/api/v1/documents/{id}` | Read permitted metadata | Document-level read permission |
| `GET` | `/api/v1/documents/{id}/content` | Download an authorized version | Read permission, version policy |
| `POST` | `/api/v1/documents/{id}/versions` | Propose new draft version | Edit permission; immutable original |
| `GET` | `/api/v1/documents/{id}/versions` | List permitted version history | Read/history permission |
| `POST` | `/api/v1/documents/{id}/approvals` | Request publication approval | Edit and workflow permissions |
| `POST` | `/api/v1/approvals/{id}/approve` | Approve a specific change | Verified human identity and approval role |
| `POST` | `/api/v1/documents/{id}/publish` | Publish approved version | Server-verified approval + optimistic concurrency |

**Important:** The API must never accept `confirmed: true` from an AI tool as proof of human approval. Approvals must be bound server-side to an authenticated user, a specific document/version/hash, and an expiry. Use optimistic locking (e.g., version ID or ETag) to avoid overwriting concurrent changes.

## 6. File Modification & Versioning Rules

- **Read:** Allowed only when policy grants access to that exact document and version.
- **Create:** Allowed only in designated workspaces with appropriate create scope.
- **Edit:** Generate a new draft version; optionally show a diff/preview.
- **Publish / replace active version:** Require explicit authorized human approval. Preserve the prior version for rollback.
- **Delete:** Not exposed to AI in the MVP. Administrative lifecycle/retention actions require a distinct policy.
- **External NAS overwrite:** Separate connector, constrained folder-level privileges, audit record, approval, and safe recovery plan. Keep source shares read-only until this flow is implemented and tested.
- **Exports:** Generate DOCX/PDF in controlled output storage. Do not overwrite originals by default.

## 7. Security Controls & Threat Model

### Primary threats
- Prompt injection within a user message, imported document, or meeting transcript.
- Unauthorized cross-user or cross-tenant retrieval through search/RAG.
- Secret leakage from environment variables, logs, mounted files, or overly broad tools.
- Malicious file uploads and document-parser exploits.
- Privilege escalation through container runtime or overly permissive service networks.
- Accidental overwrites, replayed approvals, and race conditions.

### Required controls
- Network segmentation and deny-by-default service communication; the model runtime cannot reach the metadata DB, object store, NAS, or secret manager directly.
- Never mount host `.env` files into model or agent containers. Supply each backend only its necessary secrets using controlled mechanisms; avoid placing secrets in logs and command arguments.
- No shell, arbitrary file-read, unrestricted URL fetch, SQL execution, or Docker management tools exposed to the model.
- Backend authorization on document retrieval **before** text enters the model context; ensure vector-search results are filtered by effective user permissions.
- Safe document ingestion: file type/size limits, parser isolation, malware scanning where appropriate, and resource/time limits.
- Strict tenant and project isolation, audit logs (without leaking secrets), rate limits, input validation, and test coverage for authorization failures.
- Container hardening: non-root users, dropped Linux capabilities, `no-new-privileges`, minimal mounts, read-only filesystems when practical, and AppArmor/SELinux as appropriate.
- Encryption in transit and at rest where appropriate, verified backups, secure configuration defaults, and documented recovery procedures.
- For higher-assurance deployments, consider VM-level isolation instead of relying solely on containers.

**Security acceptance test:** If an adversarial PDF or user message asks the model to reveal `/app/.env`, database passwords, or another customer's document, the model must not have the capability or backend authorization to obtain that data. Test both direct and indirect access paths.

## 8. MVP Scope

### Included
1. Local authentication and project/document authorization.
2. Document upload and retrieval through authenticated APIs.
3. Document metadata, draft versions, explicit publication approval, rollback, and audit trail.
4. PDF/text ingestion and a minimal on-premises RAG proof of concept with source references.
5. A basic web UI for upload, search, draft preview, and approval.
6. Local model inference, fully offline runtime test, and Docker Compose installation.

### Deferred
- Live Zoom/Teams bots, automatic email sending, autonomous file changes, automatic task deletion, complex workflow engines, and broad enterprise connectors.
- Meeting file upload/transcription and generated minutes come **after** the Document Core contract is stable.
- Full NAS write-back and advanced AD/LDAP integration are later hardening milestones.

## 9. Suggested Two-Week Prototype Plan

This is a **target sequence**, not a delivery guarantee.

| Period | Deliverable | Acceptance criteria |
|---|---|---|
| Days 1–2 | Repository, container boundaries, threat model, DB schema | No shared writable document volumes; model has no sensitive mounts |
| Days 3–4 | Authentication + Document API | Create/read restricted by identity and project |
| Days 5–6 | Immutable versions + draft workflow | Original version preserved; concurrent updates handled safely |
| Days 7–8 | Human approval + audit + restore | Unapproved publish fails; every approval is bound to user/version |
| Days 9–10 | Local inference + basic document indexing | Authorized PDF searchable with references; unauthorized docs excluded |
| Days 11–12 | Simple UI + generated document export | Draft document and PDF/DOCX export land in controlled workspace |
| Days 13–14 | Offline demo + adversarial/security tests | No internet dependency; `.env`/cross-user leakage attempts denied |

Avoid overbuilding microservices during the prototype: a modular backend can enforce these contracts while remaining deployable with Docker Compose.

## 10. Open-Source & Commercial Readiness

- Prefer permissively licensed components (e.g., MIT/Apache-2.0) **after verifying exact repository versions and transitive dependency obligations**.
- Review model weights' licenses independently from runtime software licenses.
- Maintain a software bill of materials (SBOM), license notices, and attribution.
- Keep component boundaries clean so a community/open-core distribution remains an option; decide public/private licensing deliberately before first release.

## 11. First End-to-End Demo

1. An authorized employee creates a text-native sample project contract. Imported PDFs are tested separately as preserved originals with linked suggestions, per §15.
2. The Document Service stores immutable version 1 and assigns metadata and permissions. An initial publication follows the same human approval rules; the expected active version is initially null.
3. The employee asks a question; an authorized retrieval endpoint supplies only permitted extracts to a local model.
4. The model generates a proposed revised paragraph and a summary.
5. The Document Service saves that output as a **draft version 2**.
6. A human reviews the diff and approves publishing that specific version.
7. The active version changes to version 2; version 1 remains recoverable and the action is audited.
8. A prompt-injection test requesting `.env` contents or unrelated documents fails at the tool and backend authorization boundaries.

**Core design rule:** **AI is untrusted; backend services enforce permissions.**

## 12. Deployment and Trust Boundaries (v0.2 Addendum)

**MVP deployment: single organization per installation.** Do not claim multi-tenant isolation until it has been designed and tested. Use separate runtime identities and network policies, not merely separate modules in one process.

```text
[Browser] --TLS--> [API/Identity + Document/Approval Backend]
                          |                |
                    [Metadata DB]     [Managed Storage]
                          |
                    [RAG Index/Worker]
                          ^  authorized, bounded content only
                          |
                 [AI Orchestrator] --> [Model Runtime]
                 (separate process/container, limited API credentials)
```

- The Document, Approval, and Authorization modules **may share one backend process** and controlled database transactions. The AI Orchestrator **must not** share that process or its database credentials.
- Use separate Compose networks or host firewall rules: public ingress only to the gateway; backend-to-DB/storage restricted; orchestrator-to-backend limited to approved API routes; model runtime cannot route to DB, storage, NAS, or secrets.
- Do not give AI containers sensitive host mounts, Docker socket, application `.env`, broad infrastructure credentials, shell tools, or privileged mode. The Orchestrator receives only its scoped service identity and short-lived delegated user authorization (§21); the model receives neither. Mount model weights read-only only if required; model runtime can necessarily access the task-specific prompt passed to it.
- Run ingestion/parsers in a separate constrained worker with resource limits, restricted egress, temporary isolated storage, and no application secrets. Treat extracted text as hostile input.
- Implement network isolation and permissions in deployable configuration and test them; a diagram is not an enforcement mechanism.

## 13. Document Data Model and State Transitions

### Minimum entities

| Entity | Key fields |
|---|---|
| `User` | id, status, roles, authentication subject |
| `Project` | id, organization_id, name, policy |
| `Document` | id, project_id, owner_id, active_version_id, ACL/policy reference, created_at |
| `DocumentVersion` | id, document_id, parent_version_id, immutable_blob_key, sha256, mime_type, size, author_id, state, created_at |
| `ApprovalRequest` | id, document_id, candidate_version_id, candidate_sha256, expected_active_version_id, requester_id, approver_id, expires_at, status, consumed_at |
| `AuditEvent` | id, actor, action, target, outcome, timestamp, correlation_id |
| `RagChunk` | id, document_id, version_id, chunk_id, content_hash, index_generation, ACL revision reference |
| `ExportJob` | id, requesting_user, source_version_ids, output_blob_key, status |

Store binary content in managed storage, metadata in the database, and index entries with explicit document/version provenance. Use stable IDs and immutable version blobs.

### Version states

`DRAFT -> PENDING_APPROVAL -> PUBLISHED` or `DRAFT/PENDING_APPROVAL -> REJECTED`.

A published version remains immutable. Publishing changes the document's **active-version pointer**, not the bytes of any prior version. Superseded versions remain recoverable under retention policy. A rejected draft may be revised by creating another draft, never by silently changing a hash-bound approval target.

### Approval rules and atomic publish

1. Create an approval bound to exact document ID, candidate version ID/hash, expected active version ID, requesting identity, approver policy, and expiration.
2. Require authenticated interactive human approval; validate approver authorization **at approval time and again at publish time**. Revocation invalidates outstanding approvals.
3. On publish, execute a transaction with a document-row lock or compare-and-swap on active version; verify approval is unexpired, unconsumed, and matches candidate hash and current active version.
4. Atomically change the active pointer, mark approval consumed, and write a durable audit/outbox record. Duplicate/replayed requests must be idempotent or rejected.
5. If storage and database cannot share a transaction, persist immutable blobs before the DB transaction, verify checksums, and reconcile orphaned blobs asynchronously. Never mark publication successful before durable commit.
6. Concurrent publish attempts with stale expected versions must return `409 Conflict` and require re-review.

## 14. Secure Ingestion and RAG Lifecycle Contract

1. **Intake:** authorize upload/import; enforce quotas, file size and type checks; use content sniffing, malware scanning as applicable, and parser sandboxing. Never execute document macros or embedded instructions.
2. **Extract:** preserve source filename, version, page/section locations, extraction confidence and parsing errors. Flag image-only PDFs as needing OCR; OCR is optional for MVP.
3. **Chunk and embed:** bind every chunk and vector to `document_id`, immutable `version_id`, index generation and source locations. Indexing workers receive narrowly scoped access.
4. **Retrieve:** authenticate the user; apply document/version ACL filters **before** returning titles, snippets, counts, or content. Recheck authorization immediately before adding each chunk to model context; never rely on model-side filtering.
5. **Answer:** treat retrieved text as untrusted data, constrain citations to authorized source version/page references, and allow an explicit “insufficient evidence” response.
6. **Invalidate:** permission revocation, project moves, document retirement and active-version changes must invalidate or reauthorize search caches, previews, generated snippets, and conversation references. Prefer short-lived per-user caches keyed by ACL revision; never expose historical snippets after access revocation.
7. **Reindex/restore:** indexing must be repeatable and idempotent. Rebuild vectors from authorized immutable versions after restore, verify provenance, and avoid serving stale index generations.

**History policy:** chat history may contain quoted confidential text. Apply per-user/project access rules on history retrieval, retention and export; revoke or redact accessible cached content when underlying access is removed. Do not promise that data already viewed by a user can be retroactively erased.

## 15. Document Editing Format Contract

The MVP **does not promise pixel-perfect PDF editing**. Preserve uploaded PDF/DOCX originals. Generate a separately stored suggested change (text or structured patch) with source references and a reviewable diff. For text-native managed documents, produce a new immutable version and optional PDF/DOCX export. For imported PDFs, a new PDF is a generated derivative, not a faithful in-place edit unless explicitly validated. Complex layouts, scanned forms, tables and image preservation are deferred.

## 16. Reference Lab Profile and Quality Gates

**Initial reference profile (to benchmark, not a guarantee):** 8-core x86-64 CPU, 32–64 GB RAM, NVIDIA GPU with 12 GB VRAM, 1 TB SSD, Linux + Docker Compose. Offer CPU-only experimental mode with clearly slower inference. Benchmark a quantized 7–8B-class model first; actual memory and context limits depend on the selected model and quantization.

**Initial workload assumption:** one organization, 2–5 interactive test users, 1–2 concurrent inference requests, 1,000–5,000 mostly text PDFs/DOCX files, English first with Persian evaluated separately. Record document sizes, page counts and OCR share. These are **test targets**, not supported production capacity claims.

**Evaluation suite:** at least 30 representative questions with known authorized answers, 10 no-answer questions, cross-user permission tests, 10 adversarial prompt-injection cases, citation/page correctness checks, Persian/English quality samples, and measured p50/p95 response latency. Track retrieval recall, answer faithfulness, refusal when evidence is missing, and failure cases; do not define pass/fail solely by a model's self-rating.

**Recovery gate:** perform a full backup **and a restore onto a clean environment**; verify document hashes, active version pointers, approval/audit records, authorization, and reindexed search results. Document RPO/RTO targets before a customer pilot.

## 17. Threat Model and Release Gates

| Threat | Trust boundary | Required mitigation / test |
|---|---|---|
| Prompt injection asks AI to read `.env` | Untrusted content -> orchestrator | No such tool or mount; deny network paths; test tool-call attempts |
| Malicious PDF exploits parser | Upload -> ingestion worker | Sandbox, resource limits, no secrets, patched parsers; test malformed files |
| Unauthorized search title/snippet leak | RAG index -> user/model | ACL filtering before metadata/snippets and pre-context recheck |
| Revoked access persists in cache/history | ACL -> cache/history | ACL-revision invalidation and authorization on history retrieval |
| Approval replay or race | Approval API -> publication | Human-bound one-time approval, hash/ETag binding, atomic CAS/transaction |
| Forged service token | Service -> backend API | Scoped identity, short expiry, audience validation, rotation and audit |
| Container escape / lateral movement | Runtime -> host/services | Non-root, capability drop, read-only mounts, egress rules; VM isolation for higher assurance |
| Sensitive data in logs/backups | Processing -> observability/storage | Redaction, access limits, encryption, retention and restore tests |

**Before pilot:** document residual risks, complete dependency/license/SBOM review, validate offline install and offline updates, test a network-disconnected deployment, run the security tests above, and obtain explicit user consent/notice for future meeting recordings.

## 18. Delivery Milestones (Revised)

The original 14-day schedule in §9 is a **stretch prototype**, not a production-ready commitment. Use these milestones instead:

- **Milestone A — Architecture & security contracts:** deployment diagram, data schema, API permissions, threat model, ingestion/RAG lifecycle, test fixtures.
- **Milestone B — Thin vertical slice:** authenticated upload -> immutable version -> restricted retrieval -> AI draft -> human approval -> atomic publish -> audit, on one reference lab machine.
- **Milestone C — Document intelligence:** reproducible ingestion/indexing, source citations, authorization revocation tests, controlled exports and offline deployment.
- **Milestone D — Pilot hardening:** backup/restore drill, quality benchmarks, monitoring, update strategy, installation documentation and customer security review.
- **Later:** meeting transcription and task proposals integrated via Document API; NAS write-back only after separate connector threat modeling and approval workflow.

**Implementation rule:** never expose generic file-read, SQL, shell, or unrestricted network tools to the model. All effects go through authenticated, scoped backend APIs; user confirmation must be server-verified and version-bound.

## 19. Selected Implementation Baseline

| Layer | Decision | Boundary |
|---|---|---|
| Document, Approval, Identity and retrieval backend | Python + FastAPI; SQLAlchemy + Alembic for persistence and migrations | One modular process; owns authorization, DB transactions and managed file writes |
| AI Orchestrator | Python, separate process/container | Calls scoped backend APIs; no DB credentials or document volume |
| Ingestion | Python, isolated parser worker | One bounded job input; temporary storage; no main DB or storage credentials |
| Database, vector index and job queue | PostgreSQL + pgvector | No separate Redis, broker or vector database initially |
| Frontend | TypeScript + React | Static assets served through the application ingress |
| File storage | Managed local filesystem behind Document API | Server-generated opaque keys; immutable blobs outside database; no client-selected paths |
| Model runtime | Separate local inference service | Engine, model and quantization selected by benchmark, not fixed in advance |
| Deployment | Docker Compose on one Linux host | Single organization; enforce and test network and identity boundaries |

Pin exact dependency versions during implementation and record license review results. Python code must pass strict type checking, linting and runtime boundary validation. These checks do not prove authorization or transaction correctness; use PostgreSQL integration tests for those properties.

The backend owns job scheduling and index writes. A worker receives only a leased, bounded input and returns validated extraction output through an internal API; untrusted parser code never receives general database credentials. Jobs require leases, bounded retries, idempotent completion and recovery after worker failure. Keep parser execution isolated from any component holding job-control credentials.

## 20. Resource Budget and AI Quality Policy

- Start with one concurrent generation request and a bounded queue. Measure memory, latency and throughput on the actual target machine before raising limits. Bound upload size, extraction time, context length, output length and indexing concurrency.
- Use authorized lexical and vector retrieval together as the initial candidate design; benchmark against simpler retrieval. Add a reranker or another model only when measured quality gains justify its memory and latency costs.
- Cache reusable extraction and embeddings by immutable content/version and extraction/embedding configuration. Apply authorization independently on every read. Do not introduce shared answer caches in the initial implementation.
- Evaluate model, quantization, embedding model, retrieval settings and prompt template as a versioned configuration. Record model artifact hashes, hardware, context limits, language, latency and peak RAM/VRAM for reproducibility.
- Choose the least resource-intensive configuration that passes the quality gates. A 7–8B model is a benchmark candidate, not a mandatory production choice. If none passes on the available hardware, report the limitation and evaluate slower inference or better hardware; never silently substitute an inadequate model.
- Start with English and Persian evaluation sets reported separately. Do not advertise a language as supported until it passes its own gates. OCR-dependent documents are outside the initial text-document quality claim.

**Initial pilot thresholds (project targets, not observed results):**

| Measure | Required result on the fixed evaluation set |
|---|---|
| Authorization and service-boundary tests | Zero unauthorized metadata/content disclosures or unauthorized side effects in the suite; any failure blocks pilot |
| Retrieval recall@5 | At least 90% of labeled relevant evidence units retrieved across answerable questions |
| Citation validity | Every emitted citation resolves to an authorized immutable source; at least 95% of cited claims supported by the cited passage, reviewed by a human |
| Answer correctness | At least 90% of answerable questions judged correct and sufficiently complete using a written rubric |
| Missing evidence | At least 9 of the initial 10 no-answer cases explicitly decline unsupported conclusions |
| Critical factual errors | Zero fabricated contract amounts, dates or obligations in the pilot evaluation set |
| Performance | Record p50/p95 latency and peak RAM/VRAM; set a numerical latency budget on the reference machine before pilot approval |

Small test sets are smoke gates, not proof of general reliability. Expand them with real failure cases. Compare configuration changes on the same fixtures; model upgrades and quantization changes must repeat the gates. Already delivered data cannot be recalled after revocation.

## 21. Delegated Identity and Revocation Contract

1. The backend authenticates the human session and issues short-lived delegation for a specific AI task. Bind delegation to subject, service actor, audience, project/document scope, task ID, expiry and revocation state. Never derive the acting user from an arbitrary `user_id` supplied by the model or Orchestrator.
2. On each tool call, authenticate the service and validate delegation. Effective permission is the intersection of current user permissions, service scope and task scope. Reject incorrect audience, expired/revoked delegation and access outside the task boundary. Signing keys remain in the trusted backend.
3. Approval endpoints accept authenticated interactive human sessions only, with CSRF protection where cookie sessions are used. Delegation and service tokens cannot approve. Publish still verifies the human-bound approval and current permissions transactionally.
4. Recheck permissions before sending content to the model, before returning generated content and before saving a draft/export. Track all source dependencies of generated answers and suggestions; deny access if a required source is no longer authorized.
5. Initially buffer generated answers rather than stream them, allowing a final authorization check before response release. Define revocation at that final check as the delivery decision point; do not promise recall of bytes already delivered. Cancellation on revocation is best effort and cannot remove a prompt already received by the runtime.
6. Coordinate ACL changes and publish transactions using a consistent policy revision/locking protocol so revocation cannot race an unchecked publication. Test this with separate database connections.

## 22. Durable Upload and Version Creation Contract

1. Authorize and reserve quota before accepting bounded upload bytes. Use server-generated storage keys and temporary files inside controlled storage; never trust filenames as paths.
2. Validate intake, compute hash and durably finalize an immutable blob before committing a usable version reference. Extraction failure is a separate processing status; it does not silently alter or replace original bytes. Block retrieval/indexing of quarantined content.
3. Commit document/version metadata and the durable indexing job/outbox record together. A version is eligible for approval only when storage and required intake checks are ready. No partially uploaded version may be published or indexed.
4. Scope idempotency keys to actor and operation and bind them to the request fingerprint. Matching retries return the original outcome; different payloads with the same key return a conflict.
5. Reconcile orphaned temporary/final blobs after failures using a grace period and checks against committed references and in-flight uploads. Never delete referenced immutable versions as orphan cleanup. Test interruption before blob finalization, before DB commit and after commit but before response delivery.

## 23. First Implementation Sequence

1. Define the endpoint permission matrix and immutable business rules, including initial publication, rollback and approval invalidation.
2. Create backend skeleton, dependency locks, local PostgreSQL configuration and migration workflow. No model download is needed for this stage.
3. Implement users, sessions, project membership and document authorization with negative-access integration tests.
4. Implement durable uploads, immutable versions and authenticated downloads, including failure recovery tests.
5. Implement approval and atomic publication with replay, expiry, revocation and concurrent-publish tests on real PostgreSQL.
6. Add the minimal document UI, then isolated extraction, retrieval, local inference and the full quality gates.

The first runnable checkpoint is the document core without AI. Completing that checkpoint does not imply that offline AI quality, parser isolation or pilot readiness has been validated.

## 24. Container-First Deployment Requirement

Docker Compose is the default installation and execution path, including development. Run the backend, Orchestrator, ingestion workers, PostgreSQL/pgvector, local inference and ingress/static frontend delivery in containers wherever supported. Use a one-shot container for migrations and containerized backup/restore tools. Build React assets in a build stage; serve static output without a Node development server in production. Share build layers where useful while preserving separate runtime identities and permissions.

- **Host prerequisites:** Docker Engine and Compose, persistent storage, and optional compatible GPU drivers/container GPU support. Do not require host Python, Node, PostgreSQL or model-runtime installations. Host firewall, disk encryption and GPU drivers remain host responsibilities; Docker is not a complete machine-security boundary.
- **Persistence:** use explicitly managed named volumes for database and document data. Only the backend mounts managed documents for writing. Model weights are a separate read-only mount for the runtime. Worker scratch storage is bounded and ephemeral. Container recreation must preserve committed documents and database state. Backup copies must also exist outside the source volumes and host failure domain.
- **Network exposure:** publish only application ingress in the production profile. Keep DB, worker control, Orchestrator and inference ports private. Separate service networks and host firewall rules enforce the allowed paths; membership in a Compose network alone does not provide per-route API authorization or a full egress policy. Restrict Docker-published ports through Docker-aware firewall rules and verify reachability from outside the host.
- **Runtime hardening:** use non-root application images, dropped capabilities, no privileged mode, no Docker socket and read-only root filesystems where compatible. Explicitly allocate writable scratch paths and memory/CPU/PID limits; bound GPU concurrency at the application layer. Document necessary exceptions for third-party images.
- **Configuration and secrets:** provide non-secret examples and validate required settings at startup. Deliver each service only its required secret files, with host file permissions; never bake secrets into images or distribute the application's complete environment to every container. Local Compose secret files are not an encrypted secret manager.
- **Reproducibility and offline installation:** pin dependencies and release image digests. Supply a release manifest, image archives, model artifacts, checksums, signed update metadata and offline import/install instructions. Initial online image builds may download dependencies; a claimed offline installation must work from the supplied bundle on a disconnected clean host.
- **Operations:** use health checks and bounded readiness waits; run migrations explicitly before starting a compatible application version. Document installation, restart, upgrade, backup and restore commands. Back up before migrations; do not promise automatic database rollback across incompatible schema changes.
- **Profiles:** keep the document core runnable without downloading a model. Add explicit CPU/GPU inference profiles after benchmarking. Development-only bind mounts and debug ports must not appear in the production profile. Health checks must not load a second model copy or require GPU inference for basic process liveness.

**Acceptance:** on a clean supported Linux host with only the documented prerequisites and release bundle, bring up the selected Compose profile, apply migrations, pass health/readiness checks, restart/recreate containers without data loss, verify private ports and denied network paths, and complete backup/restore. Repeat the installation and normal processing with external network access disabled before claiming offline readiness.


## 25. Implementation Progress

The container bootstrap, local identity/project access, durable document uploads,
restricted document access, immutable versions, human approval/publication/rollback,
local React UI and isolated text ingestion with lexical search are implemented.
See [implementation status](docs/implementation-status.md) for verified behavior,
review fixes and remaining release gates, and [README](README.md) for commands.
A read-only AI task scaffold (delegated tasks, orchestrator, citation validation) exists without a model service. Model inference, vector retrieval, PDF processing and release hardening remain pending; the architecture above
is a target contract, not a claim that all controls are already implemented.


## 26. Product Language

English is the product UI language: navigation, forms, messages, accessibility labels
and operational output. The application shell uses left-to-right layout. Document
content retains its original language and uses automatic direction where appropriate.
Future AI responses and generated documents default to English unless the user
explicitly requests another language. UI language does not imply AI language quality; each supported document/model language
still requires separate evaluation.
