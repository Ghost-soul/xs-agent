import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from novel_writer.api.dependencies import get_session
from novel_writer.api.errors import register_error_handlers
from novel_writer.api.routes.portability import (
    ImportCommitRequest,
    _staged_import_path,
    commit_import,
    preview_import,
    router,
)
from novel_writer.db.models import ImportPreviewRecord
from novel_writer.services.errors import ConflictError, WorkflowError
from novel_writer.services.idempotency import (
    IDEMPOTENCY_REQUEST_SCHEMA_VERSION,
    request_fingerprint,
)
from novel_writer.services.import_previews import (
    SupportedEncoding,
    build_import_preview,
    decode_upload,
)

_TEXT = "# 第1章 起风\n\n城门打开了。"


@pytest.mark.parametrize(
    ("payload", "encoding", "requires_confirmation"),
    [
        (_TEXT.encode("utf-8"), "utf-8", False),
        (b"\xef\xbb\xbf" + _TEXT.encode("utf-8"), "utf-8", False),
        (_TEXT.encode("gb18030"), "gb18030", True),
        (b"\xff\xfe" + _TEXT.encode("utf-16le"), "utf-16le", False),
        (b"\xfe\xff" + _TEXT.encode("utf-16be"), "utf-16be", False),
    ],
)
def test_decode_upload_detects_supported_encodings(
    payload: bytes, encoding: str, requires_confirmation: bool
) -> None:
    decoded = decode_upload(payload, "auto")

    assert decoded.text == _TEXT
    assert decoded.encoding == encoding
    assert decoded.requires_encoding_confirmation is requires_confirmation


@pytest.mark.parametrize(
    ("encoding", "payload"),
    [
        ("utf-8", b"\xef\xbb\xbf" + _TEXT.encode("utf-8")),
        ("gb18030", _TEXT.encode("gb18030")),
        ("utf-16le", b"\xff\xfe" + _TEXT.encode("utf-16le")),
        ("utf-16be", b"\xfe\xff" + _TEXT.encode("utf-16be")),
    ],
)
def test_explicit_encoding_removes_only_its_own_byte_order_mark(
    encoding: SupportedEncoding, payload: bytes
) -> None:
    decoded = decode_upload(payload, encoding)

    assert decoded.text == _TEXT
    assert decoded.encoding == encoding
    assert decoded.requires_encoding_confirmation is False


def test_explicit_utf16_rejects_the_opposite_byte_order_mark() -> None:
    with pytest.raises(WorkflowError, match="utf-16le"):
        decode_upload(b"\xfe\xff" + _TEXT.encode("utf-16be"), "utf-16le")


def test_auto_decode_failure_is_a_workflow_error() -> None:
    with pytest.raises(WorkflowError, match="gb18030"):
        decode_upload(b"\x81", "auto")


@pytest.mark.parametrize(
    "payload",
    [
        b"\xef\xbb\xbf\xff",
        b"\xff\xfe\x00",
        b"\xfe\xff\x00",
    ],
)
def test_malformed_bom_payload_is_a_workflow_error(payload: bytes) -> None:
    with pytest.raises(WorkflowError, match="文件不能按"):
        decode_upload(payload, "auto")


def test_chapter_manifest_is_stable_across_transport_line_endings() -> None:
    lf = _TEXT.encode("utf-8")
    crlf = _TEXT.replace("\n", "\r\n").encode("utf-8")

    lf_preview = build_import_preview(lf, decode_upload(lf, "auto"))
    crlf_preview = build_import_preview(crlf, decode_upload(crlf, "auto"))

    assert lf_preview["upload_sha256"] != crlf_preview["upload_sha256"]
    assert (
        lf_preview["chapter_manifest_sha256"]
        == crlf_preview["chapter_manifest_sha256"]
    )


def test_import_preview_reports_structural_and_content_warnings() -> None:
    long_body = "长" * 20_001
    text = f"# 第1章 重复\n\n短\x01\n\n# 第3章 重复\n\n{long_body}"
    raw = text.encode("utf-8")

    preview = build_import_preview(raw, decode_upload(raw, "auto"))
    warnings = preview["warnings"]

    assert {item["code"] for item in warnings} == {
        "short_chapter",
        "long_chapter",
        "duplicate_title",
        "ordinal_gap",
        "control_characters",
    }
    assert {item["ordinal"] for item in warnings if "ordinal" in item} == {1, 3}
    assert {item["count"] for item in warnings if item["code"] == "duplicate_title"} == {2}
    assert {item["count"] for item in warnings if item["code"] == "control_characters"} == {1}
    assert any(
        item == {"code": "ordinal_gap", "previous": 1, "current": 3}
        for item in warnings
    )


