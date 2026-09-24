from __future__ import annotations

import argparse
import asyncio
import gc
import json
import math
import os
import platform
import socket
import sys
import time
import tracemalloc
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5
from xml.etree import ElementTree

from sqlalchemy import delete, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from novel_writer.db.engine import Database  # noqa: E402
from novel_writer.db.models import (  # noqa: E402
    ChapterRecord,
    ChapterRevisionRecord,
    ProjectRecord,
    SearchDocumentRecord,
    StateVersionRecord,
    WritingSessionRecord,
)
from novel_writer.domain.models import StoryState  # noqa: E402
from novel_writer.services.blueprint_management import BlueprintManagementService  # noqa: E402
from novel_writer.services.data_portability import DataPortabilityService  # noqa: E402
from novel_writer.services.import_previews import (  # noqa: E402
    build_import_preview,
    decode_upload,
)
from novel_writer.services.local_search import LocalSearchService  # noqa: E402
from novel_writer.services.project_queries import ProjectQueryService  # noqa: E402
from novel_writer.services.reader_view import ReaderViewService  # noqa: E402

FIXTURE_NAMESPACE = UUID("8b0fb0d8-991c-49d5-8292-98e25fe29df4")
MIB = 1024 * 1024


@dataclass(frozen=True)
class FixtureSpec:
    name: str
    chapters: int
    characters: int
    foreshadowings: int
    plot_threads: int
    target_characters: int


@dataclass(frozen=True)
class SeededFixture:
    project_id: UUID
    current_version_id: UUID
    first_chapter_id: UUID
    chapter_characters: int


FIXTURE_SPECS = (
    FixtureSpec("longform-200", 200, 50, 100, 100, 1_000_000),
    FixtureSpec("longform-500", 500, 120, 300, 300, 2_500_000),
)


def _uuid(spec: FixtureSpec, kind: str, ordinal: int = 0) -> UUID:
    return uuid5(FIXTURE_NAMESPACE, f"{spec.name}:{kind}:{ordinal}")


def _chapter_body(spec: FixtureSpec, ordinal: int, version: str) -> str:
    target = spec.target_characters // spec.chapters
    sentence = (
        f"潮印性能锚点{ordinal:04d}，{version}阵线沿回廊推进；"
        "人物根据现场证据调整选择，伏笔与剧情线保持可检索。"
    )
    return (sentence * math.ceil(target / len(sentence)))[:target]


def _story_state(spec: FixtureSpec, version: str) -> StoryState:
    characters = []
    for index in range(spec.characters):
        tier = "A" if index < 20 else "B"
        characters.append(
            {
                "id": _uuid(spec, f"{version}-character", index),
                "name": f"角色{index:03d}",
                "aliases": [f"代号{index:03d}", f"旧称{index:03d}"],
                "tier": tier,
                "description": f"{version}人物档案与潮印性能锚点关联",
            }
        )
    plot_threads = [
        {
            "id": _uuid(spec, f"{version}-thread", index),
            "name": f"剧情线{index:03d}",
            "summary": f"{version}剧情线持续追踪潮印性能锚点",
        }
        for index in range(spec.plot_threads)
    ]
    foreshadowings = [
        {
            "id": _uuid(spec, f"{version}-foreshadowing", index),
            "content": f"伏笔{index:03d}与潮印性能锚点相关",
            "introduced_chapter": index % spec.chapters + 1,
            "last_advanced_chapter": index % spec.chapters + 1,
        }
        for index in range(spec.foreshadowings)
    ]
    return StoryState.model_validate(
        {
            "characters": characters,
            "plot_threads": plot_threads,
            "foreshadowings": foreshadowings,
            "story_foundation": {
                "premise": f"{version}长篇性能夹具",
                "central_conflict": "在封锁前确认潮印性能锚点",
            },
        }
    )


