"""Atomic local default revisions beside provider settings, inside the data volume."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from novel_writer.core.private_files import atomic_private_write, private_directory
from novel_writer.generation.content import fingerprint
from novel_writer.generation.prompt_templates import FORMAT, checked_bundle, validate_template
from novel_writer.generation.template_catalog import FIELDS, TemplateText, default_text
from novel_writer.services.errors import ConflictError, NotFoundError, WorkflowError


class PromptTemplateStore:
    def __init__(self, profile_path: Path):
        self.root = profile_path.parent / "prompt-templates"

    def current(self) -> dict[str, Any]:
        path = self.root / "current.json"
        if not path.exists():
            return self._bundle("builtin", {}, "项目默认", "")
        return self._read(path)

    @staticmethod
    def _bundle(
        revision: str,
        templates: dict[str, Any],
        note: str,
        created: str,
    ) -> dict[str, Any]:
        value = {
            "format": FORMAT,
            "revision": revision,
            "templates": templates,
            "note": note,
            "created_at": created,
        }
        return {**value, "sha256": fingerprint(value)}

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        try:
            return checked_bundle(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise WorkflowError("Prompt 模板文件损坏或不可读；未退回其他默认值") from error

    @contextmanager
    def _lock(self) -> Iterator[None]:
        private_directory(self.root)
        descriptor = os.open(self.root / ".lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise ConflictError("另一处正在保存 Prompt，请稍后重试") from error
            yield
        finally:
            os.close(descriptor)

    def save(
        self,
        variant: str,
        template: TemplateText | None,
        expected_revision: str,
        note: str,
    ) -> dict[str, Any]:
        if variant not in FIELDS:
            raise WorkflowError("未知活动模板")
        if template is not None:
            validate_template(variant, template)
        with self._lock():
            current = self.current()
            if current["revision"] != expected_revision:
                raise ConflictError("默认模板已在其他页面更新，请重新载入后合并修改")
            templates = dict(current["templates"])
            if template is None:
                templates.pop(variant, None)
            else:
                templates[variant] = template.model_dump()
            saved = self._bundle(str(uuid4()), templates, note, datetime.now(UTC).isoformat())
            data = json.dumps(saved, ensure_ascii=False, indent=2).encode("utf-8")
            atomic_private_write(self.root / "versions" / f"{saved['revision']}.json", data)
            atomic_private_write(self.root / "current.json", data)
        return saved

    def version(self, revision: str) -> dict[str, Any]:
        from uuid import UUID

        try:
            identifier = str(UUID(revision))
        except ValueError as error:
            raise NotFoundError("模板版本不存在") from error
        path = self.root / "versions" / f"{identifier}.json"
        if not path.is_file():
            raise NotFoundError("模板版本不存在")
        return self._read(path)

    def history(self) -> list[dict[str, Any]]:
        paths = sorted(
            (self.root / "versions").glob("*.json"),
            key=lambda p: p.stat().st_mtime_ns,
            reverse=True,
        )
        return [{k: v for k, v in self._read(p).items() if k != "templates"} for p in paths[:50]]

    def text(self, variant: str, bundle: dict[str, Any]) -> TemplateText:
        saved = bundle["templates"].get(variant)
        return TemplateText.model_validate(saved) if saved is not None else default_text(variant)
