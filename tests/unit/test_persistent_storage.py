import json
import os
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest

from novel_writer.core import credentials
from novel_writer.core.credentials import (
    CredentialError,
    FileCredentialStore,
    create_credential_store,
)
from novel_writer.generation.content import fingerprint
from novel_writer.generation.response_journal import ResponseJournal
from novel_writer.services.errors import WorkflowError


def test_auto_non_windows_uses_persistent_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(credentials, "sys", SimpleNamespace(platform="linux"))
    first = create_credential_store("auto", tmp_path / "credentials")
    assert isinstance(first, FileCredentialStore)
    first.set_api_key("fixture", "synthetic-key")
    assert (
        create_credential_store("auto", tmp_path / "credentials").get_api_key("fixture")
        == "synthetic-key"
    )
    if os.name == "posix":
        assert (tmp_path / "credentials").stat().st_mode & 0o777 == 0o700
        assert (tmp_path / "credentials/fixture.secret").stat().st_mode & 0o777 == 0o600


def test_keys_are_independent_atomic_and_errors_do_not_expose_secret(tmp_path, monkeypatch):
    store = FileCredentialStore(tmp_path / "keys")
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(
            executor.map(lambda n: store.set_api_key(f"profile{n}", f"test-secret-{n}"), range(32))
        )
    assert all(store.get_api_key(f"profile{n}") == f"test-secret-{n}" for n in range(32))

    def fail(*args):
        raise OSError("filesystem unavailable")

    monkeypatch.setattr(credentials, "atomic_private_write", fail)
    with pytest.raises(CredentialError) as failure:
        store.set_api_key("profile0", "replacement-secret")
    assert "replacement-secret" not in str(failure.value)
    assert store.get_api_key("profile0") == "test-secret-0"
    store.delete_api_key("profile1")
    store.delete_api_key("profile1")
    assert store.get_api_key("profile1") is None
    assert store.get_api_key("profile2") == "test-secret-2"


@pytest.mark.parametrize("profile", ["../outside", "C:/secret", "a/b", "", ".."])
def test_credential_paths_cannot_escape_root(tmp_path, profile):
    with pytest.raises(CredentialError):
        FileCredentialStore(tmp_path).set_api_key(profile, "fake")


def test_response_journal_survives_recreation_validates_content_and_isolates_databases(tmp_path):
    url = "postgresql+psycopg://user:password@localhost:5432/example_test"
    first = ResponseJournal(tmp_path, url)
    raw = {"text": "original", "usage": {"input_tokens": 12, "output_tokens": 15}}
    raw["sha256"] = fingerprint(raw)
    batch, call = uuid4(), uuid4()
    receipt = first.save(batch, call, "request-digest", raw)
    second = ResponseJournal(tmp_path, url)
    assert second.read(second.pending()[0]) == receipt
    assert second.save(batch, call, "request-digest", raw) == receipt
    assert not ResponseJournal(tmp_path, url.replace("example_test", "other_test")).pending()
    with pytest.raises(WorkflowError):
        second.save(batch, call, "wrong-request", raw)
    changed = {**receipt, "response": {**raw, "text": "changed"}}
    second.pending()[0].write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(WorkflowError):
        second.read(second.pending()[0])
