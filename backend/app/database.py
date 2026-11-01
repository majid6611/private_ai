import os
from pathlib import Path

from sqlalchemy import URL, Engine, create_engine


def make_engine() -> Engine:
    password = Path(os.environ["DB_PASSWORD_FILE"]).read_text().strip()
    if not password:
        raise ValueError("Database password file is empty")
    url = URL.create(
        "postgresql+psycopg",
        username=os.environ.get("DB_USER", "workspace_app"),
        password=password,
        host=os.environ.get("DB_HOST", "db"),
        database=os.environ.get("DB_NAME", "workspace"),
    )
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=3,
        max_overflow=2,
        connect_args={
            "connect_timeout": 3,
            "options": "-csearch_path=workspace -cstatement_timeout=3000",
        },
        hide_parameters=True,
    )
