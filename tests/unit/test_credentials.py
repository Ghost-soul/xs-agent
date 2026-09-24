import ctypes
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import novel_writer.core.credentials as credentials_module
from novel_writer.api.routes.provider_profiles import (
    SaveCredentialRequest,
    save_provider_credential,
)
from novel_writer.core.credentials import (
    CredentialError,
    MemoryCredentialStore,
    WindowsCredentialStore,
)
from novel_writer.services.provider_profiles import ProviderProfileStore


def _fake_store(
    monkeypatch: pytest.MonkeyPatch,
    results: list[bool],
    errors: list[int],
) -> tuple[WindowsCredentialStore, list[int]]:
    store = object.__new__(WindowsCredentialStore)
    persists: list[int] = []

    def write(pointer: object, flags: int) -> bool:
        del flags
        credential = ctypes.cast(
            pointer,
            ctypes.POINTER(credentials_module._CredentialW),
        ).contents
        persists.append(credential.Persist)
        return results.pop(0)

    store._cred_write = write  # type: ignore[assignment]
    monkeypatch.setattr(credentials_module.ctypes, "get_last_error", lambda: errors.pop(0))
    return store, persists


def test_windows_store_falls_back_to_session_persistence_for_missing_logon_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, persists = _fake_store(monkeypatch, [False, True], [1312])

    store.set_api_key("deepseek", "test-key")

    assert persists == [2, 1]


def test_windows_store_reports_second_write_failure_without_retrying_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, persists = _fake_store(monkeypatch, [False, False], [1312, 1312])

    with pytest.raises(CredentialError, match="1312"):
        store.set_api_key("deepseek", "test-key")

    assert persists == [2, 1]


@pytest.mark.parametrize("success,error", [(True, 0), (False, 1168), (False, 5)])
def test_windows_delete_is_targeted_and_missing_is_idempotent(monkeypatch, success, error):
    store = object.__new__(WindowsCredentialStore)
    calls = []
    store._cred_delete = lambda *args: calls.append(args) or success
    monkeypatch.setattr(credentials_module.ctypes, "get_last_error", lambda: error)
    if error == 5:
        with pytest.raises(CredentialError, match="Windows error 5"):
            store.delete_api_key("example")
    else:
        store.delete_api_key("example")
    assert calls == [("novel-writer/example", 1, 0)]


def test_memory_delete_preserves_other_credentials():
    store = MemoryCredentialStore({"example": "fake-key", "other": "other-key"})
    store.delete_api_key("example")
    store.delete_api_key("example")
    assert store.get_api_key("example") is None
    assert store.get_api_key("other") == "other-key"


@pytest.mark.asyncio
async def test_save_credential_maps_unavailable_secure_store_to_service_unavailable(
    tmp_path,
) -> None:
    class FailingCredentialStore:
        def set_api_key(self, provider: str, api_key: str) -> None:
            raise CredentialError("credential manager unavailable")

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                provider_profile_store=ProviderProfileStore(tmp_path / "profiles.json"),
                credential_store=FailingCredentialStore(),
            )
        )
    )

    with pytest.raises(HTTPException) as error:
        await save_provider_credential(
            "deepseek",
            SaveCredentialRequest(api_key="test-key", confirmed=True),
            request,
        )

    assert error.value.status_code == 503
    assert "安全凭据存储" in str(error.value.detail)
    assert "test-key" not in str(error.value.detail)


@pytest.mark.asyncio
async def test_save_credential_rejects_non_ascii_secret_before_writing(tmp_path) -> None:
    class RecordingCredentialStore:
        def __init__(self) -> None:
            self.writes = 0

        def set_api_key(self, provider: str, api_key: str) -> None:
            del provider, api_key
            self.writes += 1

    credentials = RecordingCredentialStore()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                provider_profile_store=ProviderProfileStore(tmp_path / "profiles.json"),
                credential_store=credentials,
            )
        )
    )

    with pytest.raises(HTTPException) as error:
        await save_provider_credential(
            "deepseek",
            SaveCredentialRequest(api_key="sk-test中文", confirmed=True),
            request,
        )

    assert error.value.status_code == 422
    assert credentials.writes == 0
    assert "sk-test" not in str(error.value.detail)
