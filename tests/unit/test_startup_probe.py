from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import psycopg
import pytest

from scripts import check_database


class _Connection:
    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *args: Any) -> None:
        return None


@pytest.mark.parametrize("port", [54329, 15432])
def test_database_probe_uses_a_bounded_connection_timeout(monkeypatch: Any, port: int) -> None:
    captured: dict[str, Any] = {}

    def connect(database_url: str, *, connect_timeout: int) -> _Connection:
        captured.update(url=database_url, timeout=connect_timeout)
        return _Connection()

    monkeypatch.setattr(
        check_database,
        "get_settings",
        lambda: SimpleNamespace(
            database_url=f"postgresql+psycopg://user:pass@127.0.0.1:{port}/database"
        ),
    )
    monkeypatch.setattr(check_database.psycopg, "connect", connect)

    assert check_database.main() == 0
    assert captured == {
        "url": f"postgresql://user:pass@127.0.0.1:{port}/database",
        "timeout": 2,
    }


def test_database_probe_returns_failure_when_postgres_is_offline(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        check_database,
        "get_settings",
        lambda: SimpleNamespace(database_url="postgresql://offline/database"),
    )
    monkeypatch.setattr(
        check_database.psycopg,
        "connect",
        lambda *args, **kwargs: (_ for _ in ()).throw(psycopg.OperationalError("offline")),
    )

    assert check_database.main() == 1
