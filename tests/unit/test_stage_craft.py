import json
from copy import deepcopy

import pytest

from novel_writer.generation import craft_context, craft_contract, craft_templates, event_units
from novel_writer.generation.content import fingerprint, json_text
from novel_writer.generation.craft_models import CraftSpec, read_spec
from novel_writer.generation.craft_plans import parse_stage
from novel_writer.generation.stage_scale import acceptable_units, characters, ranges, status
from novel_writer.services.craft_template_store import CraftTemplateStore
from novel_writer.services.prompt_template_store import PromptTemplateStore
from tests.unit.test_event_units import fixture


def craft_fixture():
    spec, snapshot, plan = fixture()
    data = spec.model_dump(mode="json")
    data.update(stage_mode="longform-v1", unit_limit=5)
    return CraftSpec.model_validate(data), snapshot, plan


@pytest.mark.parametrize("size", [2, 4, 5, 6])
def test_readable_scope_is_separate_from_execution_cap(size):
    spec, snapshot, plan = craft_fixture()
    plan["scenes"] = [deepcopy(plan["scenes"][0]) for _ in range(size)]
    parsed = parse_stage(json_text(plan), spec, snapshot)
    assert len(parsed.scenes) == size
    assert acceptable_units(spec) == (4, 5)
    allocated = ranges(spec.stage_scale, parsed.model_dump()["scenes"])
    assert sum(u["min_characters"] for u in allocated) == 15000
    assert sum(u["max_characters"] for u in allocated) == 20000


def test_bad_weight_does_not_discard_complete_plan():
    spec, snapshot, plan = craft_fixture()
    for scene in plan["scenes"]:
        scene["size_weight"] = "not a weight"
    parsed = parse_stage(json_text(plan), spec, snapshot)
    assert all(s.size_weight == 1 for s in parsed.scenes)


def test_unicode_and_saved_manuscript_scale():
    spec, _, _ = craft_fixture()
    assert characters("汉，𠮷🙂\n\r\t\u3000\u0085") == 4
    for count, expected in [(8000, "below"), (15000, "within"), (24000, "above")]:
        result = status(spec, "字" * count, [])
        assert result["characters"] == count
        assert result["status"] == expected
        assert result["deficit"] == max(0, 15000 - count)
    single = CraftSpec.model_validate(
        {
            **spec.model_dump(),
            "stage_mode": "single-unit-v1",
            "unit_limit": 1,
            "stage_scale": {"scale_mode": "natural", "preferred_units": 1},
        }
    )
    assert status(single, "短篇", [])["status"] == "not_requested"


def test_macro_and_previous_full_unit_reach_actual_render_with_no_optional_budget():
    spec, snapshot, plan = craft_fixture()
    state = {
        "narrative_phases": [{"id": "phase", "status": "active", "goal": "偿还债务"}],
        "narrative_position": {"current_location": "港口"},
    }
    craft_context.bind_snapshot(spec, snapshot, state)
    chief = json.loads(craft_contract.render_for(spec, snapshot, "plan")[1])
    assert chief["macro_reference"]["current_phase"]["goal"] == "偿还债务"
    assert chief["stage_scale"]["preferred_units"] == 5
    assert chief["output_schema"]["properties"]["scenes"]["minItems"] == 4
    prose = "早段的重要交接。\n\n" + "经过。" * 1800 + "\n\n最后的回应。"
    reports = {
        "_key_material_target": 0,
        "role_units": [
            {
                "unit": 1,
                "start": 0,
                "end": len(prose),
                "outcome": "交接完成",
                "position": {},
                "unresolved": [],
            }
        ],
    }
    writer = json.loads(
        craft_contract.render_for(spec, snapshot, "write:2", plan, prose, None, reports)[1]
    )
    assert writer["continuity"]["recent_prose"]["text"] == prose
    assert writer["continuity"]["coverage"] == "full"
    assert "不设字数目标" not in craft_contract.render_for(spec, snapshot, "write:2", plan)[0]


def test_templates_do_not_overwrite_old_files_or_old_bindings(tmp_path):
    legacy = PromptTemplateStore(tmp_path / "profiles.json")
    original = legacy.save(
        "writer",
        __import__(
            "novel_writer.generation.template_catalog", fromlist=["default_text"]
        ).default_text("writer"),
        "builtin",
        "旧版本",
    )
    store = CraftTemplateStore(tmp_path / "profiles.json")
    adapted = store.install()
    assert legacy.current() == original
    assert store.install() == adapted
    assert store.version(adapted["revision"]) == adapted
    spec, snap, plan = craft_fixture()
    snap["prompt_templates"] = adapted
    reports = {}
    system, task = craft_contract.render_for(spec, snap, "write:1", plan, None, None, reports)
    assert '"min_characters":15000' in task
    assert reports["prompt_template_revision"] == adapted["revision"]
    assert "不设字数目标" not in system
    assert craft_templates.validate_template("writer", store.text("writer", adapted)) == []


def test_old_absent_fields_contract_and_renderer_are_unchanged():
    spec, snap, plan = fixture()
    from novel_writer.generation.schemas import FrozenGenerationSpec

    assert (
        read_spec(spec.model_dump()).model_dump()
        == FrozenGenerationSpec.model_validate(spec.model_dump()).model_dump()
    )
    assert "craft_policy" not in read_spec(spec.model_dump()).model_dump()
    assert craft_contract.contract_for(spec, snap) == event_units.contract_for(spec)
    for action in ("plan", "write:1", "memory:1", "checker", "rewrite", "title"):
        assert fingerprint(craft_contract.render_for(spec, snap, action, plan)) == fingerprint(
            event_units.render_for(spec, snap, action, plan)
        )


