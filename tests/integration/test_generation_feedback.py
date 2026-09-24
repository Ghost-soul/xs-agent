# ruff: noqa: F811
import json

import pytest

from novel_writer.generation.runtime import GenerationRuntime
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_automation import automated, settle  # noqa: F401
from tests.integration.test_generation_longform import longform, stage_create  # noqa: F401
from tests.integration.test_genre_generation import generation, post, read, start  # noqa: F401
from tests.integration.test_novel_run_rebuild import (
    current,
    novel_generation,  # noqa: F401
)


@pytest.fixture
def feedback_run(automated, monkeypatch):
    client, control = automated
    original = GenerationRuntime.dispatch

    async def dispatch(self, call_id, request, profile, spec, counting, api_key):
        response = await original(self, call_id, request, profile, spec, counting, api_key)
        action = control["calls"][-1]
        if action.startswith("memory:"):
            data = json.loads(response.text)
            for field in [
                "stage_complete",
                "correction_needed",
                "continuity_blocked",
                "progress_reason",
            ]:
                data.pop(field, None)
            # A stale creative judgement must not terminate or redirect writing.
            data["stage_complete"] = True
            data["correction_needed"] = True
            response = response.model_copy(update={"text": json.dumps(data)})
        if action.startswith("checker"):
            if action == "checker" and control.get("edit"):
                payload = control["requests"][action]
                return response.model_copy(
                    update={
                        "text": json.dumps(
                            {
                                "explanation": "局部措辞建议",
                                "issues": [
                                    {
                                        "observation": "替换重复用词",
                                        "paragraph_ids": [payload["candidate"][-1]["id"]],
                                        "severity": "warning",
                                        "local_edit": True,
                                    }
                                ],
                            }
                        )
                    }
                )
            response = response.model_copy(
                update={
                    "text": json.dumps(
                        {
                            "conclusion": "unknown",
                            "issues": [
                                {
                                    "observation": "需要更多前文",
                                    "severity": "blocking",
                                    "paragraph_ids": ["bad-id"],
                                }
                            ],
                        }
                    )
                }
            )
        if action == "editor":
            payload = control["requests"][action]
            response = response.model_copy(
                update={
                    "text": json.dumps(
                        {
                            "decisions": [
                                {
                                    "paragraph_id": p["id"],
                                    "disposition": "patched",
                                    "replacement": p["text"].replace("次选择", "回选择"),
                                    "reason": "局部用词",
                                }
                                for p in payload["authorized_paragraphs"]
                            ]
                        }
                    )
                }
            )
        if action.startswith("reader"):
            response = response.model_copy(
                update={"text": "这里的留白让我想继续读，没有必要立刻解释。"}
            )
            if control.get("truncated_reader"):
                response = response.model_copy(
                    update={
                        "terminal": response.terminal.model_copy(update={"finish_reason": "length"})
                    }
                )
        return response

    monkeypatch.setattr(GenerationRuntime, "dispatch", dispatch)
    return client, control


@pytest.mark.parametrize(
    "checker,reader", [(True, False), (False, False), (False, True), (True, True)]
)
@pytest.mark.parametrize("policy", ["advisory-v1", "logic-v1"])
def test_chief_writer_finish_before_optional_advice_and_author_can_adopt(
    feedback_run, checker, reader, policy
):
    client, control = feedback_run
    base, draft = stage_create(
        client,
        automation_policy="stage-auto-v1",
        feedback_policy=policy,
        enable_checker=checker,
        enable_reader=reader,
    )
    batch = start(client, base, draft)
    assert control["calls"] == [
        "plan",
        "write:1",
        "memory:1",
        "write:2",
        "memory:2",
        "write:3",
        "memory:3",
        *(["checker"] if checker else []),
        *(["reader"] if reader else []),
    ], batch["state"]
    assert all(c["status"] == "completed" for c in batch["calls"])
    assert batch["state"]["units_finished"]
    if checker:
        assert current(batch, "checker")["payload"]["blocking"] is False
        if policy == "logic-v1":
            assert set(control["requests"]["checker"]) == {
                "candidate",
                "formal_start",
                "reference_boundary",
            }
            assert current(batch, "checker")["payload"]["review_scope"] == "logic-only"
    if reader:
        assert set(control["requests"]["reader"]) == {"candidate", "public_preceding"}
        assert current(batch, "review")["payload"]["experience"].startswith("这里")
    suggestions = read(client, f"{base}/{batch['id']}/stage-chapters")
    approvals = [
        {
            "chapter_id": c["id"],
            "title": "",
            "narrative_position": c["position"]
            or {"current_location": "渡口", "recent_major_event": "相邀"},
            "factual_changes": c["factual_changes"],
            "facts_confirmed": True,
        }
        for c in suggestions["chapters"]
    ]
    preview = post(client, f"{base}/{batch['id']}/stage-adoption-preview", {"chapters": approvals})
    assert preview.status_code == 200, preview.text
    assert preview.json()["needs_genre_acknowledgement"] is False
    adopted = post(
        client,
        f"{base}/{batch['id']}/stage-adopt",
        {
            "chapters": approvals,
            "preview_sha256": preview.json()["preview_sha256"],
            "confirmed": True,
            "accept_genre_deviation": False,
        },
    )
    assert adopted.status_code == 200, adopted.text


