from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from novel_writer.api.app import create_app
from novel_writer.core.config import Settings
from novel_writer.core.credentials import CredentialError, MemoryCredentialStore
from novel_writer.services.provider_profiles import ProviderModelOption, ProviderProfile
from tests.integration.support import DATABASE_URL, headers
from tests.integration.support import pytestmark as pytestmark


@pytest.fixture
def management(clean_test_database, tmp_path):
    app = create_app(
        Settings(
            database_url=DATABASE_URL,
            local_token="integration-token",
            _env_file=None,
            provider_profiles_path=tmp_path / "profiles.json",
            content_store_root=tmp_path / "content",
            local_task_worker_enabled=False,
        )
    )
    app.state.credential_store = MemoryCredentialStore({"example": "fake-key", "other": "keep"})
    app.state.provider_profile_store.save(
        ProviderProfile(
            id="example",
            display_name="测试供应商",
            protocol="openai_chat_completions",
            base_url="https://example.test/v1",
            default_model="model-a",
            models=[ProviderModelOption(id="model-a"), ProviderModelOption(id="model-b")],
        )
    )
    app.state.providers.update(app.state.provider_profile_store.build_enabled_providers())
    engine = create_engine(DATABASE_URL)
    try:
        with TestClient(app) as client:
            yield client, engine
    finally:
        engine.dispose()


def current_profile(client, profile_id="example"):
    response = client.get("/api/provider-profiles", headers=headers())
    assert response.status_code == 200, response.text
    return next(item for item in response.json() if item["id"] == profile_id)


def delete(client, profile=None):
    profile = profile or current_profile(client)
    return client.request(
        "DELETE",
        f"/api/provider-profiles/{profile['id']}",
        headers=headers(str(uuid4())),
        json={"confirmed": True, "expected_revision": profile["profile_revision"]},
    )










def test_delete_rejects_concurrent_dispatch_transaction_without_waiting(management):
    client, engine = management
    profile = current_profile(client)
    with engine.begin() as connection:
        connection.execute(text("LOCK TABLE writing_chunk_calls IN ROW EXCLUSIVE MODE"))
        response = delete(client, profile)
    assert response.status_code == 409, response.text
    assert client.app.state.credential_store.get_api_key("example") == "fake-key"


def test_delete_rejects_stale_confirmation_builtin_and_active_probe(management):
    client, _engine = management
    profile = current_profile(client)
    store = client.app.state.provider_profile_store
    original = store.get("example")
    store.save(original.model_copy(update={"base_url": "https://changed.test/v1"}))
    assert delete(client, profile).status_code == 409
    assert delete(client, current_profile(client, "deepseek")).status_code == 409
    with store.probe_operation("example"):
        assert delete(client).status_code == 409
    assert client.app.state.credential_store.get_api_key("example") == "fake-key"


def test_credential_failure_keeps_configuration_and_allows_explicit_retry(management, monkeypatch):
    client, _engine = management
    credentials = client.app.state.credential_store
    with monkeypatch.context() as patch:

        def fail(_profile_id):
            raise CredentialError("fake storage failure")

        patch.setattr(credentials, "delete_api_key", fail)
        assert delete(client).status_code == 503
    assert current_profile(client)["has_api_key"] is True
    assert delete(client).status_code == 200


def test_official_default_persists_without_mutating_runtime_contract(management):
    client, _engine = management
    before = current_profile(client, "deepseek")
    store = client.app.state.provider_profile_store
    runtime_before = store.get("deepseek").model_dump()
    response = client.post(
        "/api/provider-profiles/deepseek/default-model",
        json={"confirmed": True, "model": "deepseek-v4-flash"},
        headers=headers(str(uuid4())),
    )
    assert response.status_code == 200, response.text
    assert response.json()["default_model"] == "deepseek-v4-flash"
    assert response.json()["profile_revision"] == before["profile_revision"]
    assert current_profile(client, "deepseek")["default_model"] == "deepseek-v4-flash"
    assert store.get("deepseek").model_dump() == runtime_before
    for payload in [
        {"confirmed": False, "model": "deepseek-v4-pro"},
        {"confirmed": True, "model": "arbitrary-model"},
    ]:
        assert (
            client.post(
                "/api/provider-profiles/deepseek/default-model",
                json=payload,
                headers=headers(str(uuid4())),
            ).status_code
            == 422
        )
