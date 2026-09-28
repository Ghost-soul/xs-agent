"""Forward-moving event guidance for new stages, outside all released renderers."""

import inspect
import sys
from typing import Any

from novel_writer.generation import editable_rules, progression_rules
from novel_writer.generation import trial_prompts as previous
from novel_writer.generation.content import fingerprint
from novel_writer.generation.craft_models import enabled
from novel_writer.generation.prompt_templates import variant_for
from novel_writer.generation.schemas import GenerationSpec
from novel_writer.providers.base import ModelRequest
from novel_writer.services.errors import WorkflowError
from novel_writer.services.provider_profiles import ProviderProfile

KEY = "progression_contract"
REVISION = "event-progression-v1"


def binding() -> dict[str, str]:
    return {"revision": REVISION, "sha256": fingerprint({
        "source": inspect.getsource(sys.modules[__name__]),
        "rules": inspect.getsource(progression_rules),
    })}


def checked(snapshot: dict[str, Any], reports: dict[str, Any] | None = None) -> bool:
    policy = (reports or {}).get(KEY, snapshot.get(KEY))
    if policy is None:
        return False
    if policy != binding():
        raise WorkflowError("事件推进合同与冻结授权不符，请重新预览；原请求不升级")
    return True


def bind_snapshot(spec: GenerationSpec, snapshot: dict[str, Any]) -> None:
    if enabled(spec) and spec.stage_mode == "longform-v1":
        snapshot[KEY] = binding()


def contract_for(spec: GenerationSpec, snapshot: dict[str, Any] | None = None) -> str:
    base = previous.contract_for(spec, snapshot)
    return fingerprint({"base": base, KEY: binding()}) if checked(snapshot or {}) else base


def _settings(reports: dict[str, Any], action: str) -> None:
    variant = variant_for(action)
    value = editable_rules.ProgramSettings.model_validate(
        reports.get(editable_rules.RECEIPT, {}).get(variant, {}),
    )
    reports[editable_rules.RECEIPT] = {
        variant: progression_rules.effective(variant, value).model_dump(exclude_none=True),
    }
    reports[KEY] = binding()


def render_for(
    spec: GenerationSpec, snapshot: dict[str, Any], action: str,
    plan: dict[str, Any] | None = None, body: str | None = None,
    author_note: str | None = None, reports: dict[str, Any] | None = None,
) -> tuple[str, str]:
    reports = reports if reports is not None else {}
    active = checked(snapshot, reports)
    rendered = previous.render_for(spec, snapshot, action, plan, body, author_note, reports)
    if active:
        _settings(reports, action)
    return rendered


def prepare_output(
    request: ModelRequest, profile: ProviderProfile, action: str,
    snapshot: dict[str, Any], reports: dict[str, Any],
) -> ModelRequest:
    if checked(snapshot, reports):
        _settings(reports, action)
    # The editable suffix is generated once, before final capacity accounting.
    # Existing author system/task text and all output structures remain intact.
    return previous.prepare_output(request, profile, action, snapshot, reports)
