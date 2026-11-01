import os

import pytest

from tests.support import workspace  # noqa: F401


@pytest.fixture(scope="session", autouse=True)
def require_disposable_database() -> None:
    if os.environ.get("DB_NAME") != "workspace_test":
        pytest.fail("Use the Compose test profile; tests must not run against the application DB")
