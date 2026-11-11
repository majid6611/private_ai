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

PDF support added 2026-11-09 (migration 0008): isolated extraction with resource-limited
child process (14 parser tests incl. decompression bomb, cyclic page tree, 301 pages,
garbage/truncated/oversized input, plus HTTP-level tests), backend validation of untrusted
extraction output with page ranges computed server-side, inert PDF downloads, approver
access to the exact candidate, UI PDF card/review/page-numbered citations, restore
verification of derived text, and a scripted browser run of upload -> review -> approve
-> publish -> indexed -> search -> ask. 48 backend tests passed.

Chat added 2026-11-10 (migration 0010): private threads with follow-up retrieval,
bounded history, evidence budget, redaction on lost access and real deletion; Chat tab with
thread list, citations that open the PDF page, and a thinking indicator. The old
single-question box was replaced. A 1.5B CPU model needs roughly 40-70 s per answer on the
development host; chat quality with a model this small is unproven.

phi4-mini evaluation 2026-11-10 (52 cases, top-5 passages): 33/40 correct (82.5%), citations
include the true passage 36/40 (90%), 3/10 no-answer questions declined (4 more were valid
declines recorded as errors because an empty citation list was rejected; fixed: an empty list is
now a decline), 0 fabricated figures, latency p50 61 s / p95 116 s. FAILS the gates (correct
90%, citations 95%, decline 9/10); runs only in the labelled unevaluated test mode. Speed is
dominated by reading evidence (about 15 s per 1000 characters).

Model-assisted routing 2026-11-10 (migration 0014): a ROUTING stage before answering, model-
written document descriptions, allow-listed choice, "unsure" asks the user. Live latency on the
development host: routing 17-20 s, answer 32 s (1.7k chars) to 68 s (4.1k chars, list question).
Follow-ups in a settled chat skip routing. Known gaps: list questions can miss items; the first
question of a chat costs about 50-90 s.

Document routing 2026-11-10 (migration 0013): cards + vectors (embedder service), routing before
retrieval, clarifying questions, "not found" without a model call, per-chat document focus,
hybrid passage search inside the chosen document, larger evidence for "list all" questions.
Motivation: "send me all companions he worked. just companies name" was answered with the two
parties of a contract, because no word of the question occurs in the resume and the contract
shared "send"/"work". Cards are extractive (title, opening, headings), not model-written
summaries yet. Thresholds (`ROUTE_MIN_SCORE`, `ROUTE_MARGIN`) must be calibrated against the
real embedder; unit tests use a controllable fake embedder.

Forgiving retrieval 2026-11-10 (migration 0012): stemmed English search plus trigram typo
tolerance for both search and AI questions. Found via a real failure: "what programing
langues he knows?" returned no passages because the resume says "languages". Retrieval
benchmark with 10 added misspelled questions: typo cases 10/10, overall recall@5 50/52
(96%), recall@3 92%, recall@1 75%; the original 42 questions unchanged at 40/42. No-answer
questions still retrieve passages (declining is the model's job and is weak).
Synonyms need embeddings.

Model evaluation 2026-11-10 (qwen2.5:1.5b via Ollama, CPU, 52 synthetic cases, top-5
passages): 27/40 correct (68%), citations include the true passage 34/40 (85%), only 2/10
no-answer questions declined (it invents answers such as a parking-space count), 1 answer
with a fabricated figure, 1 invalid-citation failure, latency p50 22.6 s / p95 35.2 s. It
FAILS every gate except the minimum case count, so it runs only in the labelled
unevaluated test mode. In chat it also tends to repeat its previous answer when the same
passage holds several dates; extract-then-verify prompting (verbatim quote check) caught
its invented answers in a small trial but has not been built or evaluated.

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
- PDF: text-based PDFs are reviewed, published and indexed (see README, PDF support);
  scanned PDFs are flagged `NEEDS_OCR` but not read. No malware scanning, no DOCX, no OCR.
  Extracted text is untrusted derived data from the isolated parser; a compromised
  parser could misstate a PDF's content, and citations should be checked against the
  original.
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
