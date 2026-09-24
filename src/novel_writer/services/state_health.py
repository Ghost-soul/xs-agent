from __future__ import annotations

import copy
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from novel_writer.db.models import (
    ApprovalRecord,
    ProjectRecord,
    StateVersionRecord,
)
from novel_writer.domain.models import StoryState
from novel_writer.services.errors import ConflictError, NotFoundError
from novel_writer.services.formal_version_sync import invalidate_formal_dependents

_POLLUTION_MARKERS = ("System.Object[]", "System.Collections", "@{")
_ARRAY_FIELDS = {
    "forbidden_behaviors",
    "development_history",
    "beliefs",
    "desires",
    "fears",
    "current_emotions",
    "internal_conflicts",
    "details",
    "resources",
    "related_characters",
    "current_characters",
}


class StateHealthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def inspect(self, project_id: UUID) -> dict[str, Any]:
        project, version = await self._current(project_id)
        findings = scan_state_pollution(version.state)
        return {
            "project_id": str(project.id),
            "version": version.number,
            "healthy": not findings,
            "findings": findings,
            "safe_repairs": sum(item["safe_repair"] for item in findings),
            "manual_repairs": sum(not item["safe_repair"] for item in findings),
            "formal_state_unchanged": True,
        }

    async def apply_safe_repairs(
        self, project_id: UUID, base_version: int, reason: str
    ) -> dict[str, Any]:
        project, version = await self._current(project_id, lock_project=True)
        if version.number != base_version:
            raise ConflictError("状态体检基线已过期，请重新扫描")
        repaired, repairs, manual = normalize_safe_pollution(version.state)
        if manual:
            raise ConflictError("仍有无法安全机械恢复的字段，请先在世界/人物/大纲表单中手动修正")
        if not repairs:
            return {
                "project_id": str(project.id),
                "version": version.number,
                "created": False,
                "message": "正式StoryState未发现可安全修复的数据污染",
            }
        validated = StoryState.model_validate(repaired)
        sync = await invalidate_formal_dependents(
            self.session,
            project.id,
            "正式 StoryState 已安全修复；旧创作批次已归档，自动运行同步终止。",
        )
        created = StateVersionRecord(
            project_id=project.id,
            number=version.number + 1,
            parent_id=version.id,
            parent_number=version.number,
            state=validated.model_dump(mode="json"),
            chapter_revisions=version.chapter_revisions,
            chapter_titles=version.chapter_titles or {},
        )
        self.session.add(created)
        await self.session.flush()
        project.current_version_id = created.id
        self.session.add(
            ApprovalRecord(
                target_type="state_health_repair",
                target_id=project.id,
                decision="approved",
                reason=reason,
            )
        )
        return {
            "project_id": str(project.id),
            "version": created.number,
            "created": True,
            "repairs": repairs,
            **sync,
        }

    async def _current(
        self, project_id: UUID, *, lock_project: bool = False
    ) -> tuple[ProjectRecord, StateVersionRecord]:
        statement = select(ProjectRecord).where(ProjectRecord.id == project_id)
        if lock_project:
            statement = statement.with_for_update()
        project = await self.session.scalar(statement)
        if project is None or project.current_version_id is None:
            raise NotFoundError("project not found")
        version = await self.session.get(StateVersionRecord, project.current_version_id)
        if version is None:
            raise NotFoundError("formal version not found")
        return project, version


def scan_state_pollution(value: Any, path: str = "$") -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in _ARRAY_FIELDS and isinstance(child, str):
                findings.append(
                    _finding(
                        child_path,
                        child,
                        "structured_field_is_string",
                        child == "System.Object[]",
                    )
                )
            findings.extend(scan_state_pollution(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(scan_state_pollution(child, f"{path}[{index}]"))
    elif isinstance(value, str) and any(marker in value for marker in _POLLUTION_MARKERS):
        findings.append(
            _finding(path, value, "powershell_stringification", value == "System.Object[]")
        )
    return _deduplicate(findings)


def normalize_safe_pollution(
    state: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    repaired = copy.deepcopy(state)
    repairs: list[dict[str, Any]] = []
    manual: list[dict[str, Any]] = []

    def visit(value: Any, path: str = "$") -> None:
        if isinstance(value, dict):
            for key, child in list(value.items()):
                child_path = f"{path}.{key}"
                if key in _ARRAY_FIELDS and child == "System.Object[]":
                    value[key] = []
                    repairs.append({"path": child_path, "from": "System.Object[]", "to": []})
                else:
                    visit(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")
        elif isinstance(value, str) and any(marker in value for marker in _POLLUTION_MARKERS):
            manual.append(_finding(path, value, "manual_repair_required", False))

    visit(repaired)
    return repaired, repairs, _deduplicate(manual)


def _finding(path: str, value: str, kind: str, safe_repair: bool) -> dict[str, Any]:
    return {
        "path": path,
        "kind": kind,
        "preview": value[:240],
        "safe_repair": safe_repair,
        "severity": "error",
    }


def _deduplicate(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        unique[(str(item["path"]), str(item["kind"]))] = item
    return list(unique.values())
