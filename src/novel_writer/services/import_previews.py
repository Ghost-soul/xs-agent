from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from charset_normalizer import from_bytes
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from novel_writer.db.models import ImportPreviewRecord
from novel_writer.services.canonical_json import canonical_json_sha256
from novel_writer.services.data_portability import (
    DataPortabilityService,
    PortableChapter,
    parse_manuscript,
)
from novel_writer.services.errors import ConflictError, NotFoundError, WorkflowError

PARSER_VERSION = "manuscript-parser-v1"
SupportedEncoding = Literal["auto", "utf-8", "gb18030", "utf-16le", "utf-16be"]
CommittedEncoding = Literal["utf-8", "gb18030", "utf-16le", "utf-16be"]


@dataclass(frozen=True)
class DecodedUpload:
    text: str
    encoding: str
    requires_encoding_confirmation: bool


@dataclass(frozen=True)
class ImportPreviewBinding:
    upload_sha256: str
    chapter_manifest_sha256: str
    selected_encoding: CommittedEncoding
    parser_version: str


class ImportPreviewCommitService:
    """Commit a staged import only after every preview binding is recomputed."""

    def __init__(self, session: AsyncSession, staging_root: Path) -> None:
        self.session = session
        self.staging_root = staging_root

    async def commit(
        self,
        preview_id: UUID,
        binding: ImportPreviewBinding,
        *,
        expected_title: str | None = None,
    ) -> dict[str, Any]:
        item = await self.session.scalar(
            select(ImportPreviewRecord)
            .where(ImportPreviewRecord.id == preview_id)
            .with_for_update()
        )
        if item is None:
            raise NotFoundError("import preview not found")
        expected = (
            item.upload_sha256,
            item.chapter_manifest_sha256,
            item.selected_encoding,
            item.parser_version,
        )
        observed = (
            binding.upload_sha256,
            binding.chapter_manifest_sha256,
            binding.selected_encoding,
            binding.parser_version,
        )
        if observed != expected or (
            expected_title is not None and expected_title.strip() != item.title
        ):
            raise ConflictError("import preview binding changed; preview again")
        if item.status == "completed" and item.finalized_project_id:
            return _commit_result(item, version=1)
        if item.status != "previewed":
            raise ConflictError("import preview is not available for commit")
        if item.expires_at < datetime.now(UTC):
            raise ConflictError("import preview expired")
        path = staged_import_path(self.staging_root, item.storage_key)
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != item.upload_sha256:
            raise ConflictError("staged import changed; preview again")
        try:
            decoded = decode_upload(raw, binding.selected_encoding)
            recomputed = build_import_preview(raw, decoded)
        except WorkflowError as error:
            raise ConflictError("staged import changed; preview again") from error
        if (
            recomputed["upload_sha256"] != item.upload_sha256
            or recomputed["chapter_manifest_sha256"] != item.chapter_manifest_sha256
            or recomputed["selected_encoding"] != item.selected_encoding
            or recomputed["parser_version"] != item.parser_version
        ):
            raise ConflictError("staged import changed; preview again")
        created = await DataPortabilityService(self.session).import_manuscript(
            item.title, decoded.text
        )
        item.status = "completed"
        item.finalized_project_id = UUID(str(created["project_id"]))
        await self.session.flush()
        return _commit_result(item, version=int(created["version"]))


def decode_upload(payload: bytes, selected: SupportedEncoding) -> DecodedUpload:
    if not payload:
        raise WorkflowError("导入文件为空")
    if selected != "auto":
        return DecodedUpload(_decode(payload, selected), selected, False)
    if payload.startswith(b"\xef\xbb\xbf"):
        return DecodedUpload(_decode(payload, "utf-8"), "utf-8", False)
    if payload.startswith(b"\xff\xfe"):
        return DecodedUpload(_decode(payload, "utf-16le"), "utf-16le", False)
    if payload.startswith(b"\xfe\xff"):
        return DecodedUpload(_decode(payload, "utf-16be"), "utf-16be", False)
    try:
        return DecodedUpload(payload.decode("utf-8"), "utf-8", False)
    except UnicodeDecodeError:
        detected = from_bytes(payload).best()
        encoding = _supported_detected_encoding(detected.encoding if detected else None)
        return DecodedUpload(_decode(payload, encoding), encoding, True)


