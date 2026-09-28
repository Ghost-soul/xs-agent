import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from novel_writer.api.app import create_app
from novel_writer.core.config import Settings
from novel_writer.core.credentials import CredentialError, MemoryCredentialStore
from novel_writer.services.provider_profiles import (
    ProviderModelOption,
    ProviderProfile,
    ProviderProfileStore,
    build_provider,
)
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


@pytest.mark.parametrize("api_key", ["", "test-self-hosted-key"])
@pytest.mark.parametrize("authorization_scheme", ["bearer", "raw"])
@pytest.mark.parametrize(
    "base_url",
    [
        "http://192.168.1.10:8000/v1",
        "http://model-service:8000/v1",
    ],
)
def test_http_profile_save_reload_and_connection_with_optional_key(
    management,
    monkeypatch,
    base_url,
    api_key,
    authorization_scheme,
):
    client, _ = management
    from novel_writer.api.routes import provider_profiles as routes

    requests = []

    def respond(request):
        requests.append(request)
        assert str(request.url) == base_url + "/chat/completions"
        expected = api_key if authorization_scheme == "raw" else f"Bearer {api_key}"
        assert request.headers.get("authorization") == (expected if api_key else None)
        assert json.loads(request.content)["chat_template_kwargs"] == {
            "enable_thinking": False,
        }
        return httpx.Response(
            200,
            json={
                "id": "local-test",
                "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    transport_client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(
        routes, "build_provider", lambda p: build_provider(p, client=transport_client)
    )
    try:
        payload = {
            "id": "self-hosted",
            "display_name": "自部署模型",
            "protocol": "openai_chat_completions",
            "base_url": base_url,
            "is_local": False,
            "credential_required": bool(api_key),
            "authorization_scheme": authorization_scheme,
            "chat_template_enable_thinking": False,
            "default_model": "local-model",
            "models": [{"id": "local-model"}],
        }
        saved = client.put(
            "/api/provider-profiles/self-hosted",
            headers=headers(),
            json={
                "profile": payload,
                "confirmed": True,
            },
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["base_url"] == base_url
        assert not saved.json()["is_local"] and requests == []
        store = ProviderProfileStore(client.app.state.provider_profile_store.path)
        assert store.get("self-hosted").base_url == base_url
        assert store.get("self-hosted").credential_required is bool(api_key)
        assert store.get("self-hosted").authorization_scheme == authorization_scheme
        assert store.get("self-hosted").chat_template_enable_thinking is False
        if api_key:
            credential = client.post(
                "/api/provider-profiles/self-hosted/credential",
                headers=headers(),
                json={
                    "api_key": api_key,
                    "confirmed": True,
                },
            )
            assert credential.status_code == 200, credential.text
        tested = client.post(
            "/api/provider-profiles/self-hosted/test", headers=headers(), json={"confirmed": True}
        )
        assert tested.status_code == 200, tested.text
        assert tested.json()["ok"] is True and len(requests) == 1
        assert current_profile(client, "self-hosted")["base_url"] == base_url
    finally:
        asyncio.run(transport_client.aclose())
