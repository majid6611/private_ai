from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import Connection, Engine, text

from app.security import digest_token


@dataclass(frozen=True)
class Actor:
    id: UUID
    username: str
    is_admin: bool
    token_hash: str


def audit(connection: Connection, actor: UUID | None, action: str, target: UUID | None) -> None:
    connection.execute(
        text("""INSERT INTO security_events(id, actor_id, action, target_id)
                              VALUES (:id, :actor, :action, :target)"""),
        {"id": uuid4(), "actor": actor, "action": action, "target": target},
    )


@dataclass(frozen=True)
class Access:
    transaction: Callable[[], Iterator[Connection]]
    actor: Callable[..., Actor]
    admin: Callable[..., Actor]


def make_access(engine: Engine) -> Access:
    bearer = HTTPBearer(auto_error=False)

    def transaction() -> Iterator[Connection]:
        with engine.begin() as connection:
            yield connection

    DB = Annotated[Connection, Depends(transaction, scope="function")]

    def actor(
        connection: DB,
        credential: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> Actor:
        if credential is None or len(credential.credentials) > 128:
            raise HTTPException(
                401, "Authentication required", headers={"WWW-Authenticate": "Bearer"}
            )
        token_hash = digest_token(credential.credentials)
        row = (
            connection.execute(
                text("""
            SELECT u.id, u.username, u.is_admin FROM users u
            JOIN sessions s ON s.user_id = u.id
            WHERE s.token_hash = :token AND s.expires_at > now() AND u.enabled
            FOR SHARE OF u
        """),
                {"token": token_hash},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise HTTPException(
                401, "Authentication required", headers={"WWW-Authenticate": "Bearer"}
            )
        return Actor(row["id"], row["username"], row["is_admin"], token_hash)

    Human = Annotated[Actor, Depends(actor, scope="function")]

    def admin(human: Human) -> Actor:
        if not human.is_admin:
            raise HTTPException(403, "Administrator permission required")
        return human

    return Access(transaction, actor, admin)
