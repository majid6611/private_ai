import os
import time

import pymysql
import pytest

from fixtures.mysql import generate
from mysql_connector.config import Config

HOST = os.environ.get("FIXTURE_HOST", "mysql-fixture")
PASSWORDS = {"reader": "reader-fixture-pw", "writer": "writer-fixture-pw", "power": "power-fixture-pw", "loader": "loader-fixture-pw"}


def config(user: str = "reader", **overrides: object) -> Config:
    values: dict[str, object] = {
        "host": HOST, "port": 3306, "user": user, "password": PASSWORDS[user], "tls": "required",
        "tls_verify": False, "tls_ca": None, "schemas": ("shop",), "max_seconds": 30, "max_rows_exact": 5_000_000,
    }
    values.update(overrides)
    return Config(**values)  # type: ignore[arg-type]


@pytest.fixture(scope="session")
def data() -> generate.Fixture:
    fixture = generate.build()
    last: Exception | None = None
    for _ in range(40):
        try:
            connection = pymysql.connect(host=HOST, user="loader", password=PASSWORDS["loader"], database="shop",
                                         charset="utf8mb4", ssl={"check_hostname": False}, autocommit=False)
            break
        except pymysql.MySQLError as error:  # still starting
            last = error
            time.sleep(2)
    else:
        raise RuntimeError("fixture database did not come up") from last
    generate.load(connection, fixture)
    connection.close()
    return fixture
