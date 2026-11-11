# Knowledge Core

A **headless knowledge system**. It connects to storage systems and databases through
**connectors**, reads their documents and data, works out what they contain, and exposes
permission-aware knowledge to any client through one API. The core has **no user interface**:
the web app, a CLI, chat bots and MCP clients are plugins that only use the API.

Everything runs locally by default. A connector may need the internet (for example a cloud
drive) or may not (an on-premises database); each connector says which, and the core enforces it.

> **Status: restructuring.** Phase A of the MySQL source has started: the protocol and a tested read-only
> MySQL connector exist (`connectors/`); the core-side catalog is next.
> The project started as a private document workspace and its goal
> changed to a general knowledge core. Much of the document-workspace code is reused (identity,
> permissions, ingestion, search, routing, answers, model services). The connector layer, the
> database source and the API split are what we build next. The state before the change is kept as
> git tag `snapshot-document-workspace`.

## Goal and decisions

| Question | Decision |
|---|---|
| What is built here? | The core only: API, connector runtime, knowledge pipeline, permissions, answer engine |
| Who writes connectors? | Anyone. Connectors are untrusted, sandboxed containers behind a versioned protocol |
| Offline? | Per connector. The core itself works fully offline |
| Tenancy | One organization per install |
| First source | **MySQL**: connect, understand the structure, profile all data, learn what it means |

## Where things are

| Path | What it is |
|---|---|
| `backend/` | The core service (Python/FastAPI, PostgreSQL): identity, permissions, documents, ingestion, retrieval, routing, chat, audit |
| `orchestrator/` | The only component that talks to the chat model; leases tasks, validates model output |
| `connectors/` | The connector SDK and the **MySQL connector** (first source), plus the throwaway MySQL test database |
| `workers/` | Isolated, secret-free helpers: text/PDF parser and the ingestion controller |
| `clients/web/` | The web UI, now a **client** of the API (React, served by the `web` container) |
| `deploy/`, `scripts/` | Database bootstrap, backup/restore verification, boundary checks, model evaluation |
| `compose.yaml` | The core stack (database, backend, workers, web ingress) |
| `compose.ai.yaml` | Optional local models: chat model and embedder, each isolated |
| `docs/` | Design documents (below); `docs/archive/` keeps the document-workspace era |

## Documents

- **[Core architecture](docs/core-architecture.md)**: components, connector model and protocol,
  trust model, permissions, API outline, phases. Start here.
- **[MySQL source](docs/mysql-source.md)**: the first connector: what "understand all the data"
  means, the catalog, profiling, caches, security, acceptance criteria.
- **[Connector protocol v1](docs/connector-protocol.md)**: the calls a connector answers (implemented for
  `records`), errors, and the guarantees every connector must keep.
- [Permissions](docs/permissions.md): the permission contract the current code implements.
- [Local hardware notes](docs/local-hardware.md): what the development machine can run.
- Archive: [original blueprint](docs/archive/private-ai-platform-architecture.md),
  [document-workspace README](docs/archive/document-workspace-readme.md) (API reference, backup and
  restore, PDF support, routing details of the current code),
  [implementation status and measurements](docs/archive/document-workspace-status.md),
  [model evaluation results](docs/archive/model-evaluations/).

## Run it

Needs Linux with Docker Engine and Compose; nothing else on the host.

```sh
make init                          # once: generate local secret files
make up                            # core stack (no AI models)
UNEVALUATED_TEST_MODEL=1 make up-ai   # plus local chat model + embedder (needs the model volume)
make check                         # tests, boundary checks, lint, type checks
```

The web client is at **http://127.0.0.1:8080** (loopback only). Create the first administrator
with `docker compose run --rm backend python -m app.cli`. See the archived README for the API,
backups and the local-model setup (`ai_models` volume).

## Principles that carry over

- **Model output is untrusted.** Models never get credentials, storage or the database; the
  backend decides what they may read and rechecks permissions before any text reaches a model.
- **Deny by default.** Unknown permissions mean no access.
- **Least privilege.** Every service gets only its own secrets, network and mounts, and
  `scripts/check-boundaries.py` verifies that in the resolved Compose configuration.
- **Honest quality gates.** A model that has not passed its evaluation is labelled as such on
  every answer.
- **Recoverable.** Backups and restores are verified, and schema changes are explicit migrations.
