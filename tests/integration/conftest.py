from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text

from tests.integration.support import (
    DATABASE_URL,
    database_url_error,
    zero_skip_gate_enabled,
)

_ZERO_SKIP_GATE_ENABLED = zero_skip_gate_enabled()
_SKIPPED_REPORTS: list[str] = []

_database_url_error = database_url_error(DATABASE_URL)
if DATABASE_URL and _database_url_error:
    raise pytest.UsageError(_database_url_error)
if _ZERO_SKIP_GATE_ENABLED and _database_url_error:
    raise pytest.UsageError(f"Integration zero-skip gate: {_database_url_error}")


def _record_skipped_report(report: pytest.CollectReport | pytest.TestReport) -> None:
    if not _ZERO_SKIP_GATE_ENABLED or not report.skipped:
        return
    longrepr = report.longrepr
    if isinstance(longrepr, tuple) and len(longrepr) >= 3:
        reason = str(longrepr[2])
    else:
        reason = str(longrepr)
    _SKIPPED_REPORTS.append(f"{report.nodeid}: {reason}")


def pytest_collectreport(report: pytest.CollectReport) -> None:
    _record_skipped_report(report)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    _record_skipped_report(report)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int | pytest.ExitCode) -> None:
    if not _ZERO_SKIP_GATE_ENABLED or not _SKIPPED_REPORTS:
        return
    terminal_reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if terminal_reporter is not None:
        terminal_reporter.write_sep("=", "integration zero-skip gate failed")
        for skipped_report in _SKIPPED_REPORTS:
            terminal_reporter.write_line(skipped_report)
    if exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


@pytest.fixture
def clean_test_database() -> Iterator[None]:
    assert DATABASE_URL is not None
    if error := database_url_error(DATABASE_URL):
        pytest.fail(error)
    engine = create_engine(DATABASE_URL)

    def truncate_application_tables() -> None:
        with engine.begin() as connection:
            tables = list(
                connection.execute(
                    text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
                    )
                ).scalars()
            )
            if tables:
                quoted = ", ".join(f'"{name}"' for name in tables)
                connection.execute(text(f"TRUNCATE TABLE {quoted} CASCADE"))

    try:
        truncate_application_tables()
        yield
    finally:
        truncate_application_tables()
        engine.dispose()
