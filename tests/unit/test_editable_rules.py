from copy import deepcopy

import pytest

from novel_writer.generation import configurable_cast, creative_cast, creative_contract
from novel_writer.generation import editable_contract as contract
from novel_writer.generation import editable_rules as rules
from novel_writer.generation import output_contract_v3 as output
from novel_writer.generation.content import fingerprint, json_text, parse_object
from novel_writer.generation.craft_templates import default_text
from novel_writer.services.craft_template_store import CraftTemplateStore
from novel_writer.services.errors import ConflictError, WorkflowError
from tests.unit.test_creative_cast import fixture
from tests.unit.test_output_reliability import profile, request


def bind(maximum=3, texts=None, variant="chief"):
    spec, snapshot, plan = fixture()
    values = {variant: {"texts": texts or {}}}
    values.setdefault("chief", {})["maximum_new_characters"] = maximum
    bundle = {
        "format": "stage-craft-template-v1",
        "revision": "fixture",
        "templates": {},
        rules.SETTINGS: values,
    }
    bundle["sha256"] = fingerprint(bundle)
    snapshot["prompt_templates"] = bundle
    snapshot[output.KEY] = output.binding()
    contract.bind_snapshot(snapshot, {"characters": snapshot["context"]["characters"]})
    return spec, snapshot, plan


def render(spec, snapshot, plan, action="plan"):
    reports = {}
    system, task = contract.render_for(spec, snapshot, action, plan, reports=reports)
    wire = contract.prepare_output(
        request().model_copy(
            update={
                "system_prompt": system,
                "user_prompt": task,
            }
        ),
        profile(),
        action,
        snapshot,
        reports,
    )
    return wire, reports


@pytest.mark.parametrize("maximum", [0, 1, 5, 12])
def test_cap_controls_slots_wire_schema_and_parser_without_changing_v1(maximum):
    spec, snapshot, plan = bind(maximum)
    wire, reports = render(spec, snapshot, plan)
    source = parse_object(wire.user_prompt)
    assert len(source["creative_autonomy"]["new_character_slots"]) == maximum
    assert source["creative_autonomy"]["maximum_new_characters"] == maximum
    assert wire.json_schema["properties"]["new_characters"]["maxItems"] == maximum
    assert "最多三人" not in wire.system_prompt
    assert (
        creative_cast.CreativePlan.model_json_schema()["properties"]["new_characters"]["maxItems"]
        == 3
    )
    count = min(maximum, 5)  # Together with the original cast this fits the total cast limit.
    person = plan["new_characters"][0]
    original_ids = [i for i in plan["scenes"][0]["character_ids"] if str(i) != person["id"]]
    plan["new_characters"] = [
        {**person, "id": snapshot["new_character_slots"][i], "name": f"向导{i}"}
        for i in range(count)
    ]
    plan["scenes"][0]["character_ids"] = original_ids + [p["id"] for p in plan["new_characters"]]
    parsed = configurable_cast.parse_stage(json_text(plan), spec, snapshot)
    assert len(parsed.new_characters) == count
    assert reports[contract.KEY] == contract.binding()
    if maximum < 12:
        plan["new_characters"] = [person] * (maximum + 1)
        with pytest.raises(ValueError, match=f"最多新增 {maximum}"):
            configurable_cast.parse_stage(json_text(plan), spec, snapshot)


