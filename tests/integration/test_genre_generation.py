import asyncio
import json
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from novel_writer.api.app import create_app
from novel_writer.core.config import Settings
from novel_writer.core.credentials import MemoryCredentialStore
from novel_writer.generation.content import digest, json_text, paragraphs
from novel_writer.generation.runtime import GenerationRuntime
from novel_writer.providers.base import (
    ModelResponse,
    ProviderResponseError,
    ProviderTerminalMetadata,
    TokenUsage,
)
from novel_writer.services.provider_profiles import ProviderModelOption, ProviderProfile
from tests.integration.support import DATABASE_URL, headers
from tests.integration.support import pytestmark as pytestmark
from tests.unit.test_genre_generation import plan

BODY = "林青发现自己希望她留下，便把那张同行的船票递了过去。\n\n对方看懂邀请，答应明日同行。"
REAL_DISPATCH = GenerationRuntime.dispatch


@pytest.fixture
def generation(clean_test_database, tmp_path, monkeypatch):
    assert DATABASE_URL is not None
    app = create_app(
        Settings(
            database_url=DATABASE_URL,
            local_token="integration-token",
            provider_profiles_path=tmp_path / "profiles.json",
            content_store_root=tmp_path / "content",
            project_workspace_root=tmp_path / "projects",
            log_dir=tmp_path / "logs",
            local_task_worker_enabled=False,
            _env_file=None,
        )
    )
    app.state.credential_store = MemoryCredentialStore({})
    app.state.provider_profile_store.save(
        ProviderProfile(
            id="fixture",
            display_name="离线替身",
            protocol="openai_chat_completions",
            base_url="http://127.0.0.1:1/v1",
            is_local=True,
            credential_required=False,
            default_model="fixture-model",
            models=[
                ProviderModelOption(
                    id="fixture-model",
                    context_window=131072,
                    max_output_tokens=24000,
                    input_price_cny_per_million=0,
                    output_price_cny_per_million=0,
                )
            ],
        )
    )
    control = {"calls": [], "mode": "success", "characters": []}

    async def fake_dispatch(self, call_id, request, profile, spec, counting, api_key):
        if control["mode"] == "transport":
            return await REAL_DISPATCH(self, call_id, request, profile, spec, counting, api_key)
        action = (
            "write"
            if "你是 Writer" in request.system_prompt
            else "plan"
            if "你是 Chief" in request.system_prompt
            else "review"
        )
        control["calls"].append(action)
        terminal = ProviderTerminalMetadata(
            protocol="fixture", terminal_event_seen=True, finish_reason="stop"
        )
        if control["mode"] == "unknown":
            raise TimeoutError("fixture transport lost")
        if action == "plan":
            value = json_text(plan(spec.character_ids))
        elif action == "write":
            if control["mode"] == "hold_writers":
                control["entered"].add(str(spec.base_version_id))
                if len(control["entered"]) == 2:
                    control["both_entered"].set()
                await control["gate"].wait()
            if control["mode"] == "truncated":
                raise ProviderResponseError(
                    "fixture length",
                    "immutable raw truncated response",
                    code="token_limit_exceeded",
                    extracted_content=BODY,
                    terminal=terminal.model_copy(update={"finish_reason": "length"}),
                )
            value = BODY
            if control["mode"] == "writer_issue":
                value = '{\n "generation_blocked": "当前视角无法感知场面，需要修改核心事件"\n}'
        else:
            ids = [p["id"] for p in paragraphs(BODY)]
            value = json_text(
                {
                    "outcome": "realized",
                    "explanation": "特殊吸引改变选择",
                    "findings": [{"observation": "私人邀请", "paragraph_ids": ids}],
                    "classifications": [
                        {"paragraph_ids": ids, "category": "focus", "reason": "私人愿望影响行动"}
                    ],
                }
            )
            if control["mode"] == "broken_review":
                value = "broken visible report"
        return ModelResponse(
            text=value,
            usage=TokenUsage(input_tokens=1200, output_tokens=800),
            raw_response="immutable transport:" + value,
            terminal=terminal,
        )

    monkeypatch.setattr(GenerationRuntime, "dispatch", fake_dispatch)
    with TestClient(app) as client:
        yield client, control