async def _seed_fixture(database: Database, spec: FixtureSpec) -> SeededFixture:
    project_id = _uuid(spec, "project")
    historical_version_id = _uuid(spec, "version", 1)
    current_version_id = _uuid(spec, "version", 2)
    chapter_characters = spec.target_characters // spec.chapters
    historical_state = _story_state(spec, "历史").model_dump(mode="json")
    current_state = _story_state(spec, "当前").model_dump(mode="json")
    async with database.session() as session, session.begin():
        await _delete_fixture_rows(session, project_id)
        project = ProjectRecord(
            id=project_id,
            title=f"{spec.name}-脱敏性能夹具",
            current_version_id=None,
        )
        historical_version = StateVersionRecord(
            id=historical_version_id,
            project_id=project_id,
            number=1,
            state=historical_state,
            chapter_revisions={},
            chapter_titles={},
        )
        current_version = StateVersionRecord(
            id=current_version_id,
            project_id=project_id,
            number=2,
            parent_id=historical_version_id,
            parent_number=1,
            state=current_state,
            chapter_revisions={},
            chapter_titles={},
        )
        session.add(project)
        await session.flush()
        session.add_all([historical_version, current_version])
        await session.flush()
        chapters: list[ChapterRecord] = []
        revisions: list[ChapterRevisionRecord] = []
        historical_map: dict[str, str] = {}
        current_map: dict[str, str] = {}
        historical_titles: dict[str, str] = {}
        current_titles: dict[str, str] = {}
        for ordinal in range(1, spec.chapters + 1):
            chapter_id = _uuid(spec, "chapter", ordinal)
            historical_revision_id = _uuid(spec, "revision-history", ordinal)
            current_revision_id = _uuid(spec, "revision-current", ordinal)
            chapter = ChapterRecord(
                id=chapter_id,
                project_id=project_id,
                ordinal=ordinal,
                display_ordinal=ordinal,
                title=f"数据库标题{ordinal:04d}",
                current_revision_id=current_revision_id,
            )
            chapters.append(chapter)
            revisions.extend(
                [
                    ChapterRevisionRecord(
                        id=historical_revision_id,
                        chapter_id=chapter_id,
                        base_version_id=historical_version_id,
                        body=_chapter_body(spec, ordinal, "历史"),
                        status="committed",
                    ),
                    ChapterRevisionRecord(
                        id=current_revision_id,
                        chapter_id=chapter_id,
                        base_version_id=current_version_id,
                        supersedes_id=historical_revision_id,
                        body=_chapter_body(spec, ordinal, "当前"),
                        status="committed",
                    ),
                ]
            )
            historical_map[str(chapter_id)] = str(historical_revision_id)
            current_map[str(chapter_id)] = str(current_revision_id)
            historical_titles[str(chapter_id)] = f"历史章节标题{ordinal:04d}"
            current_titles[str(chapter_id)] = f"当前章节标题{ordinal:04d}"
        session.add_all(chapters)
        await session.flush()
        session.add_all(revisions)
        await session.flush()
        historical_version.chapter_revisions = historical_map
        historical_version.chapter_titles = historical_titles
        current_version.chapter_revisions = current_map
        current_version.chapter_titles = current_titles
        project.current_version_id = current_version_id
        session.add(
            WritingSessionRecord(
                id=_uuid(spec, "review-session"),
                project_id=project_id,
                base_version_id=current_version_id,
                direction="脱敏性能审核证据",
                author_guidance={},
                story_intent={},
                must_include=[],
                avoid=[],
                target_min_chars=1000,
                target_max_chars=6000,
                scene_target_chars=3000,
                chapter_min_chars=3000,
                chapter_target_chars=5000,
                chapter_max_chars=7000,
                status="review",
                stage="review",
                raw_body="脱敏候选正文",
                raw_sha256="0" * 64,
                review_package={
                    "segments": [
                        {
                            "ordinal": 1,
                            "title": "审核证据",
                            "body": "候选中的潮印性能锚点需要作者判断。",
                        }
                    ],
                    "checker_report": {
                        "issues": [{"quote": "潮印性能锚点", "status": "open"}]
                    },
                    "reader_feedback": {"confusion": "锚点代价仍待确认"},
                },
            )
        )
    return SeededFixture(
        project_id=project_id,
        current_version_id=current_version_id,
        first_chapter_id=_uuid(spec, "chapter", 1),
        chapter_characters=chapter_characters,
    )


