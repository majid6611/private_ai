"""Read the structure of the visible schemas from information_schema."""

from typing import Any

from connector_sdk import ConnectorError

from .config import Config
from .db import Session
from .sensitive import classify_name

SYSTEM_SCHEMAS = {"mysql", "sys", "information_schema", "performance_schema"}
MAX_TABLES = 5000


def visible_schemas(session: Session, config: Config) -> list[str]:
    names = [
        str(r[0])
        for r in session.query("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA ORDER BY SCHEMA_NAME")
        if str(r[0]).lower() not in SYSTEM_SCHEMAS
    ]
    return [n for n in names if not config.schemas or n in config.schemas]


def read_schema(
    session: Session, config: Config, schemas: list[str] | None = None, table: str | None = None
) -> dict[str, Any]:
    allowed = visible_schemas(session, config)
    wanted = [s for s in (schemas or allowed) if s in allowed]
    if not wanted:
        raise ConnectorError("not_found", "No such schema")
    marks = ",".join(["%s"] * len(wanted))
    extra = " AND TABLE_NAME = %s" if table else ""
    args = tuple(wanted) + ((table,) if table else ())

    tables = session.query(
        "SELECT TABLE_SCHEMA,TABLE_NAME,TABLE_TYPE,ENGINE,TABLE_ROWS,DATA_LENGTH,INDEX_LENGTH,"
        "CREATE_TIME,UPDATE_TIME,TABLE_COMMENT FROM information_schema.TABLES "
        f"WHERE TABLE_SCHEMA IN ({marks}){extra} ORDER BY TABLE_SCHEMA,TABLE_NAME",  # noqa: S608
        args,
    )
    columns = session.query(
        "SELECT TABLE_SCHEMA,TABLE_NAME,ORDINAL_POSITION,COLUMN_NAME,DATA_TYPE,COLUMN_TYPE,IS_NULLABLE,"
        "COLUMN_KEY,EXTRA,COLUMN_COMMENT,CHARACTER_MAXIMUM_LENGTH FROM information_schema.COLUMNS "
        f"WHERE TABLE_SCHEMA IN ({marks}){extra} ORDER BY TABLE_SCHEMA,TABLE_NAME,ORDINAL_POSITION",  # noqa: S608
        args,
    )
    indexes = session.query(
        "SELECT TABLE_SCHEMA,TABLE_NAME,INDEX_NAME,NON_UNIQUE,SEQ_IN_INDEX,COLUMN_NAME "
        f"FROM information_schema.STATISTICS WHERE TABLE_SCHEMA IN ({marks}){extra} "  # noqa: S608
        "ORDER BY TABLE_SCHEMA,TABLE_NAME,INDEX_NAME,SEQ_IN_INDEX",
        args,
    )
    keys = session.query(
        "SELECT k.TABLE_SCHEMA,k.TABLE_NAME,k.CONSTRAINT_NAME,k.ORDINAL_POSITION,k.COLUMN_NAME,"
        "k.REFERENCED_TABLE_SCHEMA,k.REFERENCED_TABLE_NAME,k.REFERENCED_COLUMN_NAME,"
        "r.UPDATE_RULE,r.DELETE_RULE FROM information_schema.KEY_COLUMN_USAGE k "
        "JOIN information_schema.REFERENTIAL_CONSTRAINTS r ON r.CONSTRAINT_SCHEMA=k.CONSTRAINT_SCHEMA "
        "AND r.CONSTRAINT_NAME=k.CONSTRAINT_NAME AND r.TABLE_NAME=k.TABLE_NAME "
        f"WHERE k.TABLE_SCHEMA IN ({marks}) AND k.REFERENCED_TABLE_NAME IS NOT NULL"  # noqa: S608
        + (" AND k.TABLE_NAME = %s" if table else "")
        + " ORDER BY k.TABLE_SCHEMA,k.TABLE_NAME,k.CONSTRAINT_NAME,k.ORDINAL_POSITION",
        args,
    )

    by_schema: dict[str, dict[str, dict[str, Any]]] = {}
    truncated = len(tables) > MAX_TABLES
    for row in tables[:MAX_TABLES]:
        schema, name, kind, engine, rows, data, index, created, updated, comment = row
        by_schema.setdefault(str(schema), {})[str(name)] = {
            "name": str(name),
            "type": "view" if str(kind) == "VIEW" else "table",
            "engine": engine,
            "row_estimate": int(rows) if rows is not None else None,
            "data_bytes": int(data) if data is not None else None,
            "index_bytes": int(index) if index is not None else None,
            "created": created,
            "updated": updated,
            "comment": comment or "",
            "columns": [],
            "primary_key": [],
            "indexes": [],
            "foreign_keys": [],
        }
    for schema, name, pos, col, dtype, ctype, nullable, key, extra_flags, comment, length in columns:
        target = by_schema.get(str(schema), {}).get(str(name))
        if target is not None:
            target["columns"].append(
                {
                    "name": str(col),
                    "position": int(pos),
                    "type": str(dtype).lower(),
                    "column_type": str(ctype),
                    "nullable": str(nullable) == "YES",
                    "key": key or "",
                    "extra": extra_flags or "",
                    "comment": comment or "",
                    "max_length": int(length) if length is not None else None,
                    "sensitivity": classify_name(str(col)),
                }
            )
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for schema, name, index_name, non_unique, _seq, col in indexes:
        target = by_schema.get(str(schema), {}).get(str(name))
        if target is None:
            continue
        entry = grouped.setdefault(
            (str(schema), str(name), str(index_name)),
            {"name": str(index_name), "unique": int(non_unique) == 0, "columns": []},
        )
        entry["columns"].append(str(col))
        if entry not in target["indexes"]:
            target["indexes"].append(entry)
        if str(index_name) == "PRIMARY" and str(col) not in target["primary_key"]:
            target["primary_key"].append(str(col))
    fks: dict[tuple[str, str, str], dict[str, Any]] = {}
    for schema, name, cname, _o, col, rschema, rtable, rcol, on_update, on_delete in keys:
        target = by_schema.get(str(schema), {}).get(str(name))
        if target is None:
            continue
        entry = fks.get((str(schema), str(name), str(cname)))
        if entry is None:
            entry = {
                "name": str(cname),
                "columns": [],
                "references": {"schema": str(rschema), "table": str(rtable), "columns": []},
                "on_update": on_update,
                "on_delete": on_delete,
            }
            fks[(str(schema), str(name), str(cname))] = entry
            target["foreign_keys"].append(entry)
        entry["columns"].append(str(col))
        entry["references"]["columns"].append(str(rcol))
    return {
        "schemas": [
            {"name": schema, "tables": list(tables_.values())} for schema, tables_ in by_schema.items()
        ],
        "truncated": truncated,
    }


def find_table(session: Session, config: Config, schema: str, table: str) -> dict[str, Any]:
    found = read_schema(session, config, [schema], table)["schemas"]
    for entry in found:
        for candidate in entry["tables"]:
            if candidate["name"] == table:
                return dict(candidate)
    raise ConnectorError("not_found", "No such table")
