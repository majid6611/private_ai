# Connector protocol v1

Status: **implemented for the `records` kind** (reference implementation: the MySQL connector in
`connectors/mysql/`). The `documents` kind (list, changes, content, permissions) is specified in
[core-architecture.md](core-architecture.md) and not built yet.

A connector is a container that answers a few JSON calls from the core. The core calls the
connector; the connector never holds a credential for the core. Anyone may write one, so the
core treats every response as **untrusted data**.

## Transport

- HTTP/1.1 and JSON over a private network. One request is served at a time.
- Every call carries `Authorization: Bearer <connector key>` (a per-connector secret the
  operator shares with the core). Missing or wrong key: `401`, compared in constant time.
- Request bodies are at most 1 MiB, responses at most 8 MiB. Bodies are never logged.
- Paths are versioned: `/v1/...`. A breaking change needs `/v2` and a deprecation period.

## Errors

`{"error": {"code": "...", "message": "..."}}`. The message is short and **never contains driver
text, credentials or row data**.

| code | HTTP | meaning |
|---|---|---|
| `bad_request` | 400 | malformed input or an unsafe identifier |
| `unauthorized` | 401 | wrong or missing key |
| `forbidden` | 403 | the source refused, the account is not allowed, or it can write |
| `not_found` | 404 | no such schema or table (or it is outside the allow-list) |
| `gone` | 410 | the object disappeared |
| `retry_later` | 503 | source unreachable or a time limit was hit; safe to retry |
| `unsupported` | 501 | the connector cannot do this (for example a view) |
| `internal` | 500 | anything else |

## Manifest (`GET /v1/describe`)

```json
{"protocol": "1", "id": "core.mysql", "version": "0.1.0", "kinds": ["records"],
 "capabilities": ["check", "schema", "profile", "sample"],
 "network": {"mode": "egress", "hosts": ["db.internal:3306"]},
 "secrets": ["password"], "read_only": true}
```

`network.mode` is `none` (offline source) or `egress` with an explicit host allow-list; the core
enforces it. `read_only: true` is a promise the core verifies with `check`.

## Calls (kind `records`)

### `POST /v1/check`

Connects, never writes, and reports whether the account may only read.

```json
{"ok": true, "read_only": true, "violations": [], "grants": ["`shop`.*"], "tls": true,
 "server_version": "8.4.9", "schemas": ["shop"], "warnings": []}
```

`violations` lists every privilege beyond reading (`INSERT on ...`, `GRANT OPTION on ...`,
roles whose privileges cannot be verified). **All other calls refuse an account with violations.**

### `POST /v1/schema {"schemas": ["shop"]?}`

Tables and views with columns (type, nullability, key, comment, `sensitivity` guessed from the
name), primary key, indexes, foreign keys, size estimates. Only allow-listed schemas the account
can see are returned.

### `POST /v1/profile {"schema", "table", "columns"?, "limits"?}`

Statistics computed by the database over **every row**, or over a deterministic sample for
tables above `max_rows_exact` (then `exact: false`, `sample_basis`, and a warning).

Per column: `non_null`, `null_count`, `null_rate`, `distinct` (+`distinct_exact`), `min`, `max`,
`avg`, `stddev`, text `length`, `top_values` (at most 10, only for low-cardinality columns),
`patterns` (email, date formats, numbers, ... among sampled distinct values),
`mixed_date_formats`, `likely_identifier` and `duplicates` (almost-unique columns without a
unique index), `sensitivity`, and `suppressed` (what was withheld).
Per table: `row_count` (+`row_count_exact`), `has_primary_key`, and for each declared foreign key
the number of `orphans` (rows pointing at a missing parent).

Limits the caller may lower but never raise: `max_seconds`, `max_rows_exact`, `sample_rows`.
A statement that exceeds the time limit becomes a warning, not a failure.

### `POST /v1/sample {"schema", "table", "n"?}`

At most 20 rows. Cells of sensitive columns, and any value that looks like an email, phone,
card number or national id, are replaced by placeholders such as `[email]`. Long text is cut
at 200 characters; binary values are not returned.

## Guarantees a `records` connector must keep (tested by the reference implementation)

1. **Read-only.** The session is read-only, only `SELECT`/`SHOW` statements run, and `check`
   verifies the account's privileges without attempting a write.
2. **No credentials out.** Not in responses, errors or logs.
3. **No unmasked personal data** in profiles or samples.
4. **Identifiers are validated** before they reach SQL.
5. **Bounded work:** every statement has a time limit; responses are size-limited.
6. **Honest quality:** approximate results say so.

## Conformance

A connector passes when it satisfies the guarantees above and the calls behave as specified.
The reference test suite (`connectors/mysql/tests`) is the model; a generic conformance kit that
any connector can run is planned.
