# Private AI Workspace

Local document workspace: authentication, project/document permissions, immutable
versions, independent human approval, atomic publication/rollback, isolated text
ingestion and permission-filtered lexical search. Includes an English React UI.

**AI answering needs an optional local model** (`make up-ai`, see "Local model" below).
Without it, questions that match documents report that no model is available; search works
regardless. Embeddings, DOCX/OCR and production readiness remain
pending. Text-based PDFs are supported (see below); scanned PDFs are detected and flagged
but not read. Text search is not represented as an AI answer. No cloud services are used
by the running application.

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

`make check` rebuilds the test images, then runs the backend integration tests, the
orchestrator prompt-injection/boundary tests (fake model, no network), a Compose
deployment-boundary check (which service may hold which secret, volume and network),
ruff and mypy.

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
intake and rolled back on failure. Text must be UTF-8. A PDF must start with the PDF signature
and is stored byte-for-byte as an immutable original (see PDF support).

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
snippets with immutable version IDs, character offsets and page numbers. It is lexical
search, not semantic retrieval or model inference, but forgiving: PostgreSQL English
stemming ("knows" matches "know"), plus typo tolerance through trigram word similarity
("langues" finds "languages") that only fills slots the exact leg left empty. Search and
AI questions share one retrieval function, so both apply identical permission filters
(including the fuzzy leg). It cannot match synonyms ("knows" vs "skills"); that needs
embeddings. The pg_trgm extension is installed by migration 0012; for a database created
before it, run once as the DB owner: `GRANT CREATE ON DATABASE <name> TO workspace_migration;`
There are no shared search/answer caches. PDF/OCR and vector/hybrid ranking await
their own quality and isolation tests.

Internal ingestion routes are denied at Nginx and require a separate service key at
the backend. A human token cannot substitute for that key, and the key does not
substitute for human approval. Never publish parser/indexer/backend/DB ports.

## Local model (optional, for testing)

```sh
docker volume create ai_models        # once; copy Ollama model files into it
UNEVALUATED_TEST_MODEL=1 make up-ai    # model container + orchestrator/backend settings
```

`compose.ai.yaml` adds an Ollama container (pinned by digest) on the internal inference
network only: no secrets, no ports, no egress, non-root, read-only root filesystem and
read-only weights, 3 GB memory cap. Only the orchestrator can reach it. The document core
runs without it. `MODEL_ID` selects the model (default `qwen2.5:1.5b`); the backend hands
the model `AI_CONTEXT_CHUNKS` passages per question (default 5 here, 8 otherwise).

The orchestrator refuses to call a model unless `models/evaluation.json` records
`passed: true` for it. `UNEVALUATED_TEST_MODEL=1` is the explicit local-testing override:
every answer is then stored with the model id and `evaluated: false`, and the UI shows an
amber "unevaluated test model" warning. To evaluate a model on the synthetic corpus:

```sh
docker compose --profile test run --rm -v "$PWD/models/work:/export" \
  -e RETRIEVAL_EXPORT=/export/cases.json -e RETRIEVAL_EXPORT_LIMIT=5 tests \
  python -m pytest -q tests/test_retrieval_eval.py
sh scripts/eval-model.sh qwen2.5:1.5b      # writes models/work/evaluation-*.json
cp models/work/evaluation-qwen2.5_1.5b.json models/evaluation.json   # only if passed
```

The evaluator scores correct answers, citations that include the true passage, fabricated
figures, declining when evidence is missing, and latency (architecture section 20). Its
checks are string-based smoke gates on a small synthetic set; they do not replace human
review. Models are given short source labels (S1, S2, ...), never database ids, and
every citation is validated against the passages they were shown. On this 4-core, 7 GiB
CPU-only host a 1.5B model needs roughly 40-70 seconds per question, and the first
request after startup also loads the model.

## PDF support

