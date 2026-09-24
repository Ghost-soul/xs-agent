# ruff: noqa: F811
from uuid import uuid4

from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_longform import longform  # noqa: F401
from tests.integration.test_genre_generation import (  # noqa: F401
    create,
    generation,
    post,
    read,
    start,
)
from tests.unit.test_generation_context_budget import install


def test_new_preview_resolves_assets_once_and_preserves_blocked_original(
    longform, tmp_path, monkeypatch
):
    from novel_writer.generation import service

    client, control = longform
    base, first = create(
        client,
        workflow="novel-run-v1",
        stage_mode="longform-v1",
        unit_limit=2,
        context_policy="full-v1",
    )
    install(tmp_path, monkeypatch)
    import json

    manifest_path = tmp_path / "exact.manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["models"] = ["fixture-model"]
    manifest_path.write_text(json.dumps(manifest))
    payload = {**first["spec"],
        "feedback_policy": "logic-v1", "enable_reader": False, "milestone_unit": None,
         "context_policy": "bounded-v1"}
    key = str(uuid4())
    response = post(client, base, payload, key)
    assert response.status_code == 200, response.text
    new = response.json()
    assert new["id"] != first["id"] and new["spec"]["chief_tokenizer_id"] == "exact"
    assert new["snapshot"]["counting"]["writer"]["method"] == "local-tokenizer"
    assert new["snapshot"]["cards"] == first["snapshot"]["cards"]
    assert new["spec"]["input_limit"] == first["spec"]["input_limit"]
    assert control["calls"] == []
    monkeypatch.setattr(
        service,
        "resolve_tokenizers",
        lambda _: (_ for _ in ()).throw(AssertionError("idempotent repeat must not resolve again")),
    )
    repeated = post(client, base, payload, key)
    assert repeated.status_code == 200 and repeated.json()["id"] == new["id"]
    assert read(client, f"{base}/{first['id']}")["snapshot"] == first["snapshot"]
    completed = start(client, base, new)
    assert completed["status"] == "needs_attention", completed["state"]
    assert len(control["calls"]) <= new["snapshot"]["maximum_calls"]
