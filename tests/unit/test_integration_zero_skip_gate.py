from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tests.integration.support import (
    ZERO_SKIP_GATE_ENV,
    database_url_error,
    zero_skip_gate_enabled,
)


def test_zero_skip_gate_is_opt_in() -> None:
    assert zero_skip_gate_enabled({}) is False
    assert zero_skip_gate_enabled({ZERO_SKIP_GATE_ENV: "0"}) is False
    assert zero_skip_gate_enabled({ZERO_SKIP_GATE_ENV: "1"}) is True


def test_test_database_url_validation_fails_closed() -> None:
    assert database_url_error(None) == "NOVEL_WRITER_TEST_DATABASE_URL is not set"
    assert database_url_error("postgresql+psycopg://localhost/novel_writer") is not None
    assert database_url_error("postgresql+psycopg://localhost/novel_writer_test") is None


def _run_probe(*, strict: bool, database_url: str | None) -> subprocess.CompletedProcess[str]:
    project_root = Path(__file__).resolve().parents[2]
    probe = project_root / "tests" / "fixtures" / "pytest_skip_probe.py"
    environment = os.environ.copy()
    environment.pop(ZERO_SKIP_GATE_ENV, None)
    environment.pop("NOVEL_WRITER_TEST_DATABASE_URL", None)
    if strict:
        environment[ZERO_SKIP_GATE_ENV] = "1"
    if database_url is not None:
        environment["NOVEL_WRITER_TEST_DATABASE_URL"] = database_url
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "tests.integration.conftest",
            str(probe),
        ],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_ordinary_pytest_skip_is_unchanged() -> None:
    result = _run_probe(
        strict=False,
        database_url="postgresql+psycopg://localhost/novel_writer_test",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 skipped" in result.stdout


def test_integration_gate_rejects_any_skip() -> None:
    result = _run_probe(
        strict=True,
        database_url="postgresql+psycopg://localhost/novel_writer_test",
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "integration zero-skip gate failed" in result.stdout
    assert "probe skip" in result.stdout


def test_integration_gate_rejects_missing_database_url() -> None:
    result = _run_probe(strict=True, database_url=None)

    assert result.returncode != 0
    assert "Integration zero-skip gate" in result.stderr
    assert "NOVEL_WRITER_TEST_DATABASE_URL is not set" in result.stderr
