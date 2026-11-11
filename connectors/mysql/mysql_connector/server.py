"""Protocol v1 calls for the MySQL connector: check, schema, profile, sample."""

import time
from typing import Any

from connector_sdk import ConnectorError, Handler

from .config import Config
from .db import Session
from .grants import check_grants
from .profile import profile_table, sample_table
from .schema import read_schema, visible_schemas

_verified: dict[str, float] = {}


def manifest(config: Config) -> dict[str, Any]:
    return {
        "id": "core.mysql",
        "version": "0.1.0",
        "kinds": ["records"],
        "capabilities": ["check", "schema", "profile", "sample"],
        "network": {"mode": "egress", "hosts": [f"{config.host}:{config.port}"]},
        "secrets": ["password"],
        "read_only": True,
    }


def require_read_only(session: Session, config: Config) -> None:
    """Refuse to do anything with an account that could write (re-verified at least every minute)."""
    key = f"{config.host}:{config.port}:{config.user}"
    if time.monotonic() - _verified.get(key, -1e9) < 60:
        return
    violations, _ = check_grants(session)
    if violations:
        raise ConnectorError("forbidden", "The database account is not read-only: " + "; ".join(violations[:5]))
    _verified[key] = time.monotonic()


def text(body: dict[str, Any], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value:
        raise ConnectorError("bad_request", f"{key} is required")
    return value


def routes(config: Config) -> dict[str, Handler]:
    def check(body: dict[str, Any]) -> dict[str, Any]:
        session = Session(config)
        try:
            violations, scopes = check_grants(session)
            tls = session.uses_tls()
            warnings = []
            if config.tls != "required":
                warnings.append("TLS is disabled for this connection")
            elif not config.tls_verify:
                warnings.append("The server certificate is not verified")
            return {
                "ok": not violations and (tls or config.tls != "required"),
                "read_only": not violations,
                "violations": violations,
                "grants": scopes,
                "tls": tls,
                "server_version": str(session.query("SELECT VERSION()")[0][0]),
                "schemas": visible_schemas(session, config),
                "warnings": warnings,
            }
        finally:
            session.close()

    def schema(body: dict[str, Any]) -> dict[str, Any]:
        session = Session(config)
        try:
            require_read_only(session, config)
            names = body.get("schemas")
            if names is not None and (not isinstance(names, list) or not all(isinstance(n, str) for n in names)):
                raise ConnectorError("bad_request", "schemas must be a list of names")
            return read_schema(session, config, names)
        finally:
            session.close()

    def profile(body: dict[str, Any]) -> dict[str, Any]:
        limits = body.get("limits") or {}
        columns = body.get("columns")
        if not isinstance(limits, dict) or (columns is not None and not isinstance(columns, list)):
            raise ConnectorError("bad_request", "Invalid limits or columns")
        session = Session(config, int(limits.get("max_seconds", config.max_seconds)))
        try:
            require_read_only(session, config)
            return profile_table(session, config, text(body, "schema"), text(body, "table"), columns, limits)
        finally:
            session.close()

    def sample(body: dict[str, Any]) -> dict[str, Any]:
        session = Session(config)
        try:
            require_read_only(session, config)
            return sample_table(session, config, text(body, "schema"), text(body, "table"), int(body.get("n", 5)))
        finally:
            session.close()

    return {"/v1/check": check, "/v1/schema": schema, "/v1/profile": profile, "/v1/sample": sample}