def build_import_preview(payload: bytes, decoded: DecodedUpload) -> dict[str, Any]:
    chapters = parse_manuscript(decoded.text)
    entries = [_chapter_entry(chapter) for chapter in chapters]
    warnings = _warnings(chapters, decoded.text)
    manifest = {
        "parser_version": PARSER_VERSION,
        "encoding": decoded.encoding,
        "chapters": [
            {
                "ordinal": item.ordinal,
                "title": item.title,
                "body_sha256": hashlib.sha256(item.body.encode("utf-8")).hexdigest(),
            }
            for item in chapters
        ],
    }
    return {
        "upload_sha256": hashlib.sha256(payload).hexdigest(),
        "byte_size": len(payload),
        "selected_encoding": decoded.encoding,
        "requires_encoding_confirmation": decoded.requires_encoding_confirmation,
        "parser_version": PARSER_VERSION,
        "chapter_manifest_sha256": canonical_json_sha256(manifest),
        "chapter_count": len(chapters),
        "character_count": sum(len(item.body) for item in chapters),
        "chapters": entries,
        "warnings": warnings,
    }


def _decode(payload: bytes, encoding: str) -> str:
    try:
        if encoding == "utf-8":
            return payload.decode("utf-8-sig")
        if encoding == "utf-16le":
            if payload.startswith(b"\xfe\xff"):
                raise UnicodeDecodeError(encoding, payload, 0, 2, "opposite byte-order mark")
            return payload.decode(encoding).removeprefix("\ufeff")
        if encoding == "utf-16be":
            if payload.startswith(b"\xff\xfe"):
                raise UnicodeDecodeError(encoding, payload, 0, 2, "opposite byte-order mark")
            return payload.decode(encoding).removeprefix("\ufeff")
        return payload.decode(encoding)
    except (LookupError, UnicodeDecodeError) as exc:
        raise WorkflowError(f"文件不能按 {encoding} 解码") from exc


def _supported_detected_encoding(encoding: str | None) -> CommittedEncoding:
    normalized = (encoding or "").lower().replace("_", "-")
    aliases: dict[str, CommittedEncoding] = {
        "gbk": "gb18030",
        "gb18030": "gb18030",
        "cp936": "gb18030",
        "utf-16": "utf-16le",
        "utf-16le": "utf-16le",
        "utf-16be": "utf-16be",
    }
    return aliases.get(normalized, "gb18030")


def _chapter_entry(chapter: PortableChapter) -> dict[str, Any]:
    body = chapter.body.strip()
    paragraphs = [item.strip() for item in body.split("\n") if item.strip()]
    return {
        "ordinal": chapter.ordinal,
        "title": chapter.title,
        "characters": len(body),
        "first_sample": paragraphs[0][:240] if paragraphs else "",
        "last_sample": paragraphs[-1][-240:] if paragraphs else "",
    }


def _warnings(chapters: list[PortableChapter], text: str) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    titles: dict[str, int] = {}
    for chapter in chapters:
        normalized_title = chapter.title.casefold().strip()
        titles[normalized_title] = titles.get(normalized_title, 0) + 1
        characters = len(chapter.body.strip())
        if characters < 500:
            warnings.append({"code": "short_chapter", "ordinal": chapter.ordinal})
        elif characters > 20_000:
            warnings.append({"code": "long_chapter", "ordinal": chapter.ordinal})
    for title, count in titles.items():
        if count > 1:
            warnings.append({"code": "duplicate_title", "title": title, "count": count})
    ordinals = [item.ordinal for item in chapters]
    for previous, current in zip(ordinals, ordinals[1:], strict=False):
        if current != previous + 1:
            warnings.append({"code": "ordinal_gap", "previous": previous, "current": current})
    control_count = sum(
        1
        for character in text
        if unicodedata.category(character) == "Cc" and character not in "\n\r\t"
    )
    if control_count:
        warnings.append({"code": "control_characters", "count": control_count})
    return warnings


def staged_import_path(staging_root: Path, storage_key: str) -> Path:
    root = staging_root.resolve()
    path = (root / storage_key).resolve()
    if path.parent != root or path.suffix != ".bin":
        raise ConflictError("invalid import staging path")
    if not path.is_file():
        raise ConflictError("staged import is unavailable; preview again")
    return path


def _commit_result(item: ImportPreviewRecord, *, version: int) -> dict[str, Any]:
    if item.finalized_project_id is None:
        raise ConflictError("completed import has no project binding")
    return {
        "preview_id": item.id,
        "project_id": item.finalized_project_id,
        "version": version,
        "chapter_count": int(item.preview["chapter_count"]),
        "upload_sha256": item.upload_sha256,
        "chapter_manifest_sha256": item.chapter_manifest_sha256,
    }