def read(client, path):
    response = client.get(path, headers=headers())
    assert response.status_code == 200, response.text
    return response.json()


def post(client, path, data, key=None):
    return client.post(path, json=data, headers=headers(key or str(uuid4())))


def create(client, **changes):
    project = post(
        client,
        "/api/projects",
        {
            "title": "题材重构测试",
            "genre_card_id": "western_fantasy_dnd",
            "secondary_genre_card_ids": ["girls_love_gl"],
            "character_names": ["林青", "江月"],
        },
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["project_id"]
    base = f"/api/projects/{project_id}/generation-batches"
    setup = read(client, base + "/setup")
    ids = [c["id"] for c in setup["characters"]]
    data = {
            "base_version_id": setup["base_version_id"],
            "workflow": "single-chapter-v1",
            "context_policy": "bounded-v1",
            "automation_policy": "legacy-v1",
            "feedback_policy": "legacy-v1",
            "writing_policy": "legacy-v1",
            "card_selection_policy": "legacy-v1",
            "narrative_policy": "legacy-v1",
            "plan_policy": "exact-v1",
            "length_policy": "legacy-v1",
            "focus_card_id": "girls_love_gl",
            "direction": "爱情影响一次行动选择",
            "character_ids": ids,
            "viewpoint": "林青视角",
            "relationship_scope": "explore",
            "relationship_character_ids": ids,
            "profile_id": "fixture",
            "chief_model": "fixture-model",
            "writer_model": "fixture-model",
            "input_limit": 100000,
            # Explicit fixture capacities retain the legacy test scenarios.
            "chief_output_limit": 24000,
            "writer_output_limit": 12000,
            "auxiliary_output_limit": 6000,
            "max_cost_cny": "1",
            **changes,
        }
    if (
        data.get("feedback_policy") == "legacy-v1"
        or data.get("enable_reader")
        or data.get("milestone_unit")
    ):
        from tests.integration.legacy_generation_fixture import saved_generation_fixture

        result = saved_generation_fixture(client, base, data)
    else:
        result = post(client, base, data)
    assert result.status_code == 200, result.text
    batch = result.json()
    assert not batch["snapshot"]["blockers"], batch["snapshot"]["blockers"]
    return base, batch


def start(client, base, batch, key=None):
    response = post(
        client,
        f"{base}/{batch['id']}/authorize",
        {"preview_sha256": batch["preview_sha256"], "confirmed": True},
        key,
    )
    assert response.status_code == 200, response.text

    async def finish():
        await asyncio.wait_for(client.app.state.generation.tasks[UUID(batch["id"])], 10)

    client.portal.call(finish)
    return read(client, f"{base}/{batch['id']}")


def test_single_chapter_three_calls_adoption_binding_and_backup(generation):
    client, control = generation
    base, batch = create(client)
    batch = start(client, base, batch, "authorize-once")
    assert batch["status"] == "ready", batch["state"]
    assert control["calls"] == ["plan", "write", "review"]
    assert len(read(client, base.replace("/generation-batches", "/chapters"))) == 0
    # Ambiguous HTTP retry cannot claim any slot again.
    start(client, base, batch, "authorize-once")
    assert len(control["calls"]) == 3
    position = {
        "current_location": "渡口",
        "recent_major_event": "约好明日同行",
        "notes": "林青主动表达私人在意",
    }
    target = f"{base}/{batch['id']}"
    preview = post(client, target + "/adoption-preview", {"narrative_position": position}).json()
    wrong = post(
        client,
        target + "/adopt",
        {
            "confirmed": True,
            "preview_sha256": preview["preview_sha256"],
            "narrative_position": {**position, "current_location": "别处"},
        },
    )
    assert wrong.status_code == 409, wrong.text
    adopted = post(
        client,
        target + "/adopt",
        {
            "confirmed": True,
            "preview_sha256": preview["preview_sha256"],
            "narrative_position": position,
        },
    )
    assert adopted.status_code == 200, adopted.text
    assert adopted.json()["version_number"] == 2
    assert read(client, target)["status"] == "adopted"
    project_base = base.replace("/generation-batches", "")
    backup = read(client, project_base + "/backup")
    assert len(backup["generation_history"][0]["calls"]) == 3
    assert backup["generation_history"][0]["calls"][0]["response"]["raw_response"].startswith(
        "immutable"
    )
    restored = post(client, "/api/data/backup/restore", {"archive": backup, "confirmed": True})
    assert restored.status_code == 201, restored.text
    restored_base = f"/api/projects/{restored.json()['project_id']}/generation-batches"
    archived = read(client, restored_base)[0]
    assert archived["status"] == "archived"
    detail = read(client, restored_base + "/" + archived["id"])
    assert not detail["calls"] and any(a["kind"] == "candidate" for a in detail["artifacts"])
    assert (
        post(
            client,
            restored_base + "/" + archived["id"] + "/authorize",
            {"confirmed": True, "preview_sha256": detail["preview_sha256"]},
        ).status_code
        == 409
    )


def test_failed_review_keeps_prose_and_requires_explicit_deviation_choice(generation):
    client, control = generation
    control["mode"] = "broken_review"
    base, batch = create(client)
    batch = start(client, base, batch)
    assert batch["status"] == "needs_attention"
    assert len(batch["calls"]) == 3
    candidate = next(a for a in batch["artifacts"] if a["kind"] == "candidate")
    assert candidate["payload"]["body"] == BODY
    target = f"{base}/{batch['id']}"
    position = {"current_location": "渡口", "recent_major_event": "邀约"}
    preview = post(client, target + "/adoption-preview", {"narrative_position": position}).json()
    payload = {
        "confirmed": True,
        "preview_sha256": preview["preview_sha256"],
        "narrative_position": position,
    }
    assert post(client, target + "/adopt", payload).status_code == 400
    assert (
        post(client, target + "/adopt", {**payload, "accept_genre_deviation": True}).status_code
        == 200
    )
    assert len(control["calls"]) == 3


def test_truncated_writer_preserved_manual_completion_invalidates_review(generation):
    client, control = generation
    control["mode"] = "truncated"
    base, batch = create(client)
    batch = start(client, base, batch)
    target = f"{base}/{batch['id']}"
    candidate = next(a for a in batch["artifacts"] if a["kind"] == "candidate")
    assert candidate["payload"]["complete"] is False
    assert len(control["calls"]) == 2
    assert post(client, target + "/adoption-preview", {"narrative_position": {}}).status_code == 400
    edited = client.put(
        target + "/candidate",
        json={"body": BODY + "\n\n她们各自回家。", "expected_body_sha256": digest(BODY)},
        headers=headers(str(uuid4())),
    )
    assert edited.status_code == 200, edited.text
    assert len([a for a in edited.json()["artifacts"] if a["kind"] == "candidate"]) == 2


def test_unknown_never_retries_and_author_can_close_with_receipt(generation):
    client, control = generation
    control["mode"] = "unknown"
    base, batch = create(client)
    batch = start(client, base, batch)
    assert batch["status"] == "outcome_uncertain"
    target = f"{base}/{batch['id']}"
    assert (
        post(
            client,
            target + "/authorize",
            {"confirmed": True, "preview_sha256": batch["preview_sha256"]},
        ).status_code
        == 409
    )
    assert len(control["calls"]) == 1
    closed = post(
        client, target + "/resolve-unknown", {"confirmed": True, "note": "已核对，保留未知费用"}
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["calls"][0]["status"] == "uncertain_closed"
    assert len(control["calls"]) == 1


def test_plan_pause_author_revision_and_other_project_are_independent(generation):
    client, control = generation
    base, batch = create(client, pause_after_plan=True)
    batch = start(client, base, batch)
    assert batch["status"] == "awaiting_plan" and len(control["calls"]) == 1
    second_base, second = create(client)
    second = start(client, second_base, second)
    assert second["status"] == "ready"
    assert read(client, f"{base}/{batch['id']}")["status"] == "awaiting_plan"
    item = next(a for a in batch["artifacts"] if a["kind"] == "plan")
    edited = client.put(
        f"{base}/{batch['id']}/plan",
        json={
            "plan": item["payload"],
            "expected_plan_sha256": item["sha256"],
            "author_note": "保留方案，双方均可自主拒绝",
        },
        headers=headers(str(uuid4())),
    )
    assert edited.status_code == 200, edited.text
    final = start(client, base, edited.json())
    assert final["status"] == "ready" and len(final["calls"]) == 3


@pytest.mark.parametrize(
    "protocol", ["openai_chat_completions", "deepseek_chat", "openai_responses"]
)
def test_actual_protocol_mapping_budget_and_receipts(generation, monkeypatch, protocol):
    client, control = generation
    store = client.app.state.provider_profile_store
    original = store.get("fixture")
    store.save(
        original.model_copy(
            update={
                "protocol": protocol,
                "structured_output_mode": "json_schema"
                if protocol == "openai_responses"
                else "json_object",
            }
        )
    )
    base, batch = create(client)
    control["mode"] = "transport"
    wire = []

    def respond(request):
        payload = json.loads(request.content)
        wire.append(payload)
        ids = [p["id"] for p in paragraphs(BODY)]
        values = [
            json_text(plan(batch["spec"]["character_ids"])),
            BODY,
            json_text(
                {
                    "outcome": "realized",
                    "explanation": "选择改变",
                    "findings": [{"observation": "邀请", "paragraph_ids": ids}],
                    "classifications": [
                        {"paragraph_ids": ids, "category": "focus", "reason": "私人选择"}
                    ],
                }
            ),
        ]
        value = values[len(wire) - 1]
        if protocol == "openai_responses":
            output = {
                "id": "fixture-response",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": value}],
                    }
                ],
                "usage": {"input_tokens": 1200, "output_tokens": 800},
            }
        else:
            output = {
                "id": "fixture-response",
                "choices": [{"message": {"content": value}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1200, "completion_tokens": 800},
            }
        return httpx.Response(200, json=output)

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        "novel_writer.generation.runtime.httpx.AsyncClient",
        lambda **kwargs: original_client(**kwargs, transport=httpx.MockTransport(respond)),
    )
    batch = start(client, base, batch)
    assert batch["status"] == "ready", batch["state"]
    assert len(wire) == 3
    for call in batch["calls"]:
        receipt = read(client, f"{base}/{batch['id']}/calls/{call['id']}")
        assert receipt["request"]["wire_input_tokens"] <= 100000
        assert receipt["request"]["wire_body"]
        assert receipt["response"]["raw_response"]
        assert receipt["response"]["sha256"]


