"""Profile a table: statistics over every row, computed by the database.

The model never reads rows to learn what a table holds. The database counts, aggregates and
groups them (exact), or, for very large tables, does the same over a deterministic sample and says
so (approximate). Sensitive columns are detected by name and by value, and their values are
suppressed.
"""

import re
import time
from collections import Counter
from typing import Any

from connector_sdk import ConnectorError

from .config import Config
from .db import Session, quote
from .schema import find_table
from .sensitive import classify_name, classify_value, mask_cell

NUMERIC = {"tinyint", "smallint", "mediumint", "int", "integer", "bigint", "decimal", "numeric", "float", "double", "real"}
INTEGER = {"tinyint", "smallint", "mediumint", "int", "integer", "bigint"}
TEMPORAL = {"date", "datetime", "timestamp", "time", "year"}
SHORT_TEXT = {"char", "varchar", "enum", "set"}
LONG_TEXT = {"tinytext", "text", "mediumtext", "longtext"}
SKIP_VALUES = {"blob", "tinyblob", "mediumblob", "longblob", "binary", "varbinary", "json", "geometry", "point", "linestring", "polygon", "bit"}

GROUP = 5  # columns per aggregate statement
SAMPLE_ROWS = 5000
SAMPLE_RANGES = 10
PATTERN_SAMPLE = 200
TOP_VALUES = 10
TOP_VALUE_MAX_DISTINCT = 1000

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("empty", re.compile(r"^\s*$")),
    ("integer", re.compile(r"^-?\d+$")),
    ("decimal", re.compile(r"^-?\d+\.\d+$")),
    ("datetime_iso", re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?")),
    ("date_iso", re.compile(r"^\d{4}-\d{2}-\d{2}$")),
    ("date_slash", re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}$")),
    ("date_text", re.compile(r"^[A-Za-z]{3,9}\.? \d{1,2}, \d{4}$")),
    ("url", re.compile(r"^https?://", re.I)),
    ("uuid", re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)),
]


def pattern_of(value: str) -> str:
    for name, pattern in PATTERNS:
        if pattern.match(value):
            return name
    return classify_value(value) or "text"


def plain(value: Any) -> Any:
    """JSON-friendly values: exact decimals as text, temporal values as ISO strings."""
    if value is None or isinstance(value, (int, bool)):
        return value
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, bytes):
        return None
    return str(value)


def aggregates(column: dict[str, Any]) -> list[tuple[str, str]]:
    """(key, SQL expression) pairs for one column."""
    name, kind = quote(column["name"]), column["type"]
    items = [("non_null", f"COUNT({name})")]
    if kind in SKIP_VALUES:
        return items
    if kind not in LONG_TEXT:
        items.append(("distinct", f"COUNT(DISTINCT {name})"))
    if kind in NUMERIC:
        items += [("min", f"MIN({name})"), ("max", f"MAX({name})"), ("avg", f"AVG({name})"), ("stddev", f"STDDEV_POP({name})")]
    elif kind in TEMPORAL:
        items += [("min", f"MIN({name})"), ("max", f"MAX({name})")]
    elif kind in SHORT_TEXT | LONG_TEXT:
        items += [
            ("len_min", f"MIN(CHAR_LENGTH({name}))"),
            ("len_max", f"MAX(CHAR_LENGTH({name}))"),
            ("len_avg", f"AVG(CHAR_LENGTH({name}))"),
        ]
        if kind in SHORT_TEXT:
            items += [("min", f"MIN({name})"), ("max", f"MAX({name})")]
    return items


class Source:
    """Where the rows come from: the whole table, evenly spaced key ranges, or the first rows."""

    def __init__(self, table_sql: str, where: str = "", basis: str | None = None, rows: int | None = None) -> None:
        self.table_sql, self.where, self.basis, self.rows = table_sql, where, basis, rows

    def sql(self, extra: str = "") -> str:
        conditions = " AND ".join(c for c in (self.where, extra) if c)
        return f"FROM {self.table_sql}" + (f" WHERE {conditions}" if conditions else "")