def _preview_record(raw: bytes = _TEXT.encode("utf-8")) -> ImportPreviewRecord:
    preview = build_import_preview(raw, decode_upload(raw, "auto"))
    preview_id = uuid4()
    return ImportPreviewRecord(
        id=preview_id,
        title="测试小说",
        original_filename="novel.txt",
        byte_size=len(raw),
        upload_sha256=preview["upload_sha256"],
        selected_encoding=preview["selected_encoding"],
        parser_version=preview["parser_version"],
        chapter_manifest_sha256=preview["chapter_manifest_sha256"],
        preview=preview,
        storage_key=f"{preview_id}.bin",
        status="previewed",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def _commit_payload(item: ImportPreviewRecord) -> dict[str, Any]:
    return {
        "upload_sha256": item.upload_sha256,
        "chapter_manifest_sha256": item.chapter_manifest_sha256,
        "selected_encoding": item.selected_encoding,
        "parser_version": item.parser_version,
        "confirmed": True,
    }


def _route_app(session: AsyncMock, content_root: Path) -> FastAPI:
    app = FastAPI()
    app.state.content_store_root = content_root
    register_error_handlers(app)
    app.include_router(router)

    async def session_override() -> Any:
        yield session

    app.dependency_overrides[get_session] = session_override
    return app


@pytest.mark.parametrize(
    ("field", "stale_value"),
    [
        ("upload_sha256", "f" * 64),
        ("chapter_manifest_sha256", "e" * 64),
        ("selected_encoding", "gb18030"),
        ("parser_version", "manuscript-parser-v0"),
    ],
)
def test_commit_rejects_each_stale_binding_with_409(
    tmp_path: Path, field: str, stale_value: str
) -> None:
    item = _preview_record()
    session = AsyncMock()
    session.scalar.return_value = item
    body = _commit_payload(item)
    body[field] = stale_value
    app = _route_app(session, tmp_path)

    with (
        patch(
            "novel_writer.api.routes.portability.load_idempotent_response",
            new=AsyncMock(return_value=None),
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            f"/api/imports/{item.id}/commit",
            json=body,
            headers={"Idempotency-Key": "stable-import-key"},
        )

    assert response.status_code == 409
    assert "binding changed" in response.json()["detail"]


def test_same_idempotency_key_rejects_a_different_commit_payload_with_409(
    tmp_path: Path,
) -> None:
    item = _preview_record()
    accepted = _commit_payload(item)
    command = "commit_import_preview"
    resource_scope = f"import-preview:{item.id}"
    idempotency_record = SimpleNamespace(
        request_fingerprint_sha256=request_fingerprint(command, accepted),
        request_schema_version=IDEMPOTENCY_REQUEST_SCHEMA_VERSION,
        resource_scope=resource_scope,
        response={
            "preview_id": str(item.id),
            "project_id": str(uuid4()),
            "version": 1,
            "chapter_count": 1,
            "upload_sha256": item.upload_sha256,
            "chapter_manifest_sha256": item.chapter_manifest_sha256,
        },
    )
    session = AsyncMock()
    session.scalar.return_value = idempotency_record
    changed = dict(accepted, chapter_manifest_sha256="d" * 64)

    with TestClient(_route_app(session, tmp_path)) as client:
        response = client.post(
            f"/api/imports/{item.id}/commit",
            json=changed,
            headers={"Idempotency-Key": "reused-import-key"},
        )

    assert response.status_code == 409
    assert "different request payload" in response.json()["detail"]


def test_staging_path_rejects_escape_and_nested_storage_keys(tmp_path: Path) -> None:
    request = cast(
        Request,
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(content_store_root=tmp_path))),
    )

    with pytest.raises(ConflictError, match="invalid import staging path"):
        _staged_import_path(request, "../outside.bin")
    with pytest.raises(ConflictError, match="invalid import staging path"):
        _staged_import_path(request, "nested/upload.bin")