Text-based PDFs (up to 2 MiB) go through the same approval and publication flow as text.
Originals are only ever served as inert attachments (`attachment`, `nosniff`, `no-store`,
`Content-Security-Policy: sandbox`). An approver opens the exact candidate through
`GET /api/v1/approvals/{id}/candidate` (the UI's "Open PDF under review"), so approval
binds to the hash of the file they read.

After publication the indexer leases the PDF and sends it to the isolated parser, which
runs extraction (pypdf, pinned and hash-locked) in a child process with CPU, memory and
file-size limits, no network, no secrets and no writable files. Its output is treated as
untrusted derived data: the backend validates size, NULs, page ranges and chunk coverage,
stores the extracted text with the extractor name (`version_texts`), rebuilds chunk text
itself, and computes each chunk's page range. Search results and AI citations show
"page N". Limits: 300 pages, 2 million characters. Unreadable files get a terminal
verdict (`ENCRYPTED`, `MALFORMED`, `TOO_LARGE`, `TOO_MANY_PAGES`, `TIMEOUT`); a PDF with
no text layer is flagged `NEEDS_OCR` and not indexed. OCR is not implemented.
A compromised parser could return text that does not match the PDF, so reviewers should
check citations against the original file. A synthetic sample contract is in `demo/`
(`scripts/make-demo-pdf.py` regenerates it).

## Passwords and sharing

Any user can change their own password in the sidebar (`POST /api/v1/auth/password`):
it needs the current password, is rate-limited, keeps that session and signs out the
others, and does not invalidate pending approvals. An administrator can set a temporary
password for another user (`PUT /api/v1/users/{id}/password`); this signs that user out
and bumps their authorization revision, so their pending approvals and AI tasks need a
fresh review (an admin cannot reset an approver's password and approve in their name).
Document sharing now picks people by name from `GET /api/v1/projects/{id}/members`
(visible to project members only; names, no capability flags).

## Which document? (routing, clarification, memory)

Questions are not matched against every passage of every file. Each published document has a
**card** (a description written by the chat model while it is otherwise idle, plus section
headings) and vectors. For a question (`app/routing.py`):

1. **Shortlist:** cards are scored by vector similarity, then the best passages of the top
   documents; names written in the question (for example "Majid") add weight.
2. **Straight to the answer** when the user picked a document (the buttons below), when the chat
   topic is settled and the question is a follow-up, when only one document fits, or when the
   best score is far ahead (`ROUTE_DECISIVE_GAP`, 0.10).
3. **Otherwise the chat model chooses** among the top four candidates (a second, short model
   call, about 17 s on the development host). It can pick one, pick none ("I couldn't find
   anything about that"), or say it is unsure, in which case the user is asked "Which one do you
   mean?" with one button per document. The model can only choose from the allow-list stored on
   the task; it never sees document ids, and the backend rechecks permissions when it retrieves.
4. **Inside the chosen document** the best passages are found by word and meaning matches fused
   together. "List all" questions (`all`, `list`, `every`, ...) read more
   (`AI_LIST_EVIDENCE_CHARS`, default 4800 characters) and are slower and still incomplete when a
   section spans many passages.
5. **Memory:** the chat remembers the settled document ("Talking about: ..."; clearable), so
   "and his languages?" stays on it.

Measured on 59 questions over 8 documents (7 synthetic + a resume): vector scores alone chose
the right file 81% of the time (19% silent mistakes); vectors plus the model's choice chose
right 88%, silently wrong 3%, unsure 8%. Vector scores cannot tell "not in the documents" from
a weak match (unanswerable questions scored 0.52-0.74, real matches 0.51-0.80), so "nothing
fits" is decided by the model, not a threshold. Descriptions the model writes are shorter and
route worse than hand-written ones (69% vs 86% on cards alone), so the model's choice, not the
raw vector, makes the final call.

Vectors come from a separate **embedder** container (`nomic-embed-text`, Apache 2.0, 137M
parameters; `compose.ai.yaml`). It has no secrets, no egress and read-only weights, and only the
backend can reach it; the chat model cannot. A background worker in the backend fills in
passage vectors and extractive cards for published documents (existing ones are backfilled on
start), and the orchestrator adds model-written descriptions when idle (`card-lease`). A document
could try to describe itself favourably through prompt injection; cards only shortlist, and the
final answer still cites real passages. Without the embedder everything falls back to the word
search below. To install the model once: run an Ollama container with
`OLLAMA_MODELS=/models/models` on the `ai_models` volume and `ollama pull nomic-embed-text`.

## Chat

The **Chat** tab (per project) is a conversation with memory. Each chat is private to its
owner, saved until deleted, and limited to 200 turns. A turn is a question plus the AI
task that answers it, so every turn is retrieved, authorized and validated exactly like
a one-off question (below). What the thread adds:

- **Follow-ups:** a question that says "he/she/they" keeps the chat's settled document but is searched
  on its own words. Only a question with almost nothing to search for ("what about it?", "who signed
  it?") borrows the previous question's words, and only then are the last two exchanges (questions at
  most 300 characters, answers at most 600) sent to the model as context; replaying the previous answer
  for complete questions made phi4-mini repeat it.
- **Latency budget:** evidence sent to the model is capped (`AI_EVIDENCE_CHARS`, default
  2500 characters, at least one passage) because reading evidence dominates CPU time
  (about 15 s per 1000 characters with phi4-mini on the 4-core development CPU).
  Identical passages (the same file uploaded twice) are sent once, and passages reach the
  model in retrieval-rank order (`ai_sources.rank`).
- **Retrieval for follow-ups:** the new question is searched on its own words first; the
  previous question is borrowed only when the new one uses a pronoun ("it", "that") or has
  almost no searchable words, so earlier passages cannot crowd out the new answer.
- **History policy:** history is rechecked on every read. Losing project access hides the
  chat (404); if an answer cites a document you can no longer read, the answer is redacted
  and not replayed to the model. Deleting a chat deletes its turns, tasks and sources.
- **One answer at a time** per chat; unfinished turns that expire report `TASK_EXPIRED`.

API: `POST /api/v1/projects/{id}/chat/threads`, `GET .../chat/threads`,
`GET|PATCH|DELETE /api/v1/chat/threads/{id}`, `POST /api/v1/chat/threads/{id}/messages`.

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
no matching evidence complete with "Insufficient evidence". The project page has an "Ask" panel that polls the task and
shows the answer with citation buttons, or a clear "no AI model is available" state.

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