async def _delete_fixture_rows(session: AsyncSession, project_id: UUID) -> None:
    chapter_ids = select(ChapterRecord.id).where(ChapterRecord.project_id == project_id)
    await session.execute(
        delete(SearchDocumentRecord).where(SearchDocumentRecord.project_id == project_id)
    )
    await session.execute(
        delete(WritingSessionRecord).where(WritingSessionRecord.project_id == project_id)
    )
    await session.execute(
        delete(ChapterRevisionRecord).where(
            ChapterRevisionRecord.chapter_id.in_(chapter_ids)
        )
    )
    await session.execute(delete(ChapterRecord).where(ChapterRecord.project_id == project_id))
    await session.execute(
        delete(StateVersionRecord).where(StateVersionRecord.project_id == project_id)
    )
    await session.execute(delete(ProjectRecord).where(ProjectRecord.id == project_id))


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


async def _timed(operation: Callable[[], Awaitable[Any]]) -> tuple[float, Any]:
    started = time.perf_counter()
    result = await operation()
    return (time.perf_counter() - started) * 1000, result


async def _latency_measurement(
    operation: Callable[[], Awaitable[Any]],
    *,
    samples: int,
    budget_ms: float,
) -> tuple[dict[str, Any], Any]:
    cold_ms, last = await _timed(operation)
    hot: list[float] = []
    for _ in range(samples):
        elapsed, last = await _timed(operation)
        hot.append(elapsed)
    p95_ms = _p95(hot)
    return (
        {
            "cold_ms": round(cold_ms, 3),
            "hot_p95_ms": round(p95_ms, 3),
            "hot_samples_ms": [round(value, 3) for value in hot],
            "budget_ms": budget_ms,
            "passed": max(cold_ms, p95_ms) < budget_ms,
        },
        last,
    )


async def _resource_measurement(
    operation: Callable[[], Awaitable[Any]],
    *,
    budget_seconds: float,
    memory_budget_mib: float,
) -> tuple[dict[str, Any], Any]:
    gc.collect()
    tracemalloc.start()
    started = time.perf_counter()
    try:
        result = await operation()
        elapsed = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    peak_mib = peak / MIB
    return (
        {
            "elapsed_seconds": round(elapsed, 3),
            "peak_python_mib": round(peak_mib, 3),
            "budget_seconds": budget_seconds,
            "memory_budget_mib": memory_budget_mib,
            "passed": elapsed < budget_seconds and peak_mib < memory_budget_mib,
        },
        result,
    )