@pytest.mark.asyncio
async def test_preview_uses_a_generated_storage_key_not_the_uploaded_filename(
    tmp_path: Path,
) -> None:
    raw = _TEXT.encode("utf-8")

    class UploadRequest:
        app = SimpleNamespace(state=SimpleNamespace(content_store_root=tmp_path))

        async def stream(self) -> Any:
            yield raw[:8]
            yield raw[8:]

    session = AsyncMock()
    session.add = Mock()

    response = await preview_import(
        cast(Request, UploadRequest()),
        session,
        " 测试小说 ",
        "auto",
        "../../unsafe\x00.txt",
    )

    item = session.add.call_args.args[0]
    assert isinstance(item, ImportPreviewRecord)
    assert response.title == "测试小说"
    assert response.original_filename == "unsafe.txt"
    assert item.storage_key == f"{UUID(item.storage_key.removesuffix('.bin'))}.bin"
    assert (tmp_path / "import-staging" / item.storage_key).read_bytes() == raw


@pytest.mark.asyncio
async def test_commit_recomputes_staged_bytes_before_creating_a_project(
    tmp_path: Path,
) -> None:
    item = _preview_record()
    staging = tmp_path / "import-staging"
    staging.mkdir()
    (staging / item.storage_key).write_bytes("# 第1章 Changed\n\nchanged".encode())
    session = AsyncMock()
    session.add = Mock()
    session.scalar.side_effect = [None, None, item]
    request = cast(
        Request,
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(content_store_root=tmp_path))),
    )

    with pytest.raises(ConflictError, match="staged import changed"):
        await commit_import(
            item.id,
            ImportCommitRequest.model_validate(_commit_payload(item)),
            request,
            "tampered-import-key",
            session,
        )


@pytest.mark.asyncio
async def test_commit_reports_invalid_tampered_bytes_as_a_binding_conflict(
    tmp_path: Path,
) -> None:
    item = _preview_record()
    staging = tmp_path / "import-staging"
    staging.mkdir()
    (staging / item.storage_key).write_bytes(b"\xff")
    session = AsyncMock()
    session.add = Mock()
    session.scalar.side_effect = [None, None, item]
    request = cast(
        Request,
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(content_store_root=tmp_path))),
    )

    with pytest.raises(ConflictError, match="staged import changed"):
        await commit_import(
            item.id,
            ImportCommitRequest.model_validate(_commit_payload(item)),
            request,
            "invalid-tampered-import-key",
            session,
        )


@pytest.mark.asyncio
async def test_successful_commit_returns_the_created_snapshot_contract(tmp_path: Path) -> None:
    raw = _TEXT.encode("utf-8")
    item = _preview_record(raw)
    staging = tmp_path / "import-staging"
    staging.mkdir()
    (staging / item.storage_key).write_bytes(raw)
    session = AsyncMock()
    session.add = Mock()
    session.scalar.side_effect = [None, None, item, None, None]
    project_id = uuid4()
    portability_service = SimpleNamespace(
        import_manuscript=AsyncMock(
            return_value={
                "project_id": str(project_id),
                "version_id": str(uuid4()),
                "version": 1,
                "chapter_count": 1,
                "source": "import",
            }
        )
    )
    request = cast(
        Request,
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(content_store_root=tmp_path))),
    )

    with patch(
        "novel_writer.services.import_previews.DataPortabilityService",
        return_value=portability_service,
    ):
        response = await commit_import(
            item.id,
            ImportCommitRequest.model_validate(_commit_payload(item)),
            request,
            "successful-import-key",
            session,
        )

    assert response.preview_id == item.id
    assert response.project_id == project_id
    assert response.version == 1
    assert response.chapter_count == 1
    assert item.status == "completed"
    assert item.finalized_project_id == project_id
    portability_service.import_manuscript.assert_awaited_once_with("测试小说", _TEXT)


@pytest.mark.asyncio
async def test_interrupted_upload_removes_the_partial_staging_file(tmp_path: Path) -> None:
    class InterruptedRequest:
        app = SimpleNamespace(state=SimpleNamespace(content_store_root=tmp_path))

        async def stream(self) -> Any:
            yield b"partial"
            raise asyncio.CancelledError

    session = AsyncMock()
    session.add = Mock()

    with pytest.raises(asyncio.CancelledError):
        await preview_import(
            cast(Request, InterruptedRequest()),
            session,
            "测试小说",
            "auto",
            "novel.txt",
        )

    assert list((tmp_path / "import-staging").iterdir()) == []
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_preview_rejects_a_whitespace_only_title_before_staging(tmp_path: Path) -> None:
    request = cast(
        Request,
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(content_store_root=tmp_path))),
    )

    with pytest.raises(WorkflowError, match="作品名称不能为空"):
        await preview_import(request, AsyncMock(), "   ", "auto", "novel.txt")

    assert not (tmp_path / "import-staging").exists()
