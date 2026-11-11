# Core system architecture (draft v0.1)

Status: proposal for review. Nothing here is implemented yet except where marked "exists today".
The first source to be built is MySQL; its plan is in [mysql-source.md](mysql-source.md).

## 1. Goal

A **headless knowledge core**: it connects to storage systems and databases through
**connectors**, reads their documents and data, and builds permission-aware knowledge that any
client can search and ask questions of. The core has **no user interface**. The web UI, a CLI,
chat bots or an MCP client are clients/plugins that only use the public API.

### Decisions already made

| Question | Decision | Consequence |
|---|---|---|
| Who writes connectors? | Anyone | Connectors are **untrusted**: strict versioned protocol, sandboxed, quota-limited |
| Offline? | Per connector | A connector declares what network it needs; the core enforces it. The core itself works fully offline |
| Tenancy | Single organization per install | No multi-tenant isolation; still one namespace per source |
| Scope of this project | The core only | We ship the core, the protocol, an SDK, a test kit and a few **reference** connectors, not a catalogue |

### Non-goals

- A catalogue of production connectors (others build them).
- A UI inside the core.
- Multi-tenancy.
- Replacing the sources: the core reads; writing back to a source is out of scope.

## 2. Components

```
 Clients / plugins     web UI · CLI · chat bots · office add-ins · MCP clients
                                   │  one versioned public API (REST + events + MCP)
 ┌───────────────────────────────── CORE ───────────────────────────────────┐
 │ API gateway        auth, API keys / service accounts, rate limits         │
 │ Identity & access  users, groups, external identity mapping, ACL engine   │
 │ Knowledge store    items, versions, passages, vectors, cards, provenance  │
 │ Pipeline           fetch → dedupe → extract → chunk → embed → describe    │
 │ Retrieval & answer routing, hybrid search, answer engine, conversations   │
 │ Scheduler & jobs   sync schedules, retries, leases (Postgres-backed)      │
 │ Connector supervisor  starts/stops connector containers, enforces policy  │
 │ Audit              append-only security and access events                 │
 └────────▲───────────────────────────────────────────────▲─────────────────┘
          │ connector protocol (private network, pull)      │ model services
   ┌──────┴───────┐ ┌──────────────┐ ┌──────────────┐     │ chat model, embedder,
   │ connector A  │ │ connector B  │ │ connector C  │     │ extractors (OCR, PDF)
   │ (own secrets,│ │ (offline:    │ │ (online:     │     │ isolated, secret-free
   │  own network)│ │  no network) │ │  egress list)│     │
   └──────────────┘ └──────────────┘ └──────────────┘
```

Deployment stays Docker Compose on one host: **one modular core service plus workers**, not many
microservices. Postgres holds metadata, jobs and (later) vectors; blobs sit behind a storage
interface (local volume or S3-compatible).

## 3. Connector model

A connector is a **container image plus a manifest**. The core never imports connector code.

### 3.1 Manifest (`connector.json`)

```json
{
  "id": "com.example.smb",
  "version": "1.2.0",
  "protocol": "1",
  "kinds": ["documents"],
  "capabilities": ["list", "changes", "content", "permissions"],
  "network": {"mode": "none"},
  "config_schema": {"type": "object", "properties": {"host": {"type": "string"}}},
  "secrets": ["username", "password"],
  "limits": {"item_bytes": 52428800, "requests_per_minute": 600}
}
```

- `kinds`: `documents`, `records` (structured rows), or both.
- `network.mode`: `none` (offline source) | `egress` with an explicit host allow-list.
  The core refuses to start an `egress` connector when the install is in **offline mode**.
- Images are pinned by digest; a signature is optional but recommended.

### 3.2 Protocol v1 (HTTP + JSON, core calls the connector)

Pull-based on purpose: the core controls rate, quotas and timing, and the connector never holds
a credential for the core.

