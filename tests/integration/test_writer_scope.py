# ruff: noqa: F401 F811
"""Short outputs remain saved; targets and request replay remain correctly bound."""

from novel_writer.generation.budget import input_tokens, request_preview
from novel_writer.generation.content import digest
from novel_writer.generation.output_contract_v3 import KEY, REVISION
from novel_writer.generation.progression_rules import WRITER_SCOPE
from novel_writer.generation.runtime import GenerationRuntime
from novel_writer.providers.base import ModelRequest
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_automation import automated
from tests.integration.test_generation_longform import longform
from tests.integration.test_genre_generation import generation, read, start
from tests.integration.test_novel_run_rebuild import current
from tests.integration.test_stage_craft import craft, create_craft


def test_short_two_unit_stage_shows_frozen_target_without_retry_or_rewriting(craft, monkeypatch):
    client, control = craft
    control.update(plan_count=2, unit_size=999)
    original = GenerationRuntime.dispatch

    async def dispatch(self, call_id, request, profile, spec, counting, api_key):
        response = await original(self, call_id, request, profile, spec, counting, api_key)
        if control["calls"][-1].startswith("write:"):
            return response.model_copy(update={
                "text": response.text + "<|eos|>",
                "usage": response.usage.model_copy(update={"reasoning_tokens": 1300}),
            })
        return response

    monkeypatch.setattr(GenerationRuntime, "dispatch", dispatch)
    base, draft = create_craft(client, unit_limit=3)
    done = start(client, base, draft)
    assert all(c["status"] == "completed" for c in done["calls"]), done["state"]
    assert control["calls"] == ["plan", "write:1", "memory:1", "write:2", "memory:2"]
    assert done["state"]["units_finished"]
    reports = done["state"]["writer_unit_status"]
    assert len(reports) == 2
    body = current(done, "candidate")["payload"]["body"]
    for call, report in zip([c for c in done["calls"] if c["action"].startswith("write:")],
                            reports, strict=True):
        receipt = read(client, f"{base}/{done['id']}/calls/{call['id']}")
        saved = receipt["request"]
        request = ModelRequest.model_validate(saved["model_request"])
        assert "当前单元目标 7,500–10,000 字" in request.system_prompt
        assert request.system_prompt.count(WRITER_SCOPE) == 1
        assert saved[KEY]["revision"] == REVISION
        assert saved["input_tokens"] == input_tokens(request_preview(request), saved["counting"])
        assert saved["writer_scale"]["current_unit_reference"] == report["target"]
        assert receipt["response"]["text"] in body  # No marker stripping or auto expansion.
        assert report["body_sha256"] == digest(receipt["response"]["text"])
        assert report["characters"] == 999 and report["saved_characters"] == 1006
        assert report["status"] == "below" and report["percent_of_minimum"] == 13.3
        assert report["reasoning_tokens"] == 1300 and report["terminal_marker"] == "<|eos|>"
        assert call["writer_output"]["target"] == report["target"]
    again = read(client, f"{base}/{done['id']}")
    assert again["state"]["writer_unit_status"] == reports
    assert len(control["calls"]) == 5
