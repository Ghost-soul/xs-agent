# ruff: noqa: F811
import json

import pytest

from novel_writer.generation.budget import input_tokens, request_preview
from novel_writer.providers.base import ModelRequest
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_automation import automated, settle  # noqa: F401
from tests.integration.test_generation_feedback import feedback_run  # noqa: F401
from tests.integration.test_generation_longform import longform, stage_create  # noqa: F401
from tests.integration.test_genre_generation import generation, post, read, start  # noqa: F401
from tests.integration.test_narrative_prompts import options
from tests.integration.test_novel_run_rebuild import current


@pytest.mark.parametrize("size,count", [(1500, 3), (8500, 1), (300, 6)])
def test_unit_scope_runs_and_adopts_complete_units_regardless_of_length(feedback_run, size, count):
    client, control = feedback_run
    control["unit_size"] = size
    base, draft = stage_create(
        client,
        **options(
            length_policy="unit-v1",
            plan_policy="bounded-v1",
            unit_limit=count,
            enable_checker=False,
            enable_reader=False,
        ),
    )
    assert draft["spec"]["target_characters"] is None
    assert draft["spec"]["chapter_count"] is None
    batch = start(client, base, draft)
    assert batch["state"].get("units_finished"), batch["state"]
    assert all(c["status"] == "completed" for c in batch["calls"]), batch["state"]
    assert control["calls"] == [
        "plan",
        *[a for n in range(1, count + 1) for a in [f"write:{n}", f"memory:{n}"]],
    ]
    for call in batch["calls"]:
        saved = read(client, f"{base}/{batch['id']}/calls/{call['id']}")["request"]
        request = ModelRequest.model_validate(saved["model_request"])
        payload = json.loads(request.user_prompt)
        assert (
            not {"unit_target_characters", "total_target_characters", "estimated_chapters"}
            & payload.keys()
        )
        assert saved["input_tokens"] == input_tokens(request_preview(request), saved["counting"])
    body = current(batch, "candidate")["payload"]["body"]
    manifest = current(batch, "segments")["payload"]
    assert manifest["tail"] is None and len(manifest["segments"]) == count
    assert "".join(body[s["start"] : s["end"]] for s in manifest["segments"]) == body
    suggestions = read(client, f"{base}/{batch['id']}/stage-chapters")
    data = {
        "chapters": [
            {
                "chapter_id": c["id"],
                "title": "单元完成",
                "facts_confirmed": True,
                "narrative_position": c["position"],
                "factual_changes": c["factual_changes"],
            }
            for c in suggestions["chapters"]
        ]
    }
    preview = post(client, f"{base}/{batch['id']}/stage-adoption-preview", data)
    assert preview.status_code == 200, preview.text
    adopted = post(
        client,
        f"{base}/{batch['id']}/stage-adopt",
        {
            **data,
            "confirmed": True,
            "preview_sha256": preview.json()["preview_sha256"],
        },
    )
    assert adopted.status_code == 200, adopted.text
    assert read(client, f"{base}/{batch['id']}")["status"] == "adopted"


def test_truncated_unit_still_pauses_and_preserves_prose(feedback_run):
    client, control = feedback_run
    control.update(unit_size=1500, incomplete_action="write:1")
    base, draft = stage_create(client, **options(length_policy="unit-v1", plan_policy="bounded-v1"))
    batch = start(client, base, draft)
    assert batch["status"] == "needs_attention"
    assert control["calls"] == ["plan", "write:1"]
    assert current(batch, "candidate")["payload"]["body"]
    assert current(batch, "units")["payload"]["items"][0]["complete"] is False


def test_independent_rewrite_of_historical_stage_binds_new_unit_scope(feedback_run):
    client, control = feedback_run
    base, draft = stage_create(client, **options(enable_checker=False, enable_reader=False))
    original = start(client, base, draft)
    target = f"{base}/{original['id']}"
    preview = post(
        client,
        target + "/amendment-preview",
        {
            "candidate_sha256": current(original, "candidate")["sha256"],
            "mode": "rewrite",
            "instruction": "完整展开当前事件，不新增后续剧情。",
            "max_cost_cny": "1",
            "output_limit": 12000,
            "enable_checker": False,
            "enable_reader": False,
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["request"]["length_policy"] == "unit-v1"
    authorized = post(
        client,
        target + "/amendment-authorize",
        {
            "confirmed": True,
            "preview_sha256": preview.json()["preview_sha256"],
        },
    )
    assert authorized.status_code == 200, authorized.text
    done = settle(client, base, original["id"])
    assert done["spec"] == original["spec"] and done["snapshot"] == original["snapshot"]
    assert all(c["status"] == "completed" for c in done["calls"]), done["state"]
    assert "unit_target_characters" not in control["requests"]["rewrite"]
    segments = current(done, "segments")["payload"]
    assert len(segments["segments"]) == 1 and segments["tail"] is None
