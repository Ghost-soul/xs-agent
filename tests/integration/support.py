import os
from collections.abc import Mapping
from urllib.parse import urlparse

import pytest

DATABASE_URL = os.getenv("NOVEL_WRITER_TEST_DATABASE_URL")
ZERO_SKIP_GATE_ENV = "NOVEL_WRITER_INTEGRATION_ZERO_SKIP"
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DATABASE_URL, reason="NOVEL_WRITER_TEST_DATABASE_URL is not set"),
]


def zero_skip_gate_enabled(environment: Mapping[str, str] | None = None) -> bool:
    source = os.environ if environment is None else environment
    return source.get(ZERO_SKIP_GATE_ENV) == "1"


def database_url_error(database_url: str | None) -> str | None:
    if not database_url:
        return "NOVEL_WRITER_TEST_DATABASE_URL is not set"
    database_name = urlparse(database_url).path.removeprefix("/")
    if not database_name.endswith("_test"):
        return "Integration tests require a database whose name ends with '_test'."
    return None


def headers(key: str | None = None) -> dict[str, str]:
    result = {
        "Authorization": "Bearer integration-token",
        "Origin": "http://127.0.0.1:5173",
        "X-CSRF-Token": "integration-token",
    }
    if key is not None:
        result["Idempotency-Key"] = key
    return result
