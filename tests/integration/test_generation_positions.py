# ruff: noqa: F811
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_automation import automated, settle  # noqa: F401
from tests.integration.test_generation_feedback import feedback_run  # noqa: F401
from tests.integration.test_generation_longform import longform, stage_create  # noqa: F401
from tests.integration.test_genre_generation import generation, post, read, start  # noqa: F401
from tests.integration.test_novel_run_rebuild import current


def test_whole_stage_memory_rebuild_retains_verified_earlier_scene_for_chapter_review(feedback_run):
    client, control = feedback_run
    control["unit_size"] = 1800
    profile = client.app.state.provider_profile_store.get("fixture")
    profile.models[0].max_output_tokens = 100000
    profile.models[0].context_window = 300000
    client.app.state.provider_profile_store.save(profile)
    base, draft = stage_create(
        client, automation_policy="stage-auto-v1", feedback_policy="advisory-v1"
    )
    batch = start(client, base, draft)
    old_units = current(batch, "units")["payload"]["items"]
    route = f"{base}/{batch['id']}"
    preview = post(
        client,
        route + "/amendment-preview",
        {
            "candidate_sha256": current(batch, "candidate")["sha256"],
            "mode": "verify",
            "instruction": "只重建事实",
            "max_cost_cny": "10",
            "enable_checker": False,
            # This case covers the historical length-based chapter projection.
            # Current unit-based rebuilding is covered by test_unit_scope.py.
            "length_policy": "legacy-v1",
        },
    )
    assert preview.status_code == 200, preview.text
    authorized = post(
        client,
        route + "/amendment-authorize",
        {
            "preview_sha256": preview.json()["preview_sha256"],
            "confirmed": True,
        },
    )
    assert authorized.status_code == 200, authorized.text
    rebuilt = settle(client, base, batch["id"])
    assert len(current(rebuilt, "units")["payload"]["items"]) == 1
    suggestions = read(client, route + "/stage-chapters")
    first = suggestions["chapters"][0]
    assert first["position_status"] == "reference"
    assert first["position_source"]["memory_id"] == old_units[0]["memory_id"]
    assert first["position_source"]["end"] <= first["end"]
    assert first["position"]["recent_major_event"] == "完成memory:1"
    final = suggestions["chapters"][-1]
    assert final["position_status"] == "known"
    assert final["position"]["recent_major_event"] == "完成memory_amend"
    assert read(client, route)["calls"] == rebuilt["calls"]
    approval = {
        "chapter_id": first["id"],
        "title": "",
        "narrative_position": first["position"],
        "factual_changes": first["factual_changes"],
        "facts_confirmed": True,
    }
    accepted = post(client, route + "/stage-adoption-preview", {"chapters": [approval]})
    assert accepted.status_code == 200, accepted.text
    approval["narrative_position"] = {**first["position"], "current_location": " "}
    missing = post(client, route + "/stage-adoption-preview", {"chapters": [approval]})
    assert missing.status_code == 400
    assert f"第 {first['ordinal']} 章缺少章末地点" in missing.text
    assert "缺少章末地点、本章实际事件" not in missing.text
