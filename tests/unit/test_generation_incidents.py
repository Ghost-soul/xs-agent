from copy import deepcopy

import pytest

from novel_writer.generation.output_failures import (
    failure_diagnostic,
    refusal_message,
    replay_blocker,
)
from tests.unit.test_output_reliability import call


@pytest.mark.parametrize("action", ["plan", "write:1", "rewrite", "memory:1", "checker"])
@pytest.mark.parametrize("body", [
    "**I must decline to generate this content.** The request cannot be fulfilled.",
    "**I must decline this request.**\n\nI cannot provide the requested content.",
    "I must decline to generate this creative plan.\nPlease revise the request.",
])
def test_explicit_service_refusal_is_not_an_output_format_failure(action, body):
    failed = call(body, action)
    before = deepcopy(failed.response)
    assert refusal_message(failed)
    assert failure_diagnostic(failed)["code"] == "provider_refusal"
    assert replay_blocker(failed)
    assert failed.response == before


@pytest.mark.parametrize("body", [
    '“I must decline this request,” she said, closing the door.',
    'The letter read:\n**I must decline this request.**',
    'I must decline the invitation. The rain had already started.',
    '**I must decline this request.** she said, closing the door.',
    'I cannot write that name. He put down his pen.',
])
def test_story_dialogue_is_not_a_service_refusal(body):
    assert refusal_message(call(body, "write:1")) is None


def test_completed_historical_refusal_has_read_only_diagnostic():
    saved = call("**I must decline this request.**", "write:1")
    saved.status, saved.error_code = "completed", None
    before = deepcopy(saved.response)
    assert "历史" in failure_diagnostic(saved)["message"]
    assert saved.status == "completed" and saved.error_code is None
    assert saved.response == before


@pytest.mark.parametrize("status,body,code", [
    ("http_502", '{"error":{"message":"Upstream access forbidden, please contact administrator",'
     '"type":"upstream_error"}}', "provider_access_denied"),
    ("http_401", "", "provider_access_denied"),
    ("http_502", "<html>Bad Gateway</html>", "provider_gateway_error"),
    ("http_504", "", "provider_gateway_error"),
])
def test_gateway_evidence_explains_failure_without_claiming_zero_cost(status, body, code):
    failed = call("", "memory:2", raw_response=body, error_code="outcome_uncertain",
                  terminal={"terminal_event_seen": True, "terminal_status": status})
    failed.status = "outcome_uncertain"
    assert failure_diagnostic(failed)["code"] == code
    assert status[5:] in failure_diagnostic(failed)["message"]
    assert failed.status == "outcome_uncertain"
    # Operators can still explicitly recover after fixing upstream access.
    assert replay_blocker(failed) is None