async def _run(database_url: str) -> dict[str, Any]:
    database = Database(database_url)
    seeded: dict[str, SeededFixture] = {}
    measurements: dict[str, Any] = {}
    try:
        async with database.session() as session:
            postgres_version = str(await session.scalar(text("SHOW server_version")))
            pg_trgm_version = str(
                await session.scalar(
                    text("SELECT extversion FROM pg_extension WHERE extname = 'pg_trgm'")
                )
            )
        for spec in FIXTURE_SPECS:
            seeded[spec.name] = await _seed_fixture(database, spec)

        large = seeded["longform-500"]
        medium = seeded["longform-200"]

        async def chapter_directory() -> list[dict[str, Any]]:
            async with database.session() as session:
                return await ProjectQueryService(session).list_chapters(large.project_id)

        measurements["chapter_directory_500"], directory = await _latency_measurement(
            chapter_directory, samples=10, budget_ms=500,
        )
        measurements["chapter_directory_500"].update({"chapter_count": len(directory)})
        measurements["chapter_directory_500"]["passed"] = bool(
            measurements["chapter_directory_500"]["passed"] and len(directory) == 500
        )

        async def version_summaries() -> list[dict[str, Any]]:
            async with database.session() as session:
                return await ProjectQueryService(session).list_versions(large.project_id)

        measurements["version_summaries_500"], versions = await _latency_measurement(
            version_summaries, samples=10, budget_ms=500,
        )
        measurements["version_summaries_500"].update({"version_count": len(versions)})
        measurements["version_summaries_500"]["passed"] = bool(
            measurements["version_summaries_500"]["passed"]
            and len(versions) == 2
            and all(item["chapter_count"] == 500 for item in versions)
        )

        async def reader_manifest() -> dict[str, Any]:
            async with database.session() as session:
                return await ReaderViewService(session).build_manifest(large.project_id)

        measurements["reader_manifest_500"], manifest = await _latency_measurement(
            reader_manifest,
            samples=10,
            budget_ms=500,
        )
        measurements["reader_manifest_500"].update(
            {
                "chapter_count": len(manifest["chapters"]),
                "contains_body_field": any("body" in item for item in manifest["chapters"]),
            }
        )
        measurements["reader_manifest_500"]["passed"] = bool(
            measurements["reader_manifest_500"]["passed"]
            and len(manifest["chapters"]) == 500
            and not measurements["reader_manifest_500"]["contains_body_field"]
        )

        async def reader_chapter() -> dict[str, Any]:
            async with database.session() as session:
                return await ReaderViewService(session).build_chapter(
                    large.project_id,
                    large.first_chapter_id,
                )

        measurements["reader_single_chapter_500"], chapter = await _latency_measurement(
            reader_chapter,
            samples=20,
            budget_ms=250,
        )
        measurements["reader_single_chapter_500"].update(
            {
                "response_characters": len(chapter["body"]),
                "expected_characters": large.chapter_characters,
            }
        )
        measurements["reader_single_chapter_500"]["passed"] = bool(
            measurements["reader_single_chapter_500"]["passed"]
            and len(chapter["body"]) == large.chapter_characters
        )

        async def rebuild_search(project_id: UUID) -> Any:
            async with database.session() as session, session.begin():
                return await LocalSearchService(session).rebuild_project_index(project_id)

        rebuild_ms, rebuilt = await _timed(lambda: rebuild_search(medium.project_id))
        measurements["search_rebuild_200"] = {
            "elapsed_ms": round(rebuild_ms, 3),
            "document_count": rebuilt.document_count,
            "index_sha256": rebuilt.index_sha256,
            "provider_called": False,
            "passed": rebuilt.document_count > 400,
        }

        async def search() -> dict[str, Any]:
            async with database.session() as session:
                return await LocalSearchService(session).search(
                    medium.project_id,
                    "潮印性能锚点",
                    limit=50,
                )

        measurements["global_search_1m"], search_result = await _latency_measurement(
            search,
            samples=20,
            budget_ms=300,
        )
        measurements["global_search_1m"].update(
            {
                "total": search_result["total"],
                "first_page_count": len(search_result["results"]),
                "provider_called": search_result["provider_called"],
            }
        )
        measurements["global_search_1m"]["passed"] = bool(
            measurements["global_search_1m"]["passed"]
            and len(search_result["results"]) <= 50
            and search_result["provider_called"] is False
        )

        async def blueprint_characters() -> dict[str, Any]:
            async with database.session() as session:
                return await BlueprintManagementService(session).blueprint_section(
                    large.project_id,
                    "characters",
                )

        measurements["blueprint_characters_500"], blueprint = await _latency_measurement(
            blueprint_characters,
            samples=10,
            budget_ms=500,
        )
        measurements["blueprint_characters_500"].update(
            {
                "character_count": len(blueprint["data"]["characters"]),
                "section": blueprint["section"],
            }
        )
        measurements["blueprint_characters_500"]["passed"] = bool(
            measurements["blueprint_characters_500"]["passed"]
            and len(blueprint["data"]["characters"]) == 120
        )

        import_prefix = "# 第1章 十兆导入性能\n\n"
        import_sentence = "潮印导入预览只做本地解析，不触发任何Provider。"
        import_text = import_prefix + import_sentence * math.ceil(
            (10 * MIB) / len(import_sentence.encode("utf-8"))
        )
        import_payload = import_text.encode("utf-8")[: 10 * MIB]

        async def import_preview() -> dict[str, Any]:
            decoded = decode_upload(import_payload, "auto")
            return build_import_preview(import_payload, decoded)

        measurements["import_preview_10mib"], preview = await _resource_measurement(
            import_preview,
            budget_seconds=30,
            memory_budget_mib=256,
        )
        measurements["import_preview_10mib"].update(
            {
                "input_bytes": len(import_payload),
                "chapter_count": preview["chapter_count"],
            }
        )

        async def export(format_name: str) -> tuple[str, str | bytes]:
            async with database.session() as session:
                service = DataPortabilityService(session)
                operation = getattr(service, f"export_{format_name}")
                return await operation(
                    large.project_id,
                    version_id=large.current_version_id,
                )

        for format_name, memory_budget in (
            ("markdown", 256),
            ("text", 256),
            ("docx", 512),
            ("epub", 512),
        ):
            key = f"export_{format_name}_500"
            measurements[key], artifact = await _resource_measurement(
                lambda format_name=format_name: export(format_name),
                budget_seconds=30,
                memory_budget_mib=memory_budget,
            )
            filename, content = artifact
            size = len(content.encode("utf-8") if isinstance(content, str) else content)
            measurements[key].update({"filename": filename, "output_bytes": size})
            if format_name in {"docx", "epub"}:
                assert isinstance(content, bytes)
                with zipfile.ZipFile(BytesIO(content)) as archive:
                    names = set(archive.namelist())
                    if format_name == "docx":
                        required = {"[Content_Types].xml", "word/document.xml"}
                    else:
                        container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
                        rootfile = container.find(
                            ".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile"
                        )
                        if rootfile is None or not rootfile.get("full-path"):
                            required = {"mimetype", "META-INF/container.xml", "__missing__.opf"}
                        else:
                            package_path = rootfile.attrib["full-path"]
                            base = package_path.rsplit("/", 1)[0]
                            required = {
                                "mimetype",
                                "META-INF/container.xml",
                                package_path,
                                f"{base}/nav.xhtml",
                            }
                measurements[key]["openable_zip"] = required.issubset(names)
                measurements[key]["passed"] = bool(
                    measurements[key]["passed"] and required.issubset(names)
                )

        passed = all(bool(item.get("passed")) for item in measurements.values())
        return {
            "schema_version": "longform-performance-gate-v1",
            "generated_at": datetime.now(UTC).isoformat(),
            "environment": {
                "hostname": socket.gethostname(),
                "platform": platform.platform(),
                "processor": platform.processor(),
                "python": platform.python_version(),
                "postgresql": postgres_version,
                "pg_trgm": pg_trgm_version,
            },
            "fixtures": [asdict(item) for item in FIXTURE_SPECS],
            "measurements": measurements,
            "provider_dispatch_count": 0,
            "passed": passed,
        }
    finally:
        for item in seeded.values():
            async with database.session() as session, session.begin():
                await _delete_fixture_rows(session, item.project_id)
        await database.dispose()


def _database_url(environment_name: str) -> str:
    value = os.getenv(environment_name, "").strip()
    if not value:
        raise SystemExit(f"{environment_name} is required")
    database_name = make_url(value).database or ""
    if not database_name.endswith("_test"):
        raise SystemExit("performance gate requires a database name ending in _test")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run deterministic long-form performance budgets against test PostgreSQL."
    )
    parser.add_argument(
        "--database-url-env",
        default="NOVEL_WRITER_TEST_DATABASE_URL",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=(
            PROJECT_ROOT
            / "docs"
            / "reports"
            / f"performance-gate-{datetime.now(UTC).date().isoformat()}.json"
        ),
    )
    args = parser.parse_args()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    report = asyncio.run(_run(_database_url(args.database_url_env)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
