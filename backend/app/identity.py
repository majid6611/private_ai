import secrets
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import Connection, Engine, text

from app.access import Access, Actor, audit
from app.security import DUMMY_HASH, digest_token, hash_password, verify_password


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=False)


class Credentials(Input):
    username: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,63}$", max_length=64)
    password: SecretStr = Field(min_length=16, max_length=256)


class NewUser(Credentials):
    pass  # New accounts never gain installation-admin rights through the API.


class NewProject(Input):
    name: str = Field(min_length=1, max_length=200, pattern=r".*\S.*")


class Membership(Input):
    can_edit: bool = False
    can_approve: bool = False
    can_publish: bool = False
    can_history: bool = False


class UserStatus(Input):
    enabled: bool


def identity_router(engine: Engine, access: Access) -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    DB = Annotated[Connection, Depends(access.transaction, scope="function")]
    Human = Annotated[Actor, Depends(access.actor, scope="function")]
    Admin = Annotated[Actor, Depends(access.admin, scope="function")]

    @router.post("/auth/login")
    def login(body: Credentials, response: Response) -> dict[str, str | int]:
        response.headers["Cache-Control"] = "no-store"
        token: str | None = None
        limited = False
        with engine.begin() as connection:
            key = digest_token(body.username)
            connection.execute(
                text("INSERT INTO login_limits(key) VALUES (:k) ON CONFLICT DO NOTHING"), {"k": key}
            )
            limit = (
                connection.execute(
                    text("""SELECT failures,
                window_start < now() - interval '5 minutes' AS reset
                FROM login_limits WHERE key = :k FOR UPDATE"""),
                    {"k": key},
                )
                .mappings()
                .one()
            )
            if limit["reset"]:
                connection.execute(
                    text("""UPDATE login_limits SET failures=0, window_start=now()
                                           WHERE key=:k"""),
                    {"k": key},
                )
            limited = not limit["reset"] and limit["failures"] >= 5
            if not limited:
                row = (
                    connection.execute(
                        text("""SELECT id, password_hash, enabled FROM users
                    WHERE username=:name FOR SHARE"""),
                        {"name": body.username},
                    )
                    .mappings()
                    .first()
                )
                valid = verify_password(
                    body.password.get_secret_value(), row["password_hash"] if row else DUMMY_HASH
                )
                if row and row["enabled"] and valid:
                    token = secrets.token_urlsafe(32)
                    connection.execute(
                        text("""INSERT INTO sessions(token_hash, user_id, expires_at)
                        VALUES (:token, :user, now() + interval '1 hour')"""),
                        {"token": digest_token(token), "user": row["id"]},
                    )
                    connection.execute(
                        text("UPDATE login_limits SET failures=0 WHERE key=:k"), {"k": key}
                    )
                    audit(connection, row["id"], "login_success", row["id"])
                else:
                    connection.execute(
                        text("UPDATE login_limits SET failures=failures+1 WHERE key=:k"), {"k": key}
                    )
                    audit(connection, None, "login_failure", None)
        if limited:
            raise HTTPException(429, "Too many login attempts", headers={"Retry-After": "300"})
        if token is None:
            raise HTTPException(401, "Invalid credentials")
        return {"access_token": token, "token_type": "bearer", "expires_in": 3600}

    @router.get("/auth/me")
    def me(human: Human, response: Response) -> dict[str, str | bool]:
        response.headers["Cache-Control"] = "no-store"
        return {"id": str(human.id), "username": human.username, "is_admin": human.is_admin}

    @router.post("/auth/logout", status_code=204)
    def logout(connection: DB, human: Human) -> None:
        connection.execute(
            text("DELETE FROM sessions WHERE token_hash=:token"), {"token": human.token_hash}
        )
        audit(connection, human.id, "logout", human.id)

    @router.get("/users")
    def list_users(connection: DB, human: Admin) -> list[dict[str, str | bool]]:
        rows = connection.execute(
            text("SELECT id,username,enabled FROM users ORDER BY username LIMIT 200")
        ).mappings()
        return [
            {"id": str(r["id"]), "username": r["username"], "enabled": r["enabled"]} for r in rows
        ]

    @router.post("/users", status_code=201)
    def create_user(body: NewUser, connection: DB, human: Admin) -> dict[str, str]:
        user_id = uuid4()
        stored_hash = hash_password(body.password.get_secret_value())
        created = connection.execute(
            text("""INSERT INTO users(id, username, password_hash)
            VALUES (:id, :name, :hash) ON CONFLICT (username) DO NOTHING RETURNING id"""),
            {"id": user_id, "name": body.username, "hash": stored_hash},
        ).scalar()
        if created is None:
            raise HTTPException(409, "Username unavailable")
        audit(connection, human.id, "user_created", user_id)
        return {"id": str(user_id), "username": body.username}

    @router.patch("/users/{user_id}")
    def set_user_status(
        user_id: UUID, body: UserStatus, connection: DB, human: Admin
    ) -> dict[str, bool]:
        if user_id == human.id:
            raise HTTPException(409, "Cannot change your own account status")
        updated = connection.execute(
            text(
                "UPDATE users SET enabled=:enabled, auth_revision=auth_revision+1 WHERE id=:id RETURNING id"
            ),
            {"enabled": body.enabled, "id": user_id},
        ).scalar()
        if updated is None:
            raise HTTPException(404, "User not found")
        if not body.enabled:
            connection.execute(text("DELETE FROM sessions WHERE user_id=:id"), {"id": user_id})
        audit(connection, human.id, "user_enabled" if body.enabled else "user_disabled", user_id)
        return {"enabled": body.enabled}

    @router.post("/projects", status_code=201)
    def create_project(body: NewProject, connection: DB, human: Admin) -> dict[str, str]:
        project_id = uuid4()
        connection.execute(
            text("INSERT INTO projects(id, name) VALUES (:id, :name)"),
            {"id": project_id, "name": body.name},
        )
        audit(connection, human.id, "project_created", project_id)
        return {"id": str(project_id), "name": body.name}

    @router.get("/projects")
    def projects(connection: DB, human: Human) -> list[dict[str, str]]:
        rows = connection.execute(
            text("""SELECT p.id, p.name FROM projects p
            JOIN memberships m ON m.project_id=p.id WHERE m.user_id=:user ORDER BY p.id"""),
            {"user": human.id},
        ).mappings()
        return [{"id": str(row["id"]), "name": row["name"]} for row in rows]

    @router.get("/projects/{project_id}")
    def project(project_id: UUID, connection: DB, human: Human) -> dict[str, str | bool | int]:
        row = (
            connection.execute(
                text("""SELECT p.id, p.name, p.acl_revision, m.can_edit,
            m.can_approve, m.can_publish, m.can_history FROM projects p
            JOIN memberships m ON m.project_id=p.id
            WHERE p.id=:id AND m.user_id=:user"""),
                {"id": project_id, "user": human.id},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise HTTPException(404, "Project not found")
        return {
            "id": str(row["id"]),
            "name": row["name"],
            "acl_revision": row["acl_revision"],
            "can_edit": row["can_edit"],
            "can_approve": row["can_approve"],
            "can_publish": row["can_publish"],
            "can_history": row["can_history"],
        }

    def lock_project(connection: Connection, project_id: UUID) -> None:
        if (
            connection.execute(
                text("SELECT id FROM projects WHERE id=:id FOR UPDATE"), {"id": project_id}
            ).scalar()
            is None
        ):
            raise HTTPException(404, "Project not found")

    @router.put("/projects/{project_id}/members/{user_id}", status_code=204)
    def membership(
        project_id: UUID, user_id: UUID, body: Membership, connection: DB, human: Admin
    ) -> None:
        lock_project(connection, project_id)
        if (
            connection.execute(
                text("SELECT id FROM users WHERE id=:id AND enabled"), {"id": user_id}
            ).scalar()
            is None
        ):
            raise HTTPException(404, "User not found")
        connection.execute(
            text("""INSERT INTO memberships
            (project_id, user_id, can_edit, can_approve, can_publish, can_history)
            VALUES (:project, :user, :can_edit, :can_approve, :can_publish, :can_history)
            ON CONFLICT (project_id, user_id) DO UPDATE SET
            can_edit=EXCLUDED.can_edit, can_approve=EXCLUDED.can_approve,
            can_publish=EXCLUDED.can_publish, can_history=EXCLUDED.can_history"""),
            {"project": project_id, "user": user_id, **body.model_dump()},
        )
        connection.execute(
            text("UPDATE projects SET acl_revision=acl_revision+1 WHERE id=:id"), {"id": project_id}
        )
        audit(connection, human.id, "membership_updated", project_id)

    @router.delete("/projects/{project_id}/members/{user_id}", status_code=204)
    def revoke_membership(project_id: UUID, user_id: UUID, connection: DB, human: Admin) -> None:
        lock_project(connection, project_id)
        connection.execute(
            text("DELETE FROM memberships WHERE project_id=:p AND user_id=:u"),
            {"p": project_id, "u": user_id},
        )
        connection.execute(
            text("UPDATE projects SET acl_revision=acl_revision+1 WHERE id=:id"), {"id": project_id}
        )
        audit(connection, human.id, "membership_revoked", project_id)

    return router