def test_incomplete_fragment_is_not_counted_as_finished_prose():
    from novel_writer.generation.content import digest

    spec, _, _ = craft_fixture()
    body = "完整单元。\n\n未知尾稿"
    units = [
        {"ordinal": 1, "start": 0, "end": 5, "body_sha256": digest(body[:5])},
        {"ordinal": 2, "start": 7, "end": len(body), "complete": False},
    ]
    scale = status(spec, body, units, complete=False)
    assert scale["characters"] == 5 and scale["unfinished_characters"] == 4
    assert scale["units"] == [{"ordinal": 1, "characters": 5}]


def test_long_window_preserves_earlier_source_without_trusting_memory_text():
    spec, snap, plan = craft_fixture()
    text = "她交出了钥匙。\n\n" + "\n\n".join("后来。" * 500 for _ in range(10))
    reports = {
        "role_units": [{"unit": 1, "start": 0, "end": len(text)}],
        "memory": {"changes": [{"evidence": [{"start": 0, "end": 7, "text": "伪造引文"}]}]},
    }
    result = craft_context.continuity(snap, text, reports, "write:2", plan)
    assert not result["recent_prose"]["complete"]
    assert result["recent_prose"]["text"] == text[result["recent_prose"]["start"] :]
    assert result["earlier_direct_evidence"][0]["text"] == text[:7]
    assert "伪造引文" not in json_text(result)


def test_macro_does_not_resurrect_fulfilled_or_future_phase_as_fact():
    spec, snap, _ = craft_fixture()
    spec = spec.model_copy(update={"character_ids": ["person-one"]})
    snap["sources"] = [{"revision_id": str(i)} for i in range(5)]
    snap["context"]["formal_summaries"] = [
        {"revision_id": str(i), "outcome": str(i)} for i in [4, 3, 0, 99, 2, 1]
    ]
    state = {
        "narrative_phases": [
            {"id": "active", "status": "active", "goal": "当前"},
            {"id": "done", "status": "completed", "goal": "结束"},
            {"id": "next", "status": "pending", "goal": "未来"},
        ],
        "reader_promises": [
            {"id": "fulfilled", "status": "fulfilled", "character_id": spec.character_ids[0]},
            {"id": "open", "status": "open", "character_id": spec.character_ids[0]},
        ],
    }
    craft_context.bind_snapshot(spec, snap, state)
    macro = snap["craft_macro"]
    assert macro["source_version_id"] == str(spec.base_version_id)
    assert macro["next_phase_reference"]["id"] == "next"
    assert [r["record"]["id"] for r in macro["related_open_references"]] == ["open"]
    assert not macro["future_is_fact"]
    assert not macro["related_open_references"][0]["binding_instruction"]
    assert [s["revision_id"] for s in macro["actual_recent_changes"]] == ["2", "3", "4"]


def test_final_capacity_never_discards_required_continuity():
    from novel_writer.generation import budget
    from novel_writer.generation.request_preparation import InputCapacityError, prepare_request
    from tests.unit.test_generation_context_budget import profile

    spec, snap, plan = craft_fixture()
    spec = spec.model_copy(update={"input_limit": 8000, "writer_model": "test"})
    snap["profile"] = profile().model_dump(mode="json")
    snap["counting"] = {"writer": budget.counting_config(None, "test")}
    # A single necessary paragraph cannot be shortened to fit a hard cap.
    text = "关键动作。" * 3000
    reports = {
        "role_units": [
            {
                "unit": 1,
                "start": 0,
                "end": len(text),
                "outcome": "动作",
                "position": {},
                "unresolved": [],
            }
        ]
    }
    with pytest.raises(InputCapacityError) as raised:
        prepare_request(spec, snap, "write:2", plan, text, None, reports, {})
    payload = json.loads(raised.value.request.user_prompt)
    assert payload["continuity"]["recent_prose"]["text"] == text
    assert reports["key_context_selection"]["craft_protected_count"] > 8000


def test_version_collision_corruption_and_unknown_reset_fail_closed(tmp_path):
    from novel_writer.services.errors import ConflictError, WorkflowError

    store = CraftTemplateStore(tmp_path / "profiles.json")
    bundle = store.install()
    current_bytes = (store.root / "current.json").read_bytes()
    collision = {**bundle, "note": "不同内容"}
    collision["sha256"] = fingerprint({k: v for k, v in collision.items() if k != "sha256"})
    version = store.root / "versions" / f"{bundle['revision']}.json"
    version.write_text(json_text(collision), encoding="utf8")
    with pytest.raises(ConflictError, match="同版本"):
        store.save("writer", None, bundle["revision"], "reset")
    assert (store.root / "current.json").read_bytes() == current_bytes
    with pytest.raises(WorkflowError, match="未知"):
        store.save("unknown", None, bundle["revision"], "reset")
    (store.root / "current.json").write_text("broken", encoding="utf8")
    with pytest.raises(WorkflowError, match="损坏"):
        store.current()


def test_published_seed_adapts_in_empty_volume_without_overwriting_mounted_text(tmp_path):
    from pathlib import Path

    profile_path = tmp_path / "profiles.json"
    legacy = PromptTemplateStore(profile_path)
    assert legacy.seed_from(Path("configs/prompt-templates/published.json"))
    original = (legacy.root / "current.json").read_bytes()
    store = CraftTemplateStore(profile_path)
    first = store.install()
    for role in ("chief", "writer"):
        assert not craft_templates.possible_conflicts(store.text(role, first))
    custom = store.text("writer", first).model_copy(update={"system_text": "保留卷内作者文字"})
    saved = store.save("writer", custom, first["revision"], "作者修改")
    assert not legacy.seed_from(Path("configs/prompt-templates/published.json"))
    assert store.install() == saved
    assert (legacy.root / "current.json").read_bytes() == original
