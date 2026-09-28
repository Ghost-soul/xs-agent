"""Separate immutable defaults for stage craft; never overwrite legacy or mounted text."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from novel_writer.core.private_files import atomic_private_write
from novel_writer.generation import craft_templates, editable_rules
from novel_writer.generation.content import fingerprint
from novel_writer.generation.template_catalog import TemplateText
from novel_writer.services.errors import ConflictError, WorkflowError
from novel_writer.services.prompt_template_store import PromptTemplateStore


class CraftTemplateStore(PromptTemplateStore):
    def __init__(self, profile_path: Path):
        super().__init__(profile_path)
        self.legacy = PromptTemplateStore(profile_path)
        self.root = self.root / "stage-craft-v1"

    @staticmethod
    def _bundle(
        revision: str, templates: dict[str, Any], note: str, created: str
    ) -> dict[str, Any]:
        value = {
            "format": craft_templates.FORMAT,
            "revision": revision,
            "templates": templates,
            "note": note,
            "created_at": created,
        }
        return {**value, "sha256": fingerprint(value)}

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        try:
            return editable_rules.checked_bundle(json.loads(path.read_text(encoding="utf8")))
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise WorkflowError("Prompt 模板文件损坏或不可读；未退回其他默认值") from error

    def _persist_version(self, bundle: dict[str, Any]) -> bytes:
        data = json.dumps(bundle, ensure_ascii=False, indent=2).encode("utf8")
        path = self.root / "versions" / f"{bundle['revision']}.json"
        if path.exists():
            if self._read(path) != bundle:
                raise ConflictError("同版本模板内容不同；未覆盖历史版本或当前默认")
        else:
            atomic_private_write(path, data)
        return data

    def current(self) -> dict[str, Any]:
        path = self.root / "current.json"
        if path.exists():
            return self._read(path)
        original = self.legacy.current()
        adapted, diffs = {}, {}
        for variant, value in original["templates"].items():
            text, edits = craft_templates.adapt(variant, TemplateText.model_validate(value))
            adapted[variant], diffs[variant] = text.model_dump(), edits
        value = self._bundle(
            str(
                uuid5(
                    NAMESPACE_URL,
                    "stage-craft-v1:" + fingerprint([original["sha256"], adapted, diffs]),
                )
            ),
            adapted,
            "由旧默认生成的最小适配；旧版本完整保留",
            original.get("created_at", ""),
        )
        value.update(
            parent_revision=original["revision"], parent_sha256=original["sha256"], adaptation=diffs
        )
        value["sha256"] = fingerprint({k: v for k, v in value.items() if k != "sha256"})
        return value

    def install(self) -> dict[str, Any]:
        with self._lock():
            bundle = self.current()
            if not (self.root / "current.json").exists():
                data = self._persist_version(bundle)
                atomic_private_write(self.root / "current.json", data)
            return bundle

    def save(
        self,
        variant: str,
        template: TemplateText | None,
        expected_revision: str,
        note: str,
        program_settings: editable_rules.ProgramSettings | None = None,
        update_program_settings: bool = False,
    ) -> dict[str, Any]:
        if variant not in craft_templates.FIELDS:
            raise WorkflowError("未知活动模板")
        if template is not None:
            craft_templates.validate_template(variant, template)
        if program_settings is not None:
            editable_rules.validate(variant, program_settings)
        with self._lock():
            current = self.current()
            if current["revision"] != expected_revision:
                raise ConflictError("默认模板已更新，请重新载入后合并修改")
            # Persist the parent even when the initial adapted default was only virtual.
            self._persist_version(current)
            templates = dict(current["templates"])
            if template is None:
                templates.pop(variant, None)
            else:
                templates[variant] = template.model_dump()
            saved = self._bundle(str(uuid4()), templates, note, datetime.now(UTC).isoformat())
            settings = dict(current.get(editable_rules.SETTINGS, {}))
            if update_program_settings:
                if program_settings is None:
                    settings.pop(variant, None)
                else:
                    settings[variant] = program_settings.model_dump(exclude_none=True)
            if settings:
                saved[editable_rules.SETTINGS] = settings
                saved["sha256"] = fingerprint({k: v for k, v in saved.items() if k != "sha256"})
            data = self._persist_version(saved)
            atomic_private_write(self.root / "current.json", data)
        return saved

    def text(self, variant: str, bundle: dict[str, Any]) -> TemplateText:
        value = bundle["templates"].get(variant)
        return (
            TemplateText.model_validate(value)
            if value is not None
            else craft_templates.default_text(variant)
        )
