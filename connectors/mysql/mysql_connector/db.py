"""Guarded access to MySQL: read-only session, statement guard, safe identifiers, typed errors."""

import re
from typing import Any

import pymysql
from pymysql.err import MySQLError, OperationalError

from connector_sdk import ConnectorError

from .config import Config

SELECT_OR_SHOW = re.compile(r"^\s*(select|show)\b", re.I)
FORBIDDEN = re.compile(
    r"\binto\s+(outfile|dumpfile)\b|\bload_file\s*\(|\bsleep\s*\(|\bbenchmark\s*\(|\bget_lock\s*\(", re.I
)
IDENTIFIER = re.compile(r"^[\w$ \-]{1,64}$")


def quote(name: str) -> str:
    """Backtick-quote an identifier after restricting its characters."""
    if not isinstance(name, str) or not IDENTIFIER.fullmatch(name):
        raise ConnectorError("bad_request", "Unsupported identifier")
    return "`" + name.replace("`", "``") + "`"


def safe_error(error: MySQLError) -> ConnectorError:
    """Map a driver error to a typed error without echoing its text (it can carry secrets)."""
    code = error.args[0] if error.args and isinstance(error.args[0], int) else 0
    if code in (1044, 1045, 1142, 1143, 1227, 1370):
        return ConnectorError("forbidden", "The database refused the request")
    if code in (2002, 2003, 2006, 2013, 1040, 1205):
        return ConnectorError("retry_later", "The database is not reachable right now")
    if code == 2026:
        return ConnectorError("forbidden", "The secure connection could not be established or verified")
    if code in (3024, 1317):
        return ConnectorError("retry_later", "The query exceeded the time limit")
    if code == 1146 or code == 1049:
        return ConnectorError("not_found", "No such table or schema")
    return ConnectorError("internal", "Database error")


class Session:
    """One connection. Every statement must be a plain SELECT or SHOW (defence in depth: the
    account should also be read-only, which the `check` call verifies)."""

    def __init__(self, config: Config, max_seconds: int | None = None) -> None:
        self.config = config
        seconds = min(config.max_seconds, max_seconds or config.max_seconds)
        options: dict[str, Any] = {}
        if config.tls == "required":
            options = {
                "ssl_disabled": False,
                "ssl_verify_cert": config.tls_verify,
                "ssl_verify_identity": config.tls_verify,
            }
            if config.tls_ca:
                options["ssl_ca"] = config.tls_ca
            else:
                options["ssl"] = {"check_hostname": config.tls_verify}
        try:
            self.conn = pymysql.connect(
                host=config.host,
                port=config.port,
                user=config.user,
                password=config.password,
                charset="utf8mb4",
                connect_timeout=10,
                read_timeout=seconds + 15,
                write_timeout=10,
                autocommit=True,
                **options,
            )
            with self.conn.cursor() as cursor:
                cursor.execute("SET SESSION TRANSACTION READ ONLY")
                cursor.execute("SET SESSION max_execution_time = %s", (seconds * 1000,))
            if config.tls == "required" and not self.uses_tls():
                self.conn.close()
                raise ConnectorError("forbidden", "TLS is required but the connection is not encrypted")
        except MySQLError as error:
            raise safe_error(error) from None

    def uses_tls(self) -> bool:
        rows = self.query("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
        return bool(rows and rows[0][1])

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        if not SELECT_OR_SHOW.match(sql) or FORBIDDEN.search(sql) or ";" in sql:
            raise ConnectorError("forbidden", "Only plain read-only statements are allowed")
        try:
            with self.conn.cursor() as cursor:
                cursor.execute(sql, params)
                return list(cursor.fetchall())
        except MySQLError as error:
            raise safe_error(error) from None

    def close(self) -> None:
        try:
            self.conn.close()
        except MySQLError:
            pass