| Call | Purpose |
|---|---|
| `GET /v1/describe` | Manifest echo, capabilities, config schema |
| `POST /v1/check` | Test credentials and connectivity |
| `POST /v1/list {cursor}` | Page of items: `external_id, kind, title, mime, size, modified, version_token, parent, source_uri` |
| `POST /v1/changes {since}` | Created / updated / deleted ids since a cursor (optional; otherwise the core diffs `list`) |
| `GET /v1/items/{id}/content` | Bounded bytes (documents) or bounded rows (records) |
| `POST /v1/items/{id}/permissions` | Who may read: external principals (users, groups) |
| `POST /v1/principals {cursor}` | External users, groups and memberships, for ACL sync |
| `POST /v1/schema` | (records) tables, columns, keys, comments, size estimates |
| `POST /v1/profile` | (records) chunked, resumable statistics over every row of a table |
| `POST /v1/sample` | (records) a few rows with sensitive columns masked |
| later: `POST /v1/query` | (records) validated read-only queries on an allow-listed semantic layer |

Everything is paginated, size-bounded and time-limited. Errors are typed (`retry_later`,
`forbidden`, `gone`, `unsupported`).

### 3.3 Trust model

Connector output is **untrusted data**, like the PDF parser's today.

- It runs with a read-only root filesystem, no host mounts, dropped capabilities, memory/CPU/PID
  limits, and **only its own secrets** (mounted as files).
- It cannot reach the core's database, storage, other connectors or the model services.
- The core validates every response (types, sizes, UTF-8, counts), computes content hashes
  itself, and treats a failed or malformed permissions call as **deny**.
- A connector can only create and describe items in **its own source namespace** and can only
  speak for principals of that source.
- Resource caps, an administrator kill switch, and an audit trail of every call.

### 3.4 Offline and online

The `network` block is enforced by Compose networks / an egress proxy: `none` connectors sit on
an internal-only network. The core itself never needs the internet. An online connector is an
explicit, visible exception.

## 4. Knowledge

Each external item becomes a **versioned knowledge item**:

`item(source, external_id) → versions → passages (text + vector + page/offset) → card
(model-written description)`; plus `provenance`: `source_uri`, `fetched_at`, `version_token`,
content hash, connector id and version.

- **Dedupe and change detection** by version token and content hash. Unchanged items are not
  re-processed.
- **Deletion propagation:** a `gone` item becomes a tombstone and its passages, vectors, cards
  and cached answers are purged; a conversation that cited it shows "source removed".
- **Freshness:** every answer reports the version and sync time of each source it used.
- **Records** (database rows): small reference tables may be rendered to passages; large or live
  data is reached through the governed query layer (phase 4), not bulk-embedded.
- **Retrieval, routing and answering** exist today (see `README.md`): document cards, a model
  choosing among a short candidate list, hybrid search inside the chosen document, citations.

## 5. Permissions

- **Principals:** core users and groups, plus **external identities** (for example an SMB user or
  a Nextcloud group) that an administrator maps to core users, or that arrive through SSO claims.
- **Item ACL:** the set of principals who may read, as reported by the connector for that item.
- **Effective access** = item ACL ∩ source access policy ∩ core policy. Evaluated in the same
  statement as retrieval, and again before text reaches a model (exists today for documents).
- **Unknown means deny.** ACLs are refreshed on a schedule and on change events; a stale ACL
  window is reported, never hidden.
- Today's project and document ACLs become the permission model of one built-in source (below).

## 6. Public API v1 (outline)

REST + JSON, OpenAPI published, versioned in the path.

| Area | Examples |
|---|---|
| Sources | create/list/update a source (connector + config + schedule), trigger sync, health |
| Connectors | install, list, inspect manifest, enable/disable, kill switch |
| Items | list, get metadata and provenance, fetch content (if permitted) |
| Search | hybrid search, filtered by source / type |
| Ask | one-off question; conversation with memory, focus and clarifying questions |
| Jobs | sync status, errors, queue |
| Admin | users, groups, identity mapping, API keys, audit query, backup |
| Events | server-sent events / webhooks: sync finished, item added/removed, answer ready |