def choose_source(session: Session, meta: dict[str, Any], schema: str, table: str, exact: bool, sample_rows: int) -> Source:
    full = f"{quote(schema)}.{quote(table)}"
    if exact:
        return Source(full)
    estimate = max(1, meta["row_estimate"] or 1)
    pk = meta["primary_key"]
    by_name = {c["name"]: c for c in meta["columns"]}
    if len(pk) == 1 and by_name[pk[0]]["type"] in INTEGER:
        column = quote(pk[0])
        low, high = session.query(f"SELECT MIN({column}),MAX({column}) FROM {full}")[0]
        if low is not None and high is not None and int(high) > int(low):
            span = int(high) - int(low) + 1
            width = max(1, int(span * min(1.0, sample_rows / estimate) / SAMPLE_RANGES))
            starts = [int(low) + int(i * max(0, span - width) / (SAMPLE_RANGES - 1)) for i in range(SAMPLE_RANGES)]
            where = "(" + " OR ".join(f"({column} >= {s} AND {column} < {s + width})" for s in starts) + ")"
            return Source(full, where, "pk_ranges")
    return Source(f"(SELECT * FROM {full} LIMIT {int(sample_rows)}) AS sample", "", "first_rows")


def profile_table(session: Session, config: Config, schema: str, table: str, wanted: list[str] | None, limits: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    quote(schema), quote(table)  # reject unsafe names before any query runs
    meta = find_table(session, config, schema, table)
    if meta["type"] != "table":
        raise ConnectorError("unsupported", "Only base tables are profiled")
    by_name = {c["name"]: c for c in meta["columns"]}
    if wanted:
        unknown = [w for w in wanted if w not in by_name]
        if unknown:
            raise ConnectorError("bad_request", "Unknown column")
        columns = [by_name[w] for w in wanted]
    else:
        columns = list(meta["columns"])
    max_exact = min(config.max_rows_exact, int(limits.get("max_rows_exact", config.max_rows_exact)))
    sample_rows = min(SAMPLE_ROWS, int(limits.get("sample_rows", SAMPLE_ROWS)))
    warnings: list[str] = []
    full = f"{quote(schema)}.{quote(table)}"

    estimate = meta["row_estimate"] or 0
    exact = estimate <= max_exact
    row_count_exact = False
    row_count = estimate
    if exact:
        try:
            row_count = int(session.query(f"SELECT COUNT(*) FROM {full}")[0][0])
            row_count_exact = True
        except ConnectorError:
            exact = False
            warnings.append("An exact row count exceeded the time limit; the table is sampled instead.")
    source = choose_source(session, meta, schema, table, exact, sample_rows)
    sampled_rows = None
    if not exact:
        sampled_rows = int(session.query(f"SELECT COUNT(*) {source.sql()}")[0][0])
        warnings.append(f"Approximate: statistics come from {sampled_rows} sampled rows ({source.basis}).")

    stats: dict[str, dict[str, Any]] = {c["name"]: {} for c in columns}
    for start in range(0, len(columns), GROUP):
        group = columns[start : start + GROUP]
        exprs: list[tuple[str, str, str]] = []
        for column in group:
            exprs += [(column["name"], key, expr) for key, expr in aggregates(column)]
        try:
            row = session.query("SELECT " + ", ".join(e for _, _, e in exprs) + " " + source.sql())[0]
            for (name, key, _), value in zip(exprs, row):
                stats[name][key] = value
        except ConnectorError as error:
            if error.code != "retry_later":
                raise
            warnings.append("Time limit reached for columns: " + ", ".join(c["name"] for c in group))

    result_columns = []
    for column in columns:
        name, kind, raw = column["name"], column["type"], stats[column["name"]]
        non_null = raw.get("non_null")
        total = sampled_rows if not exact else row_count
        entry: dict[str, Any] = {"name": name, "type": kind, "column_type": column["column_type"]}
        if non_null is not None and total is not None:
            entry["non_null"] = int(non_null)
            entry["null_count"] = max(0, int(total) - int(non_null))
            entry["null_rate"] = round(entry["null_count"] / total, 4) if total else 0.0
        distinct = raw.get("distinct")
        if distinct is not None:
            entry["distinct"] = int(distinct)
            entry["distinct_exact"] = exact
        # Patterns and sensitivity first: they decide what may be shown.
        sensitivity = column.get("sensitivity")
        if kind in SHORT_TEXT | LONG_TEXT and non_null:
            condition = f"{quote(name)} IS NOT NULL"
            sample = [r[0] for r in session.query(f"SELECT DISTINCT {quote(name)} {source.sql(condition)} LIMIT {PATTERN_SAMPLE}")]
            counts = Counter(pattern_of(str(v)) for v in sample)
            counts.pop("empty", None)
            if counts:
                entry["patterns"] = dict(counts.most_common())
                dates = [p for p in counts if p.startswith("date")]
                entry["mixed_date_formats"] = len(dates) >= 2
                personal = {k: v for k, v in counts.items() if k in {"email", "phone", "card", "id_number"}}
                if personal and sum(personal.values()) >= 0.5 * sum(counts.values()):
                    sensitivity = sensitivity or max(personal, key=lambda k: personal[k])
        entry["sensitivity"] = sensitivity
        suppressed: list[str] = []
        for key in ("min", "max"):
            if key in raw:
                if sensitivity:
                    suppressed.append(key)
                else:
                    value = plain(raw[key])
                    entry[key] = value[:60] if isinstance(value, str) else value
        for key in ("avg", "stddev"):
            if raw.get(key) is not None:
                entry[key] = float(raw[key])
        if raw.get("len_max") is not None:
            entry["length"] = {"min": int(raw["len_min"]), "max": int(raw["len_max"]), "avg": round(float(raw["len_avg"]), 1)}
        # Duplicate-looking identifiers: almost unique but not enforced.
        unique_indexed = any(ix["unique"] and ix["columns"] == [name] for ix in meta["indexes"])
        if distinct is not None and non_null and not unique_indexed and kind not in LONG_TEXT:
            ratio = int(distinct) / int(non_null)
            entry["likely_identifier"] = ratio >= 0.9 and int(non_null) >= 20
            entry["duplicates"] = int(non_null) - int(distinct) if entry["likely_identifier"] else None
        wants_top = (
            distinct is not None
            and 1 < int(distinct) <= TOP_VALUE_MAX_DISTINCT
            and (kind in SHORT_TEXT or kind in INTEGER)
        )
        if wants_top:
            if sensitivity:
                suppressed.append("top_values")
            else:
                column_sql = quote(name)
                rows = session.query(
                    f"SELECT {column_sql}, COUNT(*) AS n {source.sql(column_sql + ' IS NOT NULL')} "
                    f"GROUP BY {column_sql} ORDER BY n DESC, {column_sql} LIMIT {TOP_VALUES}"
                )
                entry["top_values"] = [{"value": mask_cell(plain(v), None), "count": int(n)} for v, n in rows]
                entry["top_values_complete"] = int(distinct) <= TOP_VALUES
        if suppressed:
            entry["suppressed"] = suppressed
        result_columns.append(entry)

    foreign_keys = []
    for fk in meta["foreign_keys"]:
        item: dict[str, Any] = {"name": fk["name"], "columns": fk["columns"], "references": fk["references"], "orphans": None}
        if not exact:
            item["skipped"] = "table too large for an exact check"
        else:
            ref = fk["references"]
            on = " AND ".join(f"c.{quote(a)} = p.{quote(b)}" for a, b in zip(fk["columns"], ref["columns"]))
            present = " AND ".join(f"c.{quote(a)} IS NOT NULL" for a in fk["columns"])
            try:
                item["orphans"] = int(
                    session.query(
                        f"SELECT COUNT(*) FROM {full} c LEFT JOIN {quote(ref['schema'])}.{quote(ref['table'])} p "
                        f"ON {on} WHERE {present} AND p.{quote(ref['columns'][0])} IS NULL"
                    )[0][0]
                )
            except ConnectorError as error:
                item["skipped"] = "not checkable: " + error.code
        foreign_keys.append(item)

    return {
        "schema": schema,
        "table": table,
        "exact": exact,
        "row_count": row_count,
        "row_count_exact": row_count_exact,
        "sampled_rows": sampled_rows,
        "sample_basis": source.basis,
        "columns": result_columns,
        "foreign_keys": foreign_keys,
        "has_primary_key": bool(meta["primary_key"]),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "warnings": warnings,
    }


def sample_table(session: Session, config: Config, schema: str, table: str, n: int) -> dict[str, Any]:
    """A few rows, with sensitive columns and personal-looking values masked."""
    quote(schema), quote(table)
    meta = find_table(session, config, schema, table)
    columns = [c for c in meta["columns"] if c["type"] not in {"geometry", "point", "linestring", "polygon"}]
    if not columns:
        raise ConnectorError("unsupported", "Nothing to sample")
    names = ", ".join(quote(c["name"]) for c in columns)
    rows = session.query(f"SELECT {names} FROM {quote(schema)}.{quote(table)} LIMIT {max(1, min(20, int(n)))}")
    kinds = [c.get("sensitivity") for c in columns]
    return {
        "schema": schema,
        "table": table,
        "columns": [c["name"] for c in columns],
        "rows": [[mask_cell(v, k) for v, k in zip(row, kinds)] for row in rows],
        "masked_columns": [c["name"] for c, k in zip(columns, kinds) if k],
    }


__all__ = ["profile_table", "sample_table", "classify_name"]
