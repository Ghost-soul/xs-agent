from copy import deepcopy
from uuid import uuid4

import pytest

from novel_writer.domain.character import Character
from novel_writer.domain.state import StoryState
from novel_writer.generation import craft_contract, creative_cast, creative_contract
from novel_writer.generation.content import json_text, paragraphs, parse_object
from novel_writer.generation.reports import memory_result
from tests.unit.test_novel_run_rebuild import memory
from tests.unit.test_stage_craft import craft_fixture


def fixture():
    spec, snapshot, plan = craft_fixture()
    creative_contract.bind_snapshot(
        spec, snapshot, {"characters": snapshot["context"]["characters"]}
    )
    proposal = {
        "id": snapshot["new_character_slots"][0],
        "name": "林岑",
        "description": "渡口值夜的向导",
        "independent_goal": "找回遗失的灯笼",
        "voice": "短句，先说眼前要办的事",
        "entry_reason": "听见码头有人呼救，带绳赶来",
    }
    plan["new_characters"] = [proposal]
    plan["scenes"][0]["character_ids"].append(proposal["id"])
    return spec, snapshot, plan


def test_old_contract_and_render_stay_exact_without_new_binding():
    spec, snapshot, plan = craft_fixture()
    assert creative_contract.contract_for(spec, snapshot) == craft_contract.contract_for(
        spec, snapshot
    )
    for action in ("plan", "write:1"):
        assert creative_contract.render_for(
            spec, snapshot, action, plan
        ) == craft_contract.render_for(
            spec,
            snapshot,
            action,
            plan,
        )


def test_new_plan_uses_reserved_identity_without_adding_formal_records():
    spec, snapshot, raw = fixture()
    before = deepcopy(snapshot)
    plan = creative_cast.parse_stage(json_text(raw), spec, snapshot)
    assert str(plan.new_characters[0].id) in plan.scenes[0].character_ids
    assert snapshot == before
    assert not any(
        c["id"] == str(plan.new_characters[0].id) for c in snapshot["context"]["characters"]
    )
    edit = creative_cast.CreativePlanEdit(plan=plan.model_dump(mode="json"), author_note="调整事件")
    assert isinstance(edit.plan, creative_cast.CreativePlan)


@pytest.mark.parametrize("bad", ["foreign", "duplicate", "existing_name", "unused", "too_many"])
def test_invalid_proposals_do_not_widen_cast(bad):
    spec, snapshot, value = fixture()
    p = value["new_characters"][0]
    if bad == "foreign":
        p["id"] = str(uuid4())
    elif bad == "duplicate":
        value["new_characters"].append(deepcopy(p))
    elif bad == "existing_name":
        p["name"] = snapshot["character_identity_registry"][0]["names"][0]
    elif bad == "unused":
        value["scenes"][0]["character_ids"].remove(p["id"])
    else:
        value["new_characters"] *= 4
    with pytest.raises(ValueError):
        creative_cast.parse_stage(json_text(value), spec, snapshot)


def test_gap_becomes_nonblocking_uncertainty_and_explicit_author_conflict_remains():
    spec, snapshot, raw = fixture()
    raw.update(
        questions=["向导叫什么？", "作者同时要求两人留在不同地点又同场相遇？"],
        question_scopes={"向导叫什么？": "current_unit"},
    )
    raw["author_question_reasons"] = {
        q: {"kind": kind, "source": "作者方向" if i else "尚未设定", "why_blocked": "原模型说明"}
        for i, (q, kind) in enumerate(
            zip(
                raw["questions"],
                [
                    "missing_canonical_fact",
                    "author_boundary_conflict",
                ],
                strict=True,
            )
        )
    }
    before = deepcopy(raw)
    plan = creative_cast.parse_stage(json_text(raw), spec, snapshot)
    assert plan.questions == [raw["questions"][1]] and not plan.question_scopes
    assert "向导叫什么？" in plan.creative_notes[0] and "尚未设定" in plan.creative_notes[0]
    assert raw == before


