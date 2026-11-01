"""Local-only initial administrator provisioning through an interactive container."""

import getpass
import sys
from uuid import uuid4

from sqlalchemy import Engine, text

from app.access import audit
from app.database import make_engine
from app.identity import Credentials
from app.security import hash_password


def bootstrap_admin(engine: Engine, username: str, password: str) -> str:
    validated = Credentials.model_validate({"username": username, "password": password})
    stored_hash = hash_password(validated.password.get_secret_value())
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(813401)"))
        if connection.execute(text("SELECT id FROM users WHERE is_admin LIMIT 1")).scalar():
            raise ValueError("An administrator already exists; bootstrap refused")
        user_id = uuid4()
        connection.execute(
            text("""INSERT INTO users(id, username, password_hash, is_admin)
            VALUES (:id, :username, :hash, true)"""),
            {"id": user_id, "username": validated.username, "hash": stored_hash},
        )
        audit(connection, user_id, "admin_bootstrapped", user_id)
    return str(user_id)


def main() -> None:
    if not sys.stdin.isatty():
        raise SystemExit("Use an interactive terminal; do not pass passwords in arguments")
    username = input("Administrator username (lowercase): ")
    password = getpass.getpass("Password (16–256 characters): ")
    if password != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords do not match")
    engine = make_engine()
    try:
        bootstrap_admin(engine, username, password)
    except ValueError:
        raise SystemExit(
            "Bootstrap refused: invalid input or an administrator already exists"
        ) from None
    finally:
        engine.dispose()
    print("Administrator created.")


if __name__ == "__main__":
    main()
