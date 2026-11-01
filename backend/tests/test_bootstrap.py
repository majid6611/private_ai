"""Integration checks against the actual Compose PostgreSQL service."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.database import make_engine
from app.main import create_app


def test_health_and_default_closed_routes() -> None:
    with TestClient(create_app()) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 200
        assert client.get("/api/v1/documents").status_code == 401
        assert client.get("/openapi.json").status_code == 404


def test_application_role_cannot_change_schema_or_create_roles() -> None:
    engine = make_engine()
    try:
        for statement in (
            "CREATE TABLE workspace.forbidden (id integer)",
            "CREATE ROLE forbidden_role",
            "UPDATE alembic_version SET version_num='tampered'",
            "DELETE FROM installation",
        ):
            with engine.connect() as connection:
                with pytest.raises(DBAPIError):
                    connection.execute(text(statement))
                connection.rollback()
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM installation")).scalar() == 1
    finally:
        engine.dispose()


def test_database_failure_is_generic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_HOST", "127.0.0.1")
    with TestClient(create_app()) as client:
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.json() == {"status": "unavailable"}
        assert client.get("/health/live").status_code == 200


def test_readiness_rejects_an_unmigrated_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_NAME", "postgres")
    with TestClient(create_app()) as client:
        assert client.get("/health/ready").status_code == 503