def test_writer_gets_proposal_but_memory_only_gets_identity_and_no_formal_pollution():
    spec, snapshot, value = fixture()
    proposal = value["new_characters"][0]
    plan = creative_cast.parse_stage(json_text(value), spec, snapshot).model_dump(mode="json")
    writer = parse_object(creative_contract.render_for(spec, snapshot, "write:1", plan)[1])
    assert writer["character_proposals"] == [proposal]
    assert proposal["id"] not in json_text(writer["formal_reference"])
    body = "林岑提着灯笼来到渡口。"
    report = parse_object(creative_contract.render_for(spec, snapshot, "memory:1", plan, body)[1])
    assert report["character_proposals"] == [{"id": proposal["id"], "name": proposal["name"]}]
    assert proposal["independent_goal"] not in json_text(report)
    assert proposal["id"] not in json_text(report["opening_reference"])


def test_full_notes_list_keeps_gap_source_without_rejecting_plan():
    spec, snapshot, raw = fixture()
    raw["creative_notes"] = [f"待展开事项 {i}" for i in range(16)]
    raw["questions"] = ["向导从哪里来？"]
    raw["author_question_reasons"] = {
        raw["questions"][0]: {
            "kind": "missing_canonical_fact", "source": "未设定出发地", "why_blocked": "原说明",
        },
    }
    plan = creative_cast.parse_stage(json_text(raw), spec, snapshot)
    assert len(plan.creative_notes) == 16 and not plan.questions
    joined = "\n".join(plan.creative_notes)
    assert all(note in joined for note in raw["creative_notes"])
    assert "未设定出发地" in joined and "原说明" in joined


def test_memory_reserved_identity_requires_evidence_and_remains_stable_on_update():
    identifier = str(uuid4())
    body = "林岑提着灯笼来到渡口。"
    raw = memory(
        body=body,
        changes=[
            {
                "collection": "characters",
                "object_id": identifier,
                "values": {"name": "林岑"},
                "observation": "林岑来到渡口",
                "paragraph_ids": [paragraphs(body)[0]["id"]],
            }
        ],
    )
    assert memory_result(json_text(raw), body, StoryState(), 1)["status"] == "partial"
    accepted = memory_result(
        json_text(raw), body, StoryState(), 1, reserved_character_ids=frozenset({identifier})
    )
    assert accepted["status"] == "complete"
    character = Character.model_validate(accepted["factual_changes"]["add_characters"][0])
    assert str(character.id) == identifier
    raw["changes"][0]["values"] = {"current_state": "带灯抵达"}
    updated = memory_result(
        json_text(raw),
        body,
        StoryState(characters=(character,)),
        1,
        reserved_character_ids=frozenset({identifier}),
    )
    assert updated["factual_changes"]["add_characters"][0]["name"] == "林岑"
    raw["changes"][0]["paragraph_ids"] = ["another-body:1"]
    invalid = memory_result(
        json_text(raw), body, StoryState(), 1, reserved_character_ids=frozenset({identifier})
    )
    assert invalid["status"] == "partial" and not invalid["factual_changes"]
    raw["changes"][0].update(
        collection="places", values={"name": "渡口"}, paragraph_ids=[paragraphs(body)[0]["id"]]
    )
    assert (
        memory_result(
            json_text(raw), body, StoryState(), 1, reserved_character_ids=frozenset({identifier})
        )["status"]
        == "partial"
    )


def test_author_template_cannot_omit_proposals_even_if_it_starts_with_brace():
    source = {"character_proposals": [{"name": "林岑"}], "creative_notes": []}
    system, task = creative_contract.finish("作者原模板", '{"作者排版":"保留"}', source, "write:1")
    assert system.startswith("作者原模板") and "林岑" in task
    assert task.startswith('{"作者排版":"保留"}')


def test_written_proposals_cannot_be_repurposed_by_plan_edit():
    _, _, plan = fixture()
    edited = deepcopy(plan)
    edited["new_characters"][0]["name"] = "另一个人"
    with pytest.raises(ValueError, match="候选人物身份"):
        creative_cast.protect_proposals(plan, edited, 1)
    creative_cast.protect_proposals(plan, edited, 0)
