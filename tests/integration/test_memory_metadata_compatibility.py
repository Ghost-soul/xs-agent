# ruff: noqa: F811
import json
from uuid import UUID

import pytest

from novel_writer.generation import diagnostics, reports, runtime
from novel_writer.generation.novel import parser_for, role_for
from novel_writer.generation.runtime import GenerationRuntime
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_automation import automated  # noqa: F401
from tests.integration.test_generation_longform import longform  # noqa: F401
from tests.integration.test_genre_generation import generation, post, read, start  # noqa: F401
from tests.integration.test_memory_evidence_repair import draft_for, repaired  # noqa: F401
from tests.integration.test_novel_run_rebuild import current, novel_generation  # noqa: F401


@pytest.fixture
def annotated(repaired, monkeypatch):
    client, control = repaired
    previous = GenerationRuntime.dispatch

    async def dispatch(self, call_id, request, profile, spec, counting, api_key):
        response = await previous(self, call_id, request, profile, spec, counting, api_key)
        if control["calls"][-1].startswith("memory"):
            data = json.loads(response.text)
            data.update(base_version=110, evidence=[])
            response = response.model_copy(update={"text": json.dumps(data)})
        return response

    monkeypatch.setattr(GenerationRuntime, "dispatch", dispatch)
    return client, control


def test_new_memory_annotations_do_not_interrupt_creation(annotated):
    client, control = annotated
    base, draft = draft_for(client, control)
    batch = start(client, base, draft)
    assert all(c["status"] == "completed" for c in batch["calls"]), batch["state"]
    kind = "handoff" if control["longform"] else "memory"
    report = current(batch, kind)["payload"]
    assert report["status"] == "complete" and not report["diagnostics"]
    assert report["format_notes"][0]["raw"] == 110
    assert report["format_notes"][0]["bound_base_version"] == 1
    assert current(batch, "candidate")["payload"]["body"]


@pytest.mark.parametrize("invalid_evidence", [False, True])
def test_failed_v2_report_can_be_revalidated_once_locally(annotated, monkeypatch, invalid_evidence):
    client, control = annotated
    control["foreign_position"] = invalid_evidence
    base, draft = draft_for(client, control)

    def old_parser(revision, action=None):
        return (
            "memory-evidence-v2"
            if action and role_for(action) == "memory"
            else parser_for(revision, action)
        )

    with monkeypatch.context() as old:
        old.setattr(reports, "normalize_memory_metadata", lambda original, base: (original, []))
        old.setattr(runtime, "parser_for", old_parser)
        old.setattr(diagnostics, "parser_for", old_parser)
        batch = start(client, base, draft)
    call = next(c for c in batch["calls"] if c["action"].startswith("memory"))
    assert call["status"] == "local_failure" and "base_version" in batch["state"]["message"]
    target = f"{base}/{batch['id']}"
    route = target + f"/calls/{call['id']}"
    before = read(client, route)
    count = len(control["calls"])
    assert next(c for c in read(client, target)["calls"] if c["id"] == call["id"])["can_revalidate"]
    result = post(client, route + "/revalidate", {"confirmed": True})
    assert result.status_code == 200 and result.json()["provider_requests"] == "0", result.text
    after = read(client, target)
    saved = read(client, route)
    assert saved["request"] == before["request"] and saved["response"] == before["response"]
    assert len(control["calls"]) == count
    assert current(after, "candidate") == current(batch, "candidate")
    assert after["spec"] == batch["spec"] and after["snapshot"] == batch["snapshot"]
    latest = next(c for c in after["calls"] if c["id"] == call["id"])
    assert latest["actual_cost_cny"] == call["actual_cost_cny"]
    assert latest["status"] == ("local_failure" if invalid_evidence else "completed")
    assert f"{call['id']}:memory-evidence-v2" in after["state"]["compiled"]
    assert f"{call['id']}:memory-evidence-v4" in after["state"]["compiled"]
    assert after["status"] not in {"queued", "running"}
    assert post(client, route + "/revalidate", {"confirmed": True}).status_code == 409
    if not invalid_evidence:
        with monkeypatch.context() as upgraded:
            upgraded.setattr(runtime, "parser_for", lambda *_: "future-parser")
            client.portal.call(
                client.app.state.generation.compile_response, UUID(batch["id"]), UUID(call["id"])
            )
        assert read(client, target) == after
