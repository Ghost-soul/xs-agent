"""Lossless author chapter boundaries, independent of Writer units and transport."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, uuid5

from pydantic import Field

from novel_writer.generation.content import digest, fingerprint, paragraphs
from novel_writer.generation.craft_operations import assert_idle
from novel_writer.generation.schemas import StrictModel
from novel_writer.services.errors import ConflictError, WorkflowError

if TYPE_CHECKING:
    from novel_writer.db.models import GenerationBatchRecord
    from novel_writer.generation.service import GenerationService


class ChapterArrangement(StrictModel):
    candidate_sha256: str = Field(min_length=64, max_length=64)
    manifest_sha256: str = Field(min_length=64, max_length=64)
    paragraph_ends: list[str] = Field(default_factory=list, max_length=5)
    preview_sha256: str | None = Field(default=None, min_length=64, max_length=64)


def manifest_for(body: str, paragraph_ends: list[str], namespace: str) -> dict[str, Any]:
    by_id = {p["id"]: p for p in paragraphs(body)}
    if len(set(paragraph_ends)) != len(paragraph_ends) or any(
        p not in by_id for p in paragraph_ends
    ):
        raise WorkflowError("拆章位置须为当前正文中不同的完整自然段末尾")
    ends = [by_id[p]["end"] for p in paragraph_ends]
    if ends != sorted(ends) or any(e >= len(body.rstrip()) for e in ends):
        raise WorkflowError("章界须按正文顺序排列，最后一章无需添加章界")
    ends.append(len(body))
    start = 0
    segments: list[dict[str, Any]] = []
    for end in ends:
        if not body[start:end].strip():
            raise WorkflowError("章节不能只有空白")
        segments.append(
            {
                "id": str(
                    uuid5(NAMESPACE_URL, f"arranged:{namespace}:{digest(body)}:{start}:{end}")
                ),
                "number": len(segments) + 1,
                "start": start,
                "end": end,
                "body_sha256": digest(body[start:end]),
            }
        )
        start = end
    assert "".join(body[s["start"] : s["end"]] for s in segments) == body
    return {
        "body_sha256": digest(body),
        "segments": segments,
        "tail": None,
        "lossless": True,
        "length_policy": "author-paragraphs-v1",
    }


async def preview(
    service: GenerationService, batch: GenerationBatchRecord, request: ChapterArrangement
) -> dict[str, Any]:
    await service.assert_current(batch, dispatch=False)
    if batch.status not in {"ready", "needs_attention", "paused"} or not batch.state.get(
        "units_finished"
    ):
        raise ConflictError("完整阶段结束后才能调整章节；先完成当前写作及接力")
    await assert_idle(service, batch)
    candidate = await service.artifact(batch, "candidate")
    prior = await service.artifact(batch, "segments")
    if (
        not candidate
        or not prior
        or candidate.sha256 != request.candidate_sha256
        or prior.sha256 != request.manifest_sha256
    ):
        raise ConflictError("正文或章节清单已变化，请刷新")
    if candidate.payload.get("complete") is False:
        raise ConflictError("未完成正文不能通过拆章确认完整")
    from novel_writer.generation.longform import units_for

    units = await units_for(service, batch)
    if any(not u.get("memory_id") for u in units):
        raise ConflictError("事实接力尚未完成")
    body = candidate.payload["body"]
    manifest = manifest_for(body, request.paragraph_ends, str(batch.id))
    manifest["candidate_sha256"] = candidate.sha256
    needs_position = [
        s["number"]
        for s in manifest["segments"]
        if not any(
            u.get("memory_id")
            and s["start"] < u["end"] <= s["end"]
            and not body[u["end"] : s["end"]].strip()
            for u in units
        )
    ]
    data = {
        "candidate_sha256": candidate.sha256,
        "parent_manifest_sha256": prior.sha256,
        "manifest": manifest,
        "unit_chain_sha256": fingerprint(units),
        "chapters_requiring_position": needs_position,
        "additional_model_calls": 0,
        "additional_cost_cny": "0",
        "notice": (
            "新章界会重新分配完整证据及依赖，并使原章节标题、审核和采用预览失效。"
            "所列章尾须由作者核对填写；独立 Memory 补全另行预览费用。"
        ),
    }
    return {**data, "preview_sha256": fingerprint(data)}


async def apply(
    service: GenerationService, batch: GenerationBatchRecord, request: ChapterArrangement
) -> dict[str, Any]:
    proposed = await preview(service, batch, request)
    if request.preview_sha256 != proposed["preview_sha256"]:
        raise ConflictError("章界预览已失效，请重新预览后确认")
    await service.append(batch, "chapter_arrangement", proposed)
    await service.append(batch, "segments", proposed["manifest"])
    batch.state = {
        k: v
        for k, v in batch.state.items()
        if k
        not in {
            "title_id",
            "early_review_id",
            "stage_adoption_preview_id",
            "adoption_preview_id",
        }
    }
    batch.state = {**batch.state, "message": "章节边界已无损更新，请重新核对逐章事实与章末现场。"}
    return await service.detail(batch)
