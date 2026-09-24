import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICES = ROOT / "src" / "novel_writer" / "services"
API_ROUTES = ROOT / "src" / "novel_writer" / "api" / "routes"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            values.add(node.module)
        elif isinstance(node, ast.Import):
            values.update(alias.name for alias in node.names)
    return values


def test_retired_execution_modules_are_absent() -> None:
    retired = [
        SERVICES / "reader_scene_execution.py",
        SERVICES / "baseline.py",
        SERVICES / "brief_planning.py",
        SERVICES / "agent_workflow.py",
        SERVICES / "agent_models.py",
        SERVICES / "orchestration.py",
        SERVICES.parent / "providers" / "local_qwen.py",
        SERVICES.parent / "providers" / "fake.py",
        API_ROUTES / "local_models.py",
        API_ROUTES / "baseline.py",
        API_ROUTES / "planning.py",
        API_ROUTES / "legacy.py",
    ]

    assert not [path for path in retired if path.exists()]


def test_retired_title_ledger_is_read_only_audit_asset() -> None:
    for path in SERVICES.rglob("*.py"):
        if path.name in {"project_deletion_inventory.py", "models.py"}:
            continue
        source = path.read_text(encoding="utf-8")
        assert "ChapterTitleBatchCallRecord" not in source, path
        assert "chapter_title_batch_calls" not in source, path


def test_active_api_routes_do_not_import_legacy_services() -> None:
    active = [path for path in API_ROUTES.glob("*.py") if path.name != "legacy.py"]

    for path in active:
        assert not any("services.legacy" in item for item in _imports(path)), path


def test_workflow_state_does_not_import_workflow_service() -> None:
    imports = _imports(SERVICES / "workflow_state.py")

    assert "novel_writer.services.workflow" not in imports


def test_workflow_layers_do_not_import_workflow_service_facade() -> None:
    facade = "novel_writer.services.workflow"

    for path in [
        SERVICES / "workflow_state.py",
        SERVICES / "workflow_persistence.py",
        SERVICES / "project_queries.py",
        SERVICES / "workflow_versions.py",
    ]:
        assert facade not in _imports(path), path


def test_workflow_keeps_version_commands_separate_from_queries() -> None:
    from novel_writer.services.workflow import WorkflowService
    from novel_writer.services.workflow_versions import WorkflowVersionMixin

    assert not hasattr(WorkflowService, "list_versions")
    assert WorkflowService.rollback is WorkflowVersionMixin.rollback
    assert WorkflowService.restart_story is WorkflowVersionMixin.restart_story