def test_each_guidance_replaces_generated_text_and_empty_removes_it():
    texts = {rule["key"]: "ONLY_" + rule["key"] for rule in rules.definitions("chief")}
    spec, snapshot, plan = bind(texts=texts)
    template = default_text("chief").model_copy(update={"system_text": "AUTHOR_TEXT"})
    bundle = snapshot["prompt_templates"]
    bundle["templates"]["chief"] = template.model_dump()
    bundle["sha256"] = fingerprint({k: v for k, v in bundle.items() if k != "sha256"})
    wire, reports = render(spec, snapshot, plan)
    for text in texts.values():
        assert wire.system_prompt.count(text) == 1
    assert creative_contract.AUTONOMY not in wire.system_prompt
    assert output.CHIEF_SCOPE not in wire.system_prompt
    assert "AUTHOR_TEXT" in wire.system_prompt and wire.json_schema
    snapshot["prompt_templates"][rules.SETTINGS]["chief"]["texts"] = dict.fromkeys(texts, "")
    bundle["sha256"] = fingerprint({k: v for k, v in bundle.items() if k != "sha256"})
    empty, _ = render(spec, snapshot, plan)
    assert "ONLY_" not in empty.system_prompt
    assert "【本阶段创作自主范围】" not in empty.system_prompt
    assert "本阶段目标 15,000–20,000 字" in empty.system_prompt
    assert empty.json_schema == wire.json_schema
    assert reports[rules.RECEIPT]["chief"]["texts"] == texts


@pytest.mark.parametrize("action,variant", [("write:1", "writer"), ("memory:1", "memory")])
def test_role_specific_guidance_is_used_for_current_action(action, variant):
    texts = {"creative_guidance": f"EDITED_{variant}"}
    if variant == "writer":
        texts["writer_scope"] = "EDITED_SCOPE"
    spec, snapshot, plan = bind(texts=texts, variant=variant)
    wire, _ = render(spec, snapshot, plan, action)
    assert f"EDITED_{variant}" in wire.system_prompt
    if variant == "writer":
        assert "EDITED_SCOPE" in wire.system_prompt
        assert output.WRITER_SCOPE not in wire.system_prompt


def test_legacy_dispatch_and_contract_remain_identical():
    spec, snapshot, plan = fixture()
    assert contract.contract_for(spec, snapshot) == creative_contract.contract_for(spec, snapshot)
    for action in ("plan", "write:1", "memory:1", "rewrite", "checker", "title"):
        assert contract.render_for(spec, snapshot, action, plan) == creative_contract.render_for(
            spec, snapshot, action, plan
        )
    assert configurable_cast.parse_stage(
        json_text(plan), spec, snapshot
    ) == creative_cast.parse_stage(json_text(plan), spec, snapshot)


def test_rules_only_store_history_stale_writes_and_old_clients_preserve_rules(tmp_path):
    store = CraftTemplateStore(tmp_path / "provider.json")
    original = store.current()
    value = rules.ProgramSettings(texts={"creative_guidance": "作者指导"}, maximum_new_characters=5)
    saved = store.save("chief", None, original["revision"], "规则修改", value, True)
    assert not saved["templates"] and rules.limit(saved) == 5
    assert store.version(saved["revision"]) == saved
    with pytest.raises(ConflictError):
        store.save("chief", None, original["revision"], "过期", value, True)
    newer = store.save("chief", default_text("chief"), saved["revision"], "仅改底稿")
    assert newer[rules.SETTINGS] == saved[rules.SETTINGS]
    reset = store.save("chief", None, newer["revision"], "恢复", None, True)
    assert rules.limit(reset) == 3 and rules.SETTINGS not in reset
    assert store.version(saved["revision"]) == saved


def test_validation_and_tampered_new_contract_fail_locally():
    with pytest.raises(WorkflowError, match="未知"):
        rules.validate("chief", rules.ProgramSettings(texts={"arbitrary": "text"}))
    with pytest.raises(WorkflowError, match="Chief"):
        rules.validate("writer", rules.ProgramSettings(maximum_new_characters=5))
    for maximum in (-1, 13, 1.5, True):
        with pytest.raises(ValueError):
            rules.ProgramSettings(maximum_new_characters=maximum)
    spec, snapshot, plan = bind()
    bad = deepcopy(snapshot)
    bad[contract.KEY]["sha256"] = "changed"
    with pytest.raises(WorkflowError, match="冻结"):
        render(spec, bad, plan)
