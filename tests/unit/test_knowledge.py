import json
from copy import deepcopy

import pytest

from novel_writer.generation import knowledge_context, world_context
from novel_writer.generation.content import fingerprint
from novel_writer.knowledge.text import chunks, corpus_hash, queries, rank, source
from novel_writer.services.errors import WorkflowError
from tests.unit.test_world_context import setup


def test_chunk_offsets_survive_chinese_paragraphs_and_only_changed_inputs_need_embeddings():
    body = "旧伤未愈。" * 100 + "\n\n" + "她把剑交给同伴。" * 80
    before = chunks([source("chapter", "revision1", body)])
    assert all(p["text"] == body[p["start"] : p["end"]] for p in before)
    after = chunks([source("chapter", "revision2", body + "新的决定。")])
    assert len({p["text_sha256"] for p in before} & {p["text_sha256"] for p in after}) > 0
    assert {p["id"] for p in before}.isdisjoint(p["id"] for p in after)


def test_multi_query_recall_and_overlapping_dedup():
    pieces = chunks(
        [
            source("chapter", "a", "师父把玄铁剑交给她。" * 80),
            source("chapter", "b", "她的腿伤仍然没有痊愈。"),
            source("chapter", "c", "店主关上了店门。"),
        ]
    )
    hits = rank(pieces, ["玄铁剑", "腿伤痊愈"])
    assert {"a", "b"} <= {h["source_id"] for h in hits}
    assert "c" not in {h["source_id"] for h in hits}
    for i, hit in enumerate(hits):
        assert not any(
            old["source_id"] == hit["source_id"]
            and max(old["start"], hit["start"]) < min(old["end"], hit["end"])
            for old in hits[:i]
        )


def test_lexical_cache_cannot_hide_changed_text_and_has_a_global_bound():
    from novel_writer.knowledge import text as module

    module._lexical_cache.clear()
    original = chunks([source("chapter", "same-source", "她将玄铁剑交给师父。")])
    assert rank(original, ["玄铁剑"])
    changed = [{**original[0], "text": "新的正文只剩白雪。"}]
    assert rank(changed, ["玄铁剑"]) == []
    assert rank(changed, ["白雪"])
    rank(chunks([source("chapter", "other", "河边旧屋")]), ["旧屋"])
    assert len(module._lexical_cache) <= 2


def test_writer_queries_only_current_plan_event():
    plan = {"scenes": [{"event": "渡河"}, {"event": "远征"}]}
    assert "渡河" in queries("任务", "write:1", plan, "结尾")[0]
    assert "远征" not in str(queries("任务", "write:1", plan, "结尾"))


@pytest.mark.parametrize("action", ["plan", "write:1", "memory:1", "checker", "rewrite"])
def test_bounded_retrieval_preserves_plan_voice_world_dependencies_and_evidence(action):
    spec, snapshot, plan = setup()
    spec = spec.model_copy(update={"context_policy": knowledge_context.POLICY})
    snapshot["knowledge_sources"] = [
        source("chapter", f"revision-{n}", "她在渡口约定还剑。" * 100) for n in range(30)
    ]
    original = deepcopy(snapshot)
    body = "实际单元证据，未检索片段不可冒充。"
    system, raw = knowledge_context.render_for(spec, snapshot, action, plan, body)
    payload = json.loads(raw)
    material = payload["knowledge_context"]
    assert material["history_count"] <= material["history_limit"]
    assert snapshot == original
    assert "省略不表示不存在" in system and "本次正文的证据" in system
    assert "回潮契约" in raw and "潮汐出处" in raw
    if action == "write:1":
        assert payload["effective_plan"] == plan
        assert "完整声音样本" in raw
    if action in {"memory:1", "checker"}:
        assert body in raw


def test_old_world_contract_and_render_are_unchanged():
    spec, snapshot, plan = setup()
    assert knowledge_context.contract_for(spec) == world_context.contract_for(spec)
    assert knowledge_context.render_for(
        spec, snapshot, "write:1", plan
    ) == world_context.render_for(
        spec,
        snapshot,
        "write:1",
        plan,
    )


@pytest.mark.parametrize("tamper", ["project", "version", "text", "sha"])
def test_receipt_is_bound_to_novel_version_and_original_text(tamper):
    spec, snapshot, plan = setup()
    spec = spec.model_copy(update={"context_policy": knowledge_context.POLICY})
    snapshot["knowledge_project_id"] = "novel-a"
    snapshot["knowledge_sources"] = [source("chapter", "revision", "渡口的约定。")]
    receipt = knowledge_context.retrieval_for(spec, snapshot, "plan", plan, None, {})
    if tamper == "project":
        receipt["project_id"] = "novel-b"
    elif tamper == "version":
        receipt["version_id"] = "other-version"
    elif tamper == "text":
        receipt["hits"][0]["text"] = "不属于此来源的事实"
    else:
        receipt["corpus_sha256"] = "0" * 64
    receipt["sha256"] = fingerprint({k: v for k, v in receipt.items() if k != "sha256"})
    with pytest.raises(WorkflowError, match="检索"):
        knowledge_context.render_for(
            spec, snapshot, "plan", plan, reports={"knowledge_retrieval": receipt}
        )


def test_world_semantic_hit_is_provided_as_complete_rule_with_dependencies():
    spec, snapshot, plan = setup()
    spec = spec.model_copy(update={"context_policy": knowledge_context.POLICY})
    snapshot["context"]["world_lore"].append(
        {
            "id": "rare",
            "name": "霜叶",
            "summary": "没有词项重合",
            "category": "society",
            "risk_level": "low",
            "details": ["仅在月圆开放，须有旧印。"],
        }
    )
    sources = knowledge_context.frozen_sources(snapshot)
    hit = next(p for p in chunks(sources) if p["source_id"] == "rare")
    receipt = {
        "project_id": None,
        "version_id": str(spec.base_version_id),
        "corpus_sha256": corpus_hash(sources),
        "mode": "hybrid",
        "reason": "ready",
        "hits": [{**hit, "channels": ["semantic"]}],
    }
    receipt["sha256"] = fingerprint(receipt)
    _, raw = knowledge_context.render_for(
        spec, snapshot, "write:1", plan, reports={"knowledge_retrieval": receipt}
    )
    assert "仅在月圆开放，须有旧印" in raw