Authentication: local users and OIDC/LDAP, plus **service accounts with scoped API keys**.
An **MCP server** exposes `search`, `read_item` and `ask` as tools, always with the caller's own
permissions (delegated, never a master key).

## 7. Reference connectors and test kit

We build only what proves the contract:

1. **Mock connector** for tests (configurable items, permissions, failures).
2. **Local folder** (offline, read-only): the simplest real source.
3. **Managed workspace**: today's upload, approval and publish workflow, packaged as a built-in
   source. This preserves current work and demonstrates a rich ACL model.
4. **Read-only MySQL** (records), the first real source, proving the `records` kind. It reads the
   structure, profiles all data with SQL, and builds a catalog the model describes. See
   [mysql-source.md](mysql-source.md).

A **conformance test kit** any connector author can run: pagination, size limits, malformed
responses, permission denial, deletion, retry behaviour, and a check that the manifest's network
claims match actual behaviour.

An **SDK** (Python first) implements the protocol server so authors write only list/fetch logic.

## 8. Mapping from the current code

| Today | In the core |
|---|---|
| `backend` identity, projects, document ACLs | Identity & access, plus the permissions of the built-in managed source |
| `backend` documents, versions, approvals | The managed-workspace source |
| `workers` (parser, indexer) | Pipeline extractors, run as isolated processors |
| `backend` ingestion leases | Scheduler & jobs |
| `app/routing.py`, `ai.py`, `chat.py` | Retrieval, answer engine, conversations |
| `orchestrator`, `model`, `embedder` | Model services (unchanged boundaries) |
| `frontend` | A separate client that talks only to the public API |
| `scripts/check-boundaries.py` | Extended to connector containers |
| backup/restore verification | Extended to knowledge items and provenance |

## 9. Phases

1. **Contracts.** API v1 (OpenAPI), connector protocol v1, knowledge schema. Review before code.
2. **Split the clients.** UI uses only the public API; service accounts and API keys; MCP server.
   Acceptance: the web UI works unchanged with the core's internals hidden behind the API.
3. **Connector runtime.** Supervisor, manifest enforcement, SDK, test kit, mock + local folder +
   managed workspace; incremental sync, deletion propagation, ACL sync.
   Acceptance: a third-party mock connector cannot read another source or reach the network
   unless its manifest says so (tested).
4. **Records.** MySQL connector (catalog, profiling, descriptions), then the semantic layer and
   governed queries, per [mysql-source.md](mysql-source.md).
5. **Scale and quality.** Vector index (pgvector or similar) replacing in-process comparison,
   entities and relations, freshness reporting.

## 10. Risks and honest limits

- **Permission mapping is the security-critical part** and the easiest to get subtly wrong;
  every connector must pass the permission tests in the kit.
- **Untrusted connectors** can lie about content. Core-computed hashes, source namespaces and
  citations limit the damage but do not make a malicious source trustworthy.
- **Database questions** (see [mysql-source.md](mysql-source.md)) need a semantic layer; small local models cannot generate queries
  reliably, so this stays a later, explicitly limited phase.
- **Model quality and speed** are bounded by hardware: a 3.8B model on a 4-core CPU answers in
  roughly 30-90 seconds and does not pass the project's own quality gates yet.
- **Scale:** the current in-process vector comparison suits thousands of passages, not millions.
- **Support burden:** an open connector ecosystem needs a stable protocol; breaking changes need
  a protocol version bump and a deprecation period.

## 11. Open questions

1. Is the protocol HTTP/JSON (simple, any language) or gRPC (typed, streaming)? Proposed: HTTP/JSON.
2. Should connectors be able to **push** change events (webhooks) in addition to being polled?
3. Do we sign and curate connector images (a registry), or leave trust entirely to the operator?
4. Which identity systems must the first release support (local users, OIDC, LDAP)?
