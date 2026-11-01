from collections.abc import Iterator
from dataclasses import dataclass
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.database import make_engine
from app.main import create_app
from app.security import hash_password

PASSWORD = "A long test password! 123"


@dataclass
class Workspace:
    client: TestClient
    engine: Engine
    admin_headers: dict[str, str]
    user_headers: dict[str, str]
    user_id: str
    username: str


def login(client: TestClient, username: str) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def workspace() -> Iterator[Workspace]:
    engine = make_engine()
    # Safety guard: integration fixtures must never write to the normal application DB.
    assert engine.url.database == "workspace_test"
    admin_name = "admin_" + uuid4().hex[:12]
    username = "user_" + uuid4().hex[:12]
    with engine.begin() as connection:
        connection.execute(
            text("""INSERT INTO users(id, username, password_hash, is_admin)
                                  VALUES (:id, :name, :hash, true)"""),
            {"id": uuid4(), "name": admin_name, "hash": hash_password(PASSWORD)},
        )
    try:
        with TestClient(create_app()) as client:
            admin = login(client, admin_name)
            created = client.post(
                "/api/v1/users", headers=admin, json={"username": username, "password": PASSWORD}
            )
            assert created.status_code == 201, created.text
            yield Workspace(
                client, engine, admin, login(client, username), created.json()["id"], username
            )
    finally:
        engine.dispose()
