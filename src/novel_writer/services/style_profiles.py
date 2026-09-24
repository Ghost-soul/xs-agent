from __future__ import annotations

import re
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from novel_writer.db.models import (
    ProjectRecord,
    ProjectStyleProfileRecord,
)
from novel_writer.services.errors import NotFoundError, WorkflowError

QUALITY_CARD_DIR = Path(__file__).resolve().parents[3] / "configs" / "genre-quality-cards"
CARD_LAYERS = {"genre": "题材卡", "narrative": "叙事卡"}
CARD_FOLDERS = ("genres", "narrative")
CARD_SECTIONS = {
    "适用线索": "match_terms",
    "读者期待": "reader_contract",
    "每章检查": "quality_checks",
    "重点避免": "failure_modes",
}
GENRE_SELECTION_MODES = ("unselected", "specified")

# ---------------------------------------------------------------------------
# G0 解析合同：题材卡顶层栏目集合。
# 根级 ASCII key（无值、位于行首）若属于该集合则打开新的顶层栏目；
# 其余根级 key 一律视为当前顶层栏目下的小节头，不再依赖固定两空格缩进。
# ---------------------------------------------------------------------------
GENRE_TOP_SECTIONS = (
    "aliases",
    "description",
    "primary_backgrounds",
    "primary_subgenres",
    "detection",
    "reader_contract",
    "writing_guidance",
    "chapter_check",
    "failure_modes",
    "advanced_rules",
    "combination_rules",
    "priority_rules",
)
GENRE_REQUIRED_SECTIONS = ("detection", "reader_contract", "chapter_check", "failure_modes")
DETECTION_EXCLUDED_SUBSECTIONS = {"weak_signals", "invalid_signals", "weak_clues"}
ASCII_HEADER_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):\s*$")
SCALAR_KEY_RE_TEMPLATE = r"^{key}:\s*(.+)$"
BULLET_RE = re.compile(r"^\s*[-*]\s+(?P<text>.+?)\s*$")
FAILURE_NAME_RE = re.compile(r"^[-*]\s+name:\s*(?P<name>.+?)\s*$")


