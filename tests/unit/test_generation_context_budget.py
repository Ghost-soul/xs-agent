import json
from copy import deepcopy

import pytest
from tokenizers import Tokenizer, models, pre_tokenizers

from novel_writer.generation import budget, tokenizer_assets
from novel_writer.generation.content import digest, fingerprint
from novel_writer.generation.context_budget import contract_for, fit_context, render_for
from novel_writer.generation.intent import contract_for as old_contract
from novel_writer.generation.intent import render_for as old_render
from novel_writer.generation.schemas import RoleModel
from novel_writer.services.errors import WorkflowError
from novel_writer.services.provider_profiles import ProviderModelOption, ProviderProfile
from tests.unit.test_generation_casting import automatic, roster, snapshot
from tests.unit.test_genre_generation import plan


def profile():
    return ProviderProfile(
        id="fixture",
        display_name="fixture",
        protocol="deepseek_chat",
        base_url="https://example.test",
        default_model="test",
        models=[
            ProviderModelOption(
                id="test",
                context_window=100000,
                max_output_tokens=24000,
                input_price_cny_per_million=0,
                output_price_cny_per_million=0,
            )
        ],
    )


def install(tmp_path, monkeypatch):
    monkeypatch.setattr(budget, "TOKENIZER_ROOT", tmp_path)
    monkeypatch.setattr(tokenizer_assets, "TOKENIZER_ROOT", tmp_path)
    t = Tokenizer(models.WordLevel({"[UNK]": 0, "word": 1}, unk_token="[UNK]"))
    t.pre_tokenizer = pre_tokenizers.Whitespace()
    t.save(str(tmp_path / "exact.json"))
    (tmp_path / "exact.manifest.json").write_text(
        json.dumps(
            {
                "models": ["test"],
                "sha256": digest((tmp_path / "exact.json").read_text()),
                "source": "offline test fixture only",
            }
        )
    )


def test_tokenizer_selection_only_uses_exact_verified_installed_models(tmp_path, monkeypatch):
    install(tmp_path, monkeypatch)
    old = automatic()
    assert tokenizer_assets.resolve_tokenizers(old) is old
    new = automatic(
        context_policy="bounded-v1", roles={"reader": RoleModel(model="test", output_limit=6000)}
    )
    resolved = tokenizer_assets.resolve_tokenizers(new)
    assert resolved.chief_tokenizer_id == resolved.writer_tokenizer_id == "exact"
    assert resolved.roles["reader"].tokenizer_id == "exact"
    assert new.chief_tokenizer_id is None
    other = new.model_copy(update={"writer_model": "different"})
    assert tokenizer_assets.resolve_tokenizers(other).writer_tokenizer_id is None
    (tmp_path / "exact.json").write_text("{}")
    with pytest.raises(WorkflowError, match="SHA"):
        tokenizer_assets.resolve_tokenizers(new)


def test_explicit_tokenizer_failure_does_not_silently_fallback(tmp_path, monkeypatch):
    install(tmp_path, monkeypatch)
    with pytest.raises(WorkflowError, match="不存在"):
        tokenizer_assets.resolve_tokenizers(
            automatic(context_policy="bounded-v1", chief_tokenizer_id="missing")
        )


def test_current_scene_events_and_cards_survive_budget_while_old_objects_stay_local():
    s = automatic(
        context_policy="bounded-v1",
        stage_mode="longform-v1",
        unit_limit=2,
        relationship_scope="genre-led",
        input_limit=20000,
    )
    snap = snapshot(s, roster())
    ctx = snap["context"]
    cards = deepcopy(snap["cards"])
    current = {"id": "now", "summary": "当前事件，不能省略"}
    old = {"id": "old", "summary": "旧事件" * 5000}
    current_scene = {
        "id": "scene-now",
        "chapter_id": "latest",
        "event_ids": ["now"],
        "summary": "当前现场",
    }
    old_scene = {
        "id": "scene-old",
        "chapter_id": "old",
        "event_ids": ["old"],
        "summary": "旧场景" * 5000,
    }
    ctx.update(
        critical_facts=[old, current],
        recent_events=[],
        related_state={"scenes": [old_scene, current_scene]},
        future_material_not_obligations={
            "plot_threads": [{"id": "old-plan", "goal": "旧计划" * 5000}],
            "open_questions": [],
        },
        world_rules=[{"id": "rule", "description": "不能更改的世界规则"}],
    )
    snap["counting"] = {"chief": budget.counting_config(None, s.chief_model)}
    fit_context(s, snap, profile(), "latest")
    assert snap["cards"] == cards
    assert ctx["critical_facts"] == [current]
    assert ctx["related_state"]["scenes"] == [current_scene]
    assert ctx["world_rules"][0]["description"] == "不能更改的世界规则"
    assert old_scene in snap["context_archive"]["1:scenes"]
    assert snap["context_budget"]["source_sha256"] == fingerprint(snap["context_archive"])
    _, rendered = render_for(s, snap, "plan")
    assert "旧场景旧场景" not in rendered
    assert "当前事件，不能省略" in rendered and "省略不等于未发生" in rendered


def test_handoff_keeps_new_facts_without_reintroducing_omitted_history_or_duplicating_records():
    s = automatic(
        context_policy="bounded-v1",
        stage_mode="longform-v1",
        unit_limit=2,
        relationship_scope="genre-led",
    )
    snap = snapshot(s, roster())
    snap["context"].update(
        critical_facts=[{"id": "required", "summary": "必要旧事实"}],
        recent_chapters=[{"body": "正式前章正文"}],
    )
    p = plan([c["id"] for c in snap["context"]["characters"]][:2])
    created = {"id": "new", "summary": "本阶段实际新事实"}
    reports = {
        "completed_units": 1,
        "working_context": {
            "characters": snap["context"]["characters"],
            "recent_events": [{"id": "omitted", "summary": "不应重新膨胀的旧事实"}, created],
            "related_state": {"events": [created], "characters": snap["context"]["characters"]},
        },
    }
    original = deepcopy(reports)
    _, writer = render_for(s, snap, "write:2", p, "完整本阶段正文", reports=reports)
    payload = json.loads(writer)
    assert "不应重新膨胀的旧事实" not in writer
    assert writer.count("本阶段实际新事实") == 1
    assert payload["already_written"] == "完整本阶段正文"
    assert payload["formal_reference"]["recent_chapters"] == []
    assert reports == original and snap["context"]["recent_chapters"]
    _, reader = render_for(s, snap, "reader", p, "完整本阶段正文", reports=reports)
    assert "正式前章正文" in reader and "必要旧事实" not in reader
    _, rewrite = render_for(s, snap, "rewrite", p, "完整本阶段正文", reports=reports)
    assert "正式前章正文" in rewrite


def test_old_context_contract_and_render_stay_unchanged():
    s = automatic(stage_mode="longform-v1", relationship_scope="genre-led")
    snap = snapshot(s, roster())
    assert contract_for(s) == old_contract(s)
    assert render_for(s, snap, "plan") == old_render(s, snap, "plan")
    old = deepcopy(snap)
    fit_context(s, snap, profile(), None)
    assert snap == old