@pytest.mark.parametrize("policy", ["advisory-v1", "logic-v1"])
def test_opted_in_reader_truncation_keeps_manuscript_and_no_retry(feedback_run, policy):
    client, control = feedback_run
    control["truncated_reader"] = True
    base, draft = stage_create(
        client, automation_policy="stage-auto-v1", feedback_policy=policy, enable_reader=True
    )
    batch = start(client, base, draft)
    assert control["calls"].count("reader") == 1
    assert current(batch, "candidate")["payload"]["complete"] is True
    assert batch["next_action"] is None
    assert read(client, f"{base}/{batch['id']}/stage-chapters")["chapters"]


@pytest.mark.parametrize("checker", [False, True])
def test_new_amendment_can_rebuild_memory_with_optional_logic_review(feedback_run, checker):
    client, control = feedback_run
    profile = client.app.state.provider_profile_store.get("fixture")
    profile.models[0].max_output_tokens = 100000
    profile.models[0].context_window = 300000
    client.app.state.provider_profile_store.save(profile)
    base, draft = stage_create(
        client, automation_policy="stage-auto-v1", feedback_policy="advisory-v1"
    )
    batch = start(client, base, draft)
    route = f"{base}/{batch['id']}"
    preview = post(
        client,
        route + "/amendment-preview",
        {
            "candidate_sha256": current(batch, "candidate")["sha256"],
            "mode": "verify",
            "instruction": "保留写法，只更新事实",
            "max_cost_cny": "10",
            "enable_checker": checker,
        },
    )
    assert preview.status_code == 200, preview.text
    expected = ["memory_amend", *(["checker_amend"] if checker else [])]
    assert preview.json()["slots"] == expected
    assert preview.json()["feedback_policy"] == "logic-v1"
    assert preview.json()["enable_reader"] is False
    size = len(control["calls"])
    approved = post(
        client,
        route + "/amendment-authorize",
        {"preview_sha256": preview.json()["preview_sha256"], "confirmed": True},
    )
    assert approved.status_code == 200, approved.text
    done = settle(client, base, batch["id"])
    assert control["calls"][size:] == expected
    assert done["spec"] == batch["spec"]
    if checker:
        assert "formal_start" in control["requests"]["checker_amend"]
    assert done["next_action"] is None
    assert all(c["status"] == "completed" for c in done["calls"]), done["state"]


def test_explicit_chief_milestone_keeps_creative_judgement_with_chief(feedback_run):
    client, control = feedback_run
    control["genre_progress"] = "missing"
    base, draft = stage_create(
        client, automation_policy="stage-auto-v1", feedback_policy="advisory-v1", milestone_unit=2
    )
    batch = start(client, base, draft)
    assert control["calls"].count("chief:1") == 1
    assert (
        control["calls"].index("memory:2")
        < control["calls"].index("chief:1")
        < control["calls"].index("write:3")
    )
    assert batch["state"]["units_finished"]
    assert not batch["state"].get("checkpoint_author_required")


def test_optional_title_and_unused_editor_end_without_reader(feedback_run):
    client, control = feedback_run
    base, draft = stage_create(
        client,
        automation_policy="stage-auto-v1",
        feedback_policy="advisory-v1",
        enable_editor=True,
        generate_title=True,
    )
    batch = start(client, base, draft)
    assert control["calls"][-2:] == ["checker", "title"]
    assert "editor" not in control["calls"]
    assert current(batch, "title")
    assert all(c["status"] == "completed" for c in batch["calls"]), batch["state"]


def test_opted_in_editor_rebuilds_facts_without_adding_reader(feedback_run):
    client, control = feedback_run
    control["edit"] = True
    base, draft = stage_create(
        client, automation_policy="stage-auto-v1", feedback_policy="advisory-v1", enable_editor=True
    )
    batch = start(client, base, draft)
    assert control["calls"][-4:] == ["checker", "editor", "memory_edit", "checker_edit"], batch[
        "state"
    ]
    assert "reader" not in control["calls"]
    assert all(c["status"] == "completed" for c in batch["calls"]), batch["state"]
    assert current(batch, "edit_decisions")["payload"]["changed"] is True
    assert batch["next_action"] is None


@pytest.mark.parametrize("reader", [False, True])
@pytest.mark.parametrize("policy", ["advisory-v1", "logic-v1"])
def test_single_unit_novel_supports_the_same_optional_feedback(novel_generation, reader, policy):
    from tests.integration.test_genre_generation import create

    client, control = novel_generation
    base, draft = create(
        client,
        workflow="novel-run-v1",
        feedback_policy=policy,
        enable_checker=False,
        enable_reader=reader,
    )
    batch = start(client, base, draft)
    assert control["calls"] == ["plan", "write", "memory", *(["reader"] if reader else [])]
    assert batch["next_action"] is None
    assert all(c["status"] == "completed" for c in batch["calls"]), batch["state"]


def test_logic_review_cannot_authorize_edits_from_style_advice(feedback_run):
    client, control = feedback_run
    control["edit"] = True
    base, draft = stage_create(
        client, automation_policy="stage-auto-v1", feedback_policy="logic-v1", enable_editor=True
    )
    assert not {"editor", "memory_edit", "checker_edit"} & set(draft["snapshot"]["action_slots"])
    batch = start(client, base, draft)
    assert control["calls"][-1] == "checker"
    assert len(control["calls"]) == 8
    result = current(batch, "checker")["payload"]
    assert result["issues"][0]["local_edit"] is False
    assert result["raw_feedback"]
    assert batch["state"]["units_finished"] and batch["next_action"] is None
