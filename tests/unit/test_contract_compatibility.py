import ast
import inspect
from copy import deepcopy

import pytest

from novel_writer.generation import budget, format_trial, reliability_contract, trial_prompts
from novel_writer.generation import contract_compatibility as compat
from novel_writer.generation.content import digest, fingerprint
from tests.unit.test_editable_rules import bind
from tests.unit.test_event_units import fixture


def historical_contract(monkeypatch, spec, snapshot, *, amendment=False):
    original = inspect.getsource
    legacy = compat.legacy_builder_source()
    assert legacy is not None
    with monkeypatch.context() as old:
        old.setattr(inspect, "getsource", lambda obj: legacy
                    if obj is budget.request_for else original(obj))
        return (reliability_contract.amendment_contract if amendment
                else trial_prompts.contract_for)(spec, snapshot)


@pytest.mark.parametrize("mode", ["event", "craft", "reliability", "trial", "amendment"])
def test_diagnostic_only_change_preserves_both_generations_without_mutation(monkeypatch, mode):
    spec, snapshot, _ = fixture() if mode == "event" else bind()
    if mode in {"reliability", "trial", "amendment"}:
        reliability_contract.bind_snapshot(spec, snapshot)
    if mode == "trial":
        format_trial.bind_snapshot(spec, snapshot)
    before = deepcopy(snapshot)
    old = historical_contract(monkeypatch, spec, snapshot, amendment=mode == "amendment")
    assert old != trial_prompts.contract_for(spec, snapshot)
    assert compat.matches(spec, snapshot, old, amendment=mode == "amendment")
    assert compat.matches(spec, snapshot, trial_prompts.contract_for(spec, snapshot))
    assert not compat.matches(spec, snapshot, "0" * 64)
    assert snapshot == before


def test_exactly_one_raise_message_changed_not_request_construction():
    current = ast.parse(inspect.getsource(budget.request_for))
    legacy = ast.parse(compat.legacy_builder_source())
    current_raise = next(n for n in ast.walk(current) if isinstance(n, ast.Raise))
    old_raise = next(n for n in ast.walk(legacy) if isinstance(n, ast.Raise))
    assert isinstance(current_raise.exc, ast.Call) and isinstance(old_raise.exc, ast.Call)
    assert current_raise.exc.func.id == old_raise.exc.func.id == "WorkflowError"
    current_raise.exc.args = old_raise.exc.args
    assert ast.dump(current) == ast.dump(legacy)
    assert digest(compat.legacy_builder_source()) == compat.LEGACY_BUILDER_SHA


def test_builder_or_other_prompt_changes_are_not_whitelisted(monkeypatch):
    spec, snapshot, _ = bind()
    old = historical_contract(monkeypatch, spec, snapshot)
    original = inspect.getsource
    with monkeypatch.context() as changed:
        changed.setattr(inspect, "getsource", lambda obj: original(obj).replace(
            'else "none"', 'else "medium"',
        ) if obj is budget.request_for else original(obj))
        assert compat.legacy_builder_source() is None
        assert not compat.matches(spec, snapshot, old)
    with monkeypatch.context() as changed:
        changed.setattr(inspect, "getsource", lambda obj: original(obj) + "\n# changed prompt\n"
                        if obj is compat.craft_prompts else original(obj))
        assert not compat.matches(spec, snapshot, old)


def test_different_author_template_is_not_accepted_as_legacy(monkeypatch):
    spec, snapshot, _ = bind()
    old = historical_contract(monkeypatch, spec, snapshot)
    bundle = snapshot["prompt_templates"]
    bundle["revision"] = "different-author-template"
    bundle["sha256"] = fingerprint({k: v for k, v in bundle.items() if k != "sha256"})
    assert not compat.matches(spec, snapshot, old)


def test_future_dispatcher_recipe_drift_fails_closed(monkeypatch):
    spec, snapshot, _ = bind()
    old = historical_contract(monkeypatch, spec, snapshot)
    monkeypatch.setattr(trial_prompts, "contract_for", lambda *args: "future-recipe")
    assert not compat.matches(spec, snapshot, old)
