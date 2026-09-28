"""Read-only guards for old successes whose selected artifacts contain refusals."""

from novel_writer.db.models import (
    GenerationArtifactRecord,
    GenerationBatchRecord,
    GenerationCallRecord,
)
from novel_writer.generation.content import digest
from novel_writer.generation.novel import role_for
from novel_writer.generation.output_failures import refusal_message


def refusal_blocker(
    batch: GenerationBatchRecord,
    calls: list[GenerationCallRecord],
    artifacts: list[GenerationArtifactRecord],
) -> str | None:
    refused = {str(c.id): c for c in calls if c.status == "completed" and refusal_message(c)}
    if not refused:
        return None
    by_id = {str(a.id): a for a in artifacts if a.batch_id == batch.id}
    plan = by_id.get(batch.state.get("plan_id", ""))
    candidate = by_id.get(batch.state.get("candidate_id", ""))
    units = by_id.get(batch.state.get("units_id", ""))
    body = candidate.payload.get("body", "") if candidate else ""
    items = units.payload.get("items", []) if units else []
    invalid = set()
    for ident, call in refused.items():
        raw = (call.response or {}).get("text", "")
        if role_for(call.action) == "chief" and plan and plan.payload.get("raw_response") == raw:
            invalid.add(ident)
        if candidate and candidate.payload.get("source_call_id") == ident:
            invalid.add(ident)
        for unit in items:
            start, end = unit.get("start"), unit.get("end")
            if not (
                isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(body)
            ):
                continue
            piece = body[start:end]
            if digest(piece) != unit.get("body_sha256"):
                continue  # An author replacement no longer uses this old unit.
            if unit.get("source_call_id") == ident and piece.strip() == raw.strip():
                invalid.add(ident)
            note = by_id.get(unit.get("note_id", ""))
            if note and note.payload.get("source_call_id") == ident:
                invalid.add(ident)
    if not invalid:
        return None
    actions = "、".join(call.action for call in calls if str(call.id) in invalid)
    return (
        f"当前成果仍引用模型拒绝说明（{actions}），不能当作有效方案、正文或笔记继续。"
        "请先更换无效成果或新建阶段；仅重试后续步骤不能修复。原响应和费用记录保留。"
    )
