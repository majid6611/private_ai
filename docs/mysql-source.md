# MySQL as the first source (plan v0.1)

Status: **step A1-A3 built** (protocol, connector, test database: see "What exists" below); the core-side
catalog is next. Part of [core-architecture.md](core-architecture.md); it defines the `records`
side of the connector protocol and the first connector that uses it.

## 1. Goal

Connect the core to a MySQL database that is **not** part of the core, then:

1. **Understand the structure**: tables, columns, types, keys, relationships.
2. **Review all the data**: complete profiling of every table and column.
3. **Learn what it means**: plain-language descriptions of every table and column.
4. **Keep a cache** so the model does not have to hit the database to answer common things.

## 2. What "review all data" means

The model does **not** read every row. On this class of hardware it reads about 65 characters a
second, and rows are the wrong unit anyway. Instead the database computes statistics over *every*
row, which is exact, fast and needs no model; the model then reasons over those statistics and a
small masked sample.

| Layer | Produced by | Contents |
|---|---|---|
| Structure | the database (`information_schema`) | schemas, tables, columns, types, keys, indexes, declared foreign keys, comments |
| Profile | SQL aggregates over all rows (chunked) | row counts, null rates, distinct counts, min/max, common values, length and pattern stats, date ranges, duplicate keys, orphaned references |
| Samples | a few rows per table | masked; never raw personal data |
| Meaning | the chat model, then an administrator | table and column descriptions, glossary, likely joins |

Very large tables use database estimates and sampling, clearly marked **approximate**.

## 3. Connector side

A container implementing the `records` calls of the connector protocol (HTTP + JSON), written in
Python with a MySQL driver. Its manifest declares:

```json
{"id": "core.mysql", "kinds": ["records"],
 "capabilities": ["describe", "schema", "profile", "sample", "query"],
 "network": {"mode": "egress", "hosts": ["<mysql-host>:3306"]},
 "secrets": ["username", "password"], "tls": "required"}
```

| Call | Behaviour |
|---|---|
| `POST /v1/check` | Connects, reports server version, confirms the account is **read-only** and lists granted schemas |
| `POST /v1/schema` | Tables, columns, keys, indexes, comments, row and size estimates |
| `POST /v1/profile {table, columns}` | Chunked aggregates with limits (below); resumable |
| `POST /v1/sample {table, n}` | Up to N rows with sensitive columns masked |
| `POST /v1/query {sql, limits}` | Validated read-only SELECT with row, byte and time caps (phase C) |

Controls: `SET SESSION TRANSACTION READ ONLY`; per-statement `MAX_EXECUTION_TIME`; rows and
bytes per response capped; a throttle between chunks; profiling runs one table per call so the core can pause and resume at table
granularity (large tables are sampled, never scanned in full); a configurable window (for example nightly) and a preference for a **read
replica**. If a table has no usable key, profiling falls back to sampling and says so.

## 4. The catalog (in the core)

New core tables, all scoped to a `source`:

- `source_objects`: databases, tables, columns with type, nullability, keys, comments.
- `profiles`: versioned profile results (JSON), with `exact` or `approximate` and the time taken.
- `descriptions`: text per object with `origin` (`model` or `curated`), `status`
  (`draft`, `approved`, `edited`) and the model that wrote it. **Curated text always outranks the
  model's.**
- `relationships`: declared foreign keys plus inferred joins with evidence and a confidence.
- `glossary`: business terms mapped to objects.
- `exposure`: which tables and columns each group may see (everything is hidden until selected).

Descriptions are embedded like document cards, so the existing routing works on tables: the
question "what data do we have about customers?" finds the customer tables, explains them, and
cites the catalog.

## 5. Caches

| Cache | Holds | Invalidated by |
|---|---|---|
| Catalog | structure, profiles, descriptions | schema change or scheduled refresh |
| Knowledge | text-heavy rows rendered to passages with vectors (opt-in, per table) | incremental sync by `updated_at` or key range |
| Query result | results of repeated validated queries (phase C) | a per-table data version (max update time and row count) or a time limit |

Large transactional tables are **not** embedded. Cached data is subject to the same access rules
as the source, purged when exposure is withdrawn, and encrypted at rest with the rest of the core.

## 6. Security

- A **read-only MySQL account** created by the database owner: SELECT on chosen schemas, no
  file, process or administrative privileges. The connector refuses to run if it finds more.
- The model and the core's other services **never see credentials**; only the connector holds
  them, as a mounted secret.
- Network: the connector reaches only the declared host and port, over TLS.
- **Sensitive data:** columns are classified (email, phone, national ID, names, free text).
  Flagged columns are excluded, or sampled and cached only in masked form, and are never sent to
  a model unmasked.
- **Cell contents are untrusted.** A value can say "ignore your instructions"; results are data,
  as with document text.
- **Access:** MySQL has no notion of the core's users, so access is the core's own policy
  (`exposure`: table and column allow-lists per group). Row-level rules are out of scope for the
  first version.
- Every call is audited without logging parameters that contain data.

## 7. Development fixture

A throwaway **MySQL 8 container** (Compose profile `fixtures`, never part of the product) filled
by a deterministic generator with synthetic customers, orders, products and support tickets, and
deliberate problems: duplicate keys, orphaned references, mixed-format dates, nulls, an enum
column, a text-heavy table, and columns of fake personal data. It makes tests reproducible
without downloading anything.

## 8. Acceptance criteria (phase A: the catalog)

1. `check` fails if the account can write, and succeeds for a read-only account.
2. Schema import matches `information_schema` exactly for the fixture.
3. Profiling matches a known answer for the fixture (counts, nulls, distincts, duplicates,
   orphans), is resumable after an interruption, and respects row, byte and time limits.
4. Personal-data columns are detected in the fixture and never leave the connector unmasked.
5. Model-written descriptions are stored as drafts, can be edited and approved, and curated text
   wins in answers.
6. The question "what data do we have about customers?" returns the right tables with sources,
   and a question about an unexposed table returns nothing about it.
7. Credentials never appear in logs, responses or model prompts (tested).
8. The connector cannot reach any host but the declared one (tested).

## 8a. What exists

- `connectors/sdk/connector_sdk.py`: protocol server (auth, size limits, typed errors).
- `connectors/mysql/`: the connector (`check`, `schema`, `profile`, `sample`), see
  [connector-protocol.md](connector-protocol.md).
- `connectors/fixtures/mysql/` and `compose.fixtures.yaml`: the synthetic test database with
  known answers, and 24 tests (`make check` runs them): read-only enforcement, credential safety,
  masking, exact profiles for every deliberate problem, sampling of a large table, the HTTP layer.
- Not yet: the core side (sources, catalog tables, profiling jobs, model descriptions, search),
  the connector as a deployed service with its network allow-list, and `query` (phase C).

## 9. Phases

- **A. Catalog:** structure, profile, descriptions, review, search over the catalog.
- **B. Knowledge cache:** opt-in indexing of text-heavy tables with incremental sync.
- **C. Governed questions:** curated metrics and a semantic layer, validated read-only SQL, a
  query cache, and confirmed questions saved as examples. A small local model is **not reliable
  at writing SQL for a real schema**, so this is restricted to allow-listed objects and shows the
  SQL and the data with every answer.
- **D. Refresh and summaries:** scheduled refresh and precomputed aggregates.

## 10. Open questions

1. How large is the real database, and is there a **read replica**? MySQL version (5.7 or 8)?
2. Is it mostly text content (tickets, articles) or transactions (orders, accounts)?
3. Does it contain personal data, and must different users see different tables or columns?
4. Should profiling run on a schedule, on demand, or both?
