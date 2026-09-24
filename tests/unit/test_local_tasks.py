from pathlib import Path
from uuid import uuid4

import pytest

from novel_writer.db.models import LocalTaskRecord
from novel_writer.services.canonical_json import canonical_json_sha256
from novel_writer.services.errors import WorkflowError
from novel_writer.services.local_tasks import (
    LOCAL_TASK_ALLOWLIST,
    _validate_record_manifest,
    local_task_artifact_path,
    task_artifact_metadata,
)


def _record() -> LocalTaskRecord:
    project_id = uuid4()
    payload = {
        "schema_version": "local-task-v1",
        "project_id": str(project_id),
        "kind": "export_epub",
        "formal_snapshot": {
            "schema_version": "formal-snapshot-manifest-v1",
            "project_id": str(project_id),
            "project_title": "冻结标题",
            "version_id": str(uuid4()),
            "version_number": 3,
            "state_sha256": "a" * 64,
            "chapters": [],
            "snapshot_sha256": "b" * 64,
        },
    }
    return LocalTaskRecord(
        id=uuid4(),
        project_id=project_id,
        schema_version="local-task-v1",
        kind="export_epub",
        input_sha256=canonical_json_sha256(payload),
        payload=payload,
    )


def test_local_task_manifest_is_immutable_and_allowlisted() -> None:
    record = _record()

    _validate_record_manifest(record)

    record.payload = {**record.payload, "kind": "provider_dispatch"}
    with pytest.raises(WorkflowError, match="manifest is invalid"):
        _validate_record_manifest(record)
    assert "provider_dispatch" not in LOCAL_TASK_ALLOWLIST


def test_local_task_manifest_requires_a_frozen_formal_snapshot() -> None:
    record = _record()
    record.payload = {
        "schema_version": "local-task-v1",
        "project_id": str(record.project_id),
        "kind": record.kind,
    }
    record.input_sha256 = canonical_json_sha256(record.payload)

    with pytest.raises(WorkflowError, match="manifest is invalid"):
        _validate_record_manifest(record)


def test_local_task_artifact_path_is_sha_and_uuid_bound(tmp_path: Path) -> None:
    task_id = uuid4()
    digest = "a" * 64

    path = local_task_artifact_path(tmp_path, task_id, digest, "docx")

    assert path == (tmp_path / "local-tasks" / str(task_id) / f"{digest}.docx").resolve()
    with pytest.raises(WorkflowError, match="SHA"):
        local_task_artifact_path(tmp_path, task_id, "../escape", "docx")
    with pytest.raises(WorkflowError, match="extension"):
        local_task_artifact_path(tmp_path, task_id, digest, "../../zip")


def test_local_task_artifact_metadata_sanitizes_filename() -> None:
    filename, media_type, extension = task_artifact_metadata(
        "project_backup", '长篇:终稿/"确认"'
    )

    assert filename == "长篇_终稿__确认_-backup.json"
    assert media_type == "application/json"
    assert extension == "json"


def test_local_task_runner_has_no_provider_import_boundary() -> None:
    source = Path("src/novel_writer/services/local_tasks.py").read_text(encoding="utf-8")

    assert "novel_writer.providers" not in source
    assert "provider_run" not in source
    assert "AgentCallRecord" not in source
