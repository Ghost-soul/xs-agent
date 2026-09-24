"""Identical binding rules for live persistence and explicit buffer relocation."""

from datetime import datetime
from typing import Any
from uuid import UUID

from novel_writer.db.models import GenerationBatchRecord, GenerationCallRecord
from novel_writer.generation.content import fingerprint
from novel_writer.services.errors import WorkflowError


def validate_response_binding(
    batch: GenerationBatchRecord | None,
    call: GenerationCallRecord | None,
    entry: dict[str, Any],
) -> None:
    batch_id, call_id = UUID(entry["batch_id"]), UUID(entry["call_id"])
    datetime.fromisoformat(entry["received_at"])
    raw = entry["response"]
    if raw.get("sha256") != fingerprint({k: v for k, v in raw.items() if k != "sha256"}):
        raise WorkflowError("响应缓冲摘要不一致")
    if (
        batch is None
        or call is None
        or batch.id != batch_id
        or call.id != call_id
        or call.batch_id != batch_id
        or call.request.get("batch_preview_sha256") != batch.preview_sha256
        or fingerprint(call.request.get("model_request")) != entry["model_request_sha256"]
    ):
        raise WorkflowError("响应缓冲与原调用绑定不一致，拒绝覆盖")
    if call.response is not None:
        if call.response != raw:
            raise WorkflowError("原调用已有不同响应，拒绝覆盖")
        return
    if call.status != "executing" and not (
        call.status == "outcome_uncertain" and call.error_code == "process_interrupted"
    ):
        raise WorkflowError("原调用已另行处理，缓冲响应保留待核对")