def test_writer_conflict_is_not_adoptable_prose(generation):
    client, control = generation
    control["mode"] = "writer_issue"
    base, batch = create(client)
    result = start(client, base, batch)
    assert result["status"] == "needs_attention"
    assert not any(a["kind"] == "candidate" for a in result["artifacts"])
    assert any(a["kind"] == "writer_issue" for a in result["artifacts"])
    assert control["calls"] == ["plan", "write"]


def test_final_wire_budget_blocks_before_transport(generation, monkeypatch):
    client, control = generation
    control["mode"] = "transport"
    base, batch = create(client)
    network = []
    original_client = httpx.AsyncClient

    def forbidden(request):
        network.append(request)
        raise AssertionError("oversized wire request must never dispatch")

    monkeypatch.setattr(
        "novel_writer.generation.runtime.httpx.AsyncClient",
        lambda **kwargs: original_client(**kwargs, transport=httpx.MockTransport(forbidden)),
    )

    class ProviderWithLargeEnvelope:
        def __init__(self, connection):
            self.connection = connection

        async def generate(self, request, api_key):
            await self.connection.post(
                "http://127.0.0.1:1/mock",
                json={
                    "system": request.system_prompt,
                    "user": request.user_prompt,
                    "protocol_envelope": "padding" * 20000,
                },
            )
            raise AssertionError("request should be rejected")

    monkeypatch.setattr(
        "novel_writer.generation.runtime.build_provider",
        lambda profile, *, client: ProviderWithLargeEnvelope(client),
    )
    result = start(client, base, batch)
    assert result["status"] == "failed"
    assert result["calls"][0]["status"] == "not_dispatched"
    assert not network