class StyleAsset(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    purpose: str = Field(min_length=1, max_length=240)
    strengthen: list[str] = Field(default_factory=list, max_length=8)
    avoid: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("strengthen", "avoid")
    @classmethod
    def validate_rules(cls, values: list[str]) -> list[str]:
        cleaned = [item.strip() for item in values if item.strip()]
        if any(len(item) > 160 for item in cleaned):
            raise ValueError("单条风格规则不能超过160字")
        return list(dict.fromkeys(cleaned))


@lru_cache(maxsize=4)
def load_quality_cards(directory: Path = QUALITY_CARD_DIR) -> list[dict[str, Any]]:
    registered_paths: list[Path] = []
    paths = [*directory.glob("*.md")]
    for folder in CARD_FOLDERS:
        paths.extend((directory / folder).glob("*.md"))
    for path in sorted(paths):
        if not path.resolve().is_relative_to(directory.resolve()):
            raise WorkflowError("创作卡来源越出卡库目录")
        _body, metadata = _strip_front_matter(path.read_text(encoding="utf-8"))
        if metadata.get("schema_version") == "genre-knowledge-card-v1":
            registered_paths.append(path)
    cards = []
    for path in registered_paths:
        card = _parse_quality_card(path)
        if card is not None:
            card["source_file"] = path.relative_to(directory).as_posix()
            card["layer"] = card.get("layer") or "genre"
            if card["layer"] not in CARD_LAYERS:
                raise WorkflowError(f"创作卡分类无效：{path.name}")
            card["category_label"] = CARD_LAYERS[card["layer"]]
            cards.append(card)
    if not cards:
        raise WorkflowError(f"题材质量卡目录中没有已注册的 Markdown 文件：{directory}")
    identifiers = [str(card["id"]) for card in cards]
    if len(identifiers) != len(set(identifiers)):
        raise WorkflowError("题材质量卡 id 不能重复")
    return cards


def genre_card_by_id(card_id: str) -> dict[str, Any] | None:
    """Return a locally registered genre card by its stable id."""

    normalized = card_id.strip()
    if not normalized:
        return None
    return next((card for card in load_quality_cards() if card["id"] == normalized), None)


def validate_genre_card_id(card_id: str) -> dict[str, Any]:
    """Validate an author-selected card without consulting a Provider."""

    card = genre_card_by_id(card_id)
    if card is None:
        raise WorkflowError(f"题材卡不存在或不可用：{card_id}")
    return card


def validate_genre_card_selection(
    primary_genre_card_id: str,
    secondary_genre_card_ids: Sequence[str] = (),
) -> tuple[str, tuple[str, ...]]:
    """Read an author-managed pool, including historical mixed card selections."""

    primary = primary_genre_card_id.strip()
    secondary = tuple(card_id.strip() for card_id in secondary_genre_card_ids)
    if not primary:
        raise WorkflowError("指定题材卡模式必须选择一张主题材卡")
    if any(not card_id for card_id in secondary):
        raise WorkflowError("副题材卡 ID 不能为空")
    card_ids = (primary, *secondary)
    if len(card_ids) != len(set(card_ids)):
        raise WorkflowError("主题材卡和副题材卡不能重复")
    for card_id in card_ids:
        validate_genre_card_id(card_id)
    return primary, secondary


def validate_project_card_selection(
    primary_id: str, secondary_ids: Sequence[str] = ()
) -> tuple[str, tuple[str, ...]]:
    """Validate new author settings; historical reads retain their original mixed pool."""
    primary, secondary = validate_genre_card_selection(primary_id, secondary_ids)
    if validate_genre_card_id(primary)["layer"] != "genre":
        raise WorkflowError("主题材须选择题材卡；叙事卡请放入叙事多选")
    if sum(validate_genre_card_id(value)["layer"] == "genre" for value in secondary) > 1:
        raise WorkflowError("题材最多一张主卡和一张副卡；叙事卡可多选")
    return primary, secondary


# ---------------------------------------------------------------------------
# 题材卡解析（G0：同时支持根级栏目版式与历史两空格缩进版式）
# ---------------------------------------------------------------------------


def _parse_quality_card(path: Path) -> dict[str, Any] | None:
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return None
    body, meta = _strip_front_matter(text)
    body = _unwrap_document_fence(body)
    if not body.lstrip().startswith("genre_card:"):
        return _parse_simple_markdown_card(path, body)
    return _parse_structured_card(path, body, meta)


def _strip_front_matter(text: str) -> tuple[str, dict[str, str]]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text, {}
    meta: dict[str, str] = {}
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(lines[index + 1 :]), meta
        if ":" in lines[index]:
            key, value = lines[index].split(":", 1)
            meta[key.strip()] = value.strip()
    return text, {}


def _unwrap_document_fence(text: str) -> str:
    """兼容历史整卡代码围栏；栏目内部围栏仍由栏目解析器跳过。"""

    lines = text.splitlines()
    nonempty = [index for index, line in enumerate(lines) if line.strip()]
    if len(nonempty) >= 2:
        first = lines[nonempty[0]].strip().lower()
        last = lines[nonempty[-1]].strip()
        if first.startswith("```") and last == "```":
            return "\n".join(lines[nonempty[0] + 1 : nonempty[-1]])
    return text


def _normalize_indent(lines: list[str]) -> list[str]:
    """历史版式把所有键缩进在 genre_card: 之下；统一还原为根级栏目版式。"""

    has_root_id = any(line.startswith("id:") for line in lines)
    has_indented_id = any(line.startswith("  id:") for line in lines)
    if has_indented_id and not has_root_id:
        return [line[2:] if line.startswith("  ") else line for line in lines]
    return lines


def _walk_sections(
    lines: list[str], top_sections: tuple[str, ...]
) -> tuple[dict[str, list[str]], list[str], list[str]]:
    sections: dict[str, list[str]] = {}
    order: list[str] = []
    preamble: list[str] = []
    current: str | None = None
    in_fence = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            if current is not None:
                sections[current].append(line)
            elif stripped != "```":
                preamble.append(line)
            continue
        header = ASCII_HEADER_RE.match(stripped)
        if header and not in_fence and not line[:1].isspace():
            key = header.group(1)
            if key in top_sections and key not in sections:
                current = key
                sections[key] = []
                order.append(key)
                continue
        if current is None:
            if stripped:
                preamble.append(line)
            continue
        sections[current].append(line)
    return sections, order, preamble


def _collect_scalars(lines: list[str]) -> dict[str, str]:
    scalars: dict[str, str] = {}
    for key in ("id", "name", "card_type", "version"):
        pattern = re.compile(SCALAR_KEY_RE_TEMPLATE.format(key=key))
        for line in lines:
            if line[:1].isspace():
                continue
            if match := pattern.match(line):
                scalars[key] = match.group(1).strip()
                break
    return scalars


def _validate_genre_fields(
    path: Path, scalars: dict[str, str], sections: dict[str, list[str]]
) -> None:
    if not scalars.get("name"):
        raise WorkflowError(f"题材质量卡缺少 name：{path.name}")
    for section in GENRE_REQUIRED_SECTIONS:
        if section not in sections:
            raise WorkflowError(f"题材质量卡格式不完整：{path.name}（缺少 {section} 栏目）")
    if not _detection_terms_from(sections["detection"]):
        raise WorkflowError(f"题材质量卡格式不完整：{path.name}（detection 无有效词条）")
    if not _section_bullet_list(sections["reader_contract"]):
        raise WorkflowError(f"题材质量卡格式不完整：{path.name}（reader_contract 无有效条目）")
    if not _section_bullet_list(sections["chapter_check"]):
        raise WorkflowError(f"题材质量卡格式不完整：{path.name}（chapter_check 无有效条目）")
    if not _failure_mode_items(sections["failure_modes"]):
        raise WorkflowError(f"题材质量卡格式不完整：{path.name}（failure_modes 无有效条目）")


def _parse_structured_card(path: Path, body: str, meta: dict[str, str]) -> dict[str, Any]:
    lines = _normalize_indent(body.splitlines())
    scalars = _collect_scalars(lines)
    if meta:
        if meta.get("schema_version") != "genre-knowledge-card-v1":
            raise WorkflowError(f"题材质量卡 schema_version 不支持：{path.name}")
        if meta.get("id") and meta["id"] != scalars.get("id", path.stem):
            raise WorkflowError(f"题材质量卡 front matter 与正文 id 不一致：{path.name}")
    sections, order, preamble = _walk_sections(lines, GENRE_TOP_SECTIONS)
    _validate_genre_fields(path, scalars, sections)
    card: dict[str, Any] = {
        "id": scalars.get("id", path.stem),
        "name": scalars["name"],
        "card_type": scalars.get("card_type", ""),
        "content_version": scalars.get("version", ""),
        "tagline": _description_text(sections.get("description", [])),
        "aliases": _section_bullet_list(sections.get("aliases", [])),
        "match_terms": _detection_terms_from(sections["detection"]),
        "reader_contract": _section_bullet_list(sections["reader_contract"]),
        "quality_checks": _section_bullet_list(sections["chapter_check"]),
        "failure_modes": _failure_mode_items(sections["failure_modes"]),
        "writing_guidance": _section_bullet_list(sections.get("writing_guidance", [])),
        "combination_guidance": _subsection_bullets(
            sections.get("combination_rules", []), "compatible"
        ),
        "combination_conflicts": _subsection_bullets(
            sections.get("combination_rules", []), "conflicts"
        ),
        "source_file": path.name,
        "top_sections": order,
        "preamble_lines": len(preamble),
    }
    if meta:
        card["front_matter"] = meta
        card["schema_version"] = meta["schema_version"]
        card["layer"] = meta.get("layer", "")
        card["rule_manifest"] = meta.get("rule_manifest", "")
        card["detection_profile"] = meta.get("detection_profile", "")
    return card


def _parse_simple_markdown_card(path: Path, text: str) -> dict[str, Any] | None:
    if not text.strip():
        return None
    title = ""
    metadata: dict[str, str] = {}
    sections: dict[str, list[str]] = {value: [] for value in CARD_SECTIONS.values()}
    current: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("# ") and not title:
            title = line[2:].strip()
        elif line.startswith("## "):
            current = CARD_SECTIONS.get(line[3:].strip())
        elif current and line.startswith("- "):
            sections[current].append(line[2:].strip())
        elif not current and ":" in line:
            key, value = line.split(":", 1)
            metadata[key.strip().lower()] = value.strip()
    card_id = metadata.get("id", path.stem)
    tagline = metadata.get("tagline", "")
    if not title and not any(f"## {heading}" in text for heading in CARD_SECTIONS):
        return None
    if not title or not tagline or any(not sections[key] for key in sections):
        raise WorkflowError(f"题材质量卡格式不完整：{path.name}")
    return {
        "id": card_id,
        "name": title,
        "tagline": tagline,
        **sections,
        "source_file": path.name,
    }


def _detection_terms_from(section_lines: list[str]) -> list[str]:
    """收集 detection 栏目中的证据词条，排除 weak/invalid 信号小节。"""

    terms: list[str] = []
    excluded = False
    in_fence = False
    for line in section_lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        header = ASCII_HEADER_RE.match(stripped)
        if header:
            excluded = header.group(1) in DETECTION_EXCLUDED_SUBSECTIONS
            continue
        if excluded:
            continue
        if match := BULLET_RE.match(line):
            term = match.group("text").strip()
            if term:
                terms.append(term)
    return _unique(terms)


def _section_bullet_list(section_lines: list[str]) -> list[str]:
    values: list[str] = []
    for line in section_lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            continue
        if ASCII_HEADER_RE.match(stripped):
            continue
        if match := BULLET_RE.match(line):
            value = match.group("text").strip()
            if value:
                values.append(value)
    return _unique(values)


def _description_text(section_lines: list[str]) -> str:
    texts: list[str] = []
    bullets: list[str] = []
    for line in section_lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            continue
        if ASCII_HEADER_RE.match(stripped):
            continue
        if match := BULLET_RE.match(line):
            bullets.append(match.group("text").strip())
            continue
        if stripped and not stripped.endswith(":") and not stripped.endswith("："):
            texts.append(stripped)
    if texts:
        return " ".join(texts)
    return " ".join(bullets)


def _failure_mode_items(section_lines: list[str]) -> list[str]:
    """把 - name / problem / correction 结构压成“名称：问题”诊断条目。"""

    items: list[str] = []
    name = ""
    collecting = False
    problem_parts: list[str] = []

    def flush() -> None:
        nonlocal name, collecting, problem_parts
        problem = " ".join(problem_parts).strip()
        if problem:
            items.append(f"{name}：{problem}" if name else problem)
        name, collecting, problem_parts = "", False, []

    for line in section_lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            continue
        if name_match := FAILURE_NAME_RE.match(stripped):
            flush()
            name = name_match.group("name").strip()
            continue
        if header := ASCII_HEADER_RE.match(stripped):
            flush()
            collecting = header.group(1) == "problem"
            continue
        if inline := re.match(r"^(?:-\s*)?problem:\s*(?P<text>.+)$", stripped):
            flush()
            collecting = True
            problem_parts.append(inline.group("text").strip())
            continue
        if collecting and stripped and not BULLET_RE.match(line):
            problem_parts.append(stripped)
    flush()
    return _unique(items)


def _subsection_bullets(section_lines: list[str], subsection: str) -> list[str]:
    values: list[str] = []
    collecting = False
    for line in section_lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            continue
        if header := ASCII_HEADER_RE.match(stripped):
            collecting = header.group(1) == subsection
            continue
        if collecting and (match := BULLET_RE.match(line)):
            value = match.group("text").strip()
            if value:
                values.append(value)
    return _unique(values)


# ---------------------------------------------------------------------------
# 题材与叙事卡统一读取，按 layer 分别呈现；均由作者手选。
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# 作者手选题材卡与风格资料管理
# ---------------------------------------------------------------------------


class StyleProfileService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def catalog(self) -> list[dict[str, Any]]:
        return [dict(card) for card in load_quality_cards()]

    async def get(self, project_id: UUID) -> dict[str, Any]:
        await self._project(project_id)
        record = await self.session.get(ProjectStyleProfileRecord, project_id)
        assets = record.assets if record else []
        stored_genre_id = str(record.genre_id).strip() if record is not None else "auto"
        stored_secondary_ids = tuple(record.secondary_genre_ids or ()) if record else ()
        selection_mode = "unselected"
        selected_card: dict[str, Any] | None = None
        selected_cards: list[dict[str, Any]] = []
        if record is not None and record.selection_mode == "specified":
            selection_mode = "specified"
        if selection_mode == "specified":
            primary_id, secondary_ids = validate_genre_card_selection(
                stored_genre_id,
                stored_secondary_ids,
            )
            selected_card = validate_genre_card_id(primary_id)
            selected_cards = [selected_card]
            selected_cards.extend(validate_genre_card_id(card_id) for card_id in secondary_ids)
        if selection_mode == "specified":
            matches = [
                {
                    **card,
                    "match_score": 0,
                    "match_evidence": [
                        f"作者指定{'主卡' if index == 0 else '可选卡'} · {card['category_label']}"
                    ],
                    "selection_reason": (
                        "author_specified_primary" if index == 0 else "author_specified_secondary"
                    ),
                }
                for index, card in enumerate(selected_cards)
            ]
        else:
            matches = []
        mechanisms: list[dict[str, Any]] = []
        return self._response(
            project_id,
            matches,
            mechanisms,
            assets,
            selection_mode=selection_mode,
            genre_card_id=(selected_card["id"] if selected_card is not None else None),
            secondary_genre_card_ids=(
                [card["id"] for card in matches[1:]] if selection_mode == "specified" else []
            ),
        )


    async def save(
        self,
        project_id: UUID,
        assets: list[dict[str, Any]],
        legacy_genre_id: str = "",
        *,
        selection_mode: str = "unselected",
        genre_card_id: str | None = None,
        secondary_genre_card_ids: Sequence[str] = (),
    ) -> dict[str, Any]:
        await self._project(project_id)
        validated = [item.model_dump() for item in map(StyleAsset.model_validate, assets)]
        if len(validated) > 12:
            raise WorkflowError("一个项目最多保存12个风格资产")
        if selection_mode == "automatic":
            selection_mode = "unselected"
        if selection_mode not in GENRE_SELECTION_MODES:
            raise WorkflowError("题材卡选择模式无效")
        requested_card_id = (genre_card_id or "").strip()
        if selection_mode == "specified":
            if not requested_card_id:
                requested_card_id = legacy_genre_id.strip()
            stored_genre_id, stored_secondary_ids = validate_project_card_selection(
                requested_card_id,
                secondary_genre_card_ids,
            )
        else:
            stored_genre_id = ""
            if requested_card_id:
                raise WorkflowError("未选择题材卡时不能携带主题材卡")
            if secondary_genre_card_ids:
                raise WorkflowError("未选择题材卡时不能携带副题材卡")
            stored_secondary_ids = ()
        record = await self.session.get(ProjectStyleProfileRecord, project_id)
        if record is None:
            record = ProjectStyleProfileRecord(
                project_id=project_id,
                selection_mode=selection_mode,
                genre_id=stored_genre_id,
                secondary_genre_ids=list(stored_secondary_ids),
                assets=validated,
            )
            self.session.add(record)
        else:
            record.selection_mode = selection_mode
            record.genre_id = stored_genre_id
            record.secondary_genre_ids = list(stored_secondary_ids)
            record.assets = validated
        await self.session.flush()
        return await self.get(project_id)


    def _response(
        self,
        project_id: UUID,
        matches: list[dict[str, Any]],
        mechanisms: list[dict[str, Any]],
        assets: list[dict[str, Any]],
        *,
        selection_mode: str,
        genre_card_id: str | None,
        secondary_genre_card_ids: list[str],
    ) -> dict[str, Any]:
        return {
            "project_id": str(project_id),
            "selection_mode": selection_mode,
            "genre_id": genre_card_id or "",
            "genre_card_id": genre_card_id,
            "secondary_genre_card_ids": secondary_genre_card_ids,
            "matched_cards": [dict(card) for card in matches],
            "matched_mechanisms": [dict(card) for card in mechanisms],
            "assets": assets,
            "policy": (
                "题材卡提供世界背景与基础设定；叙事卡引入人物关系、职业事件、故事机制与经典桥段。"
                "题材一张主卡、最多一张副卡；叙事可多选，写作时单独调整本阶段使用的卡。"
                "两类均由作者手选，新预览使用当前卡文，已保存批次沿用冻结版本。"
            ),
        }

    async def _project(self, project_id: UUID) -> ProjectRecord:
        project = await self.session.get(ProjectRecord, project_id)
        if project is None:
            raise NotFoundError("project not found")
        return project


# ---------------------------------------------------------------------------
# Phase 4.1: Genre pacing baseline extraction
# ---------------------------------------------------------------------------


def _unique(values: Any) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values))
