"""Connector configuration, read from the environment. Secrets come from mounted files."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    user: str
    password: str
    tls: str  # "required" | "disabled" (disabled only for local fixtures)
    tls_verify: bool
    tls_ca: str | None
    schemas: tuple[str, ...]  # allow-list; empty means every schema the account can see
    max_seconds: int  # per-statement time limit
    max_rows_exact: int  # above this a table is profiled by sampling


def from_env() -> Config:
    password_file = os.environ.get("MYSQL_PASSWORD_FILE")
    password = Path(password_file).read_text().strip() if password_file else ""
    return Config(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        user=os.environ["MYSQL_USER"],
        password=password,
        tls=os.environ.get("MYSQL_TLS", "required"),
        tls_verify=os.environ.get("MYSQL_TLS_VERIFY", "true").lower() == "true",
        tls_ca=os.environ.get("MYSQL_TLS_CA_FILE") or None,
        schemas=tuple(s for s in os.environ.get("MYSQL_SCHEMAS", "").split(",") if s),
        max_seconds=min(120, max(1, int(os.environ.get("MYSQL_MAX_SECONDS", "30")))),
        max_rows_exact=int(os.environ.get("MYSQL_MAX_ROWS_EXACT", "5000000")),
    )