def test_invalid_factual_reference_is_a_reviewable_client_error(generation):
    client, _ = generation
    base, batch = create(client)
    batch = start(client, base, batch)
    response = post(
        client,
        f"{base}/{batch['id']}/adoption-preview",
        {
            "narrative_position": {"current_location": "渡口", "recent_major_event": "邀请"},
            "factual_changes": {
                "add_relationships": [
                    {
                        "source_character_id": str(uuid4()),
                        "target_character_id": str(uuid4()),
                        "relation_type": "陌生关系",
                    }
                ]
            },
        },
    )
    assert response.status_code == 400
    assert "引用无效" in response.text


def test_pausing_one_inflight_project_does_not_cancel_another(generation):
    client, control = generation
    control["mode"] = "hold_writers"

    async def gates():
        control.update(gate=asyncio.Event(), both_entered=asyncio.Event(), entered=set())

    client.portal.call(gates)
    first_base, first = create(client)
    second_base, second = create(client)
    for base, batch in ((first_base, first), (second_base, second)):
        response = post(
            client,
            f"{base}/{batch['id']}/authorize",
            {"confirmed": True, "preview_sha256": batch["preview_sha256"]},
        )
        assert response.status_code == 200, response.text

    async def wait_entered():
        await asyncio.wait_for(control["both_entered"].wait(), 5)

    client.portal.call(wait_entered)
    paused = post(client, f"{first_base}/{first['id']}/pause", {"confirmed": True})
    assert paused.status_code == 200

    async def release():
        control["gate"].set()
        await asyncio.wait_for(asyncio.gather(*client.app.state.generation.tasks.values()), 5)

    client.portal.call(release)
    assert read(client, f"{first_base}/{first['id']}")["status"] == "paused"
    assert read(client, f"{second_base}/{second['id']}")["status"] == "ready"
    assert control["calls"].count("write") == 2
    assert control["calls"].count("review") == 1


def test_formal_change_invalidates_queued_design(generation):
    client, _ = generation
    base, batch = create(client, pause_after_plan=True)
    batch = start(client, base, batch)
    project_base = base.replace("/generation-batches", "")
    section = read(client, project_base + "/story-blueprint/sections/world")
    changed = client.put(
        project_base + "/story-blueprint/sections/world",
        json={
            "expected_state_version": section["state_version"],
            "data": {**section["data"], "world_rules": [{"statement": "潮汐每日两次"}]},
            "confirmed": True,
        },
        headers=headers(str(uuid4())),
    )
    assert changed.status_code == 201, changed.text
    stale = read(client, f"{base}/{batch['id']}")
    assert stale["status"] == "archived"
    assert (
        post(
            client,
            f"{base}/{batch['id']}/authorize",
            {"confirmed": True, "preview_sha256": batch["preview_sha256"]},
        ).status_code
        == 409
    )
