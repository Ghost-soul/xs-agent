from __future__ import annotations

import argparse
import ast
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src" / "novel_writer"


@dataclass(frozen=True)
class AllowlistEntry:
    owner: Literal[
        "novel_run",
        "controlled_amendment",
        "blueprint",
        "provider_capability",
        "role_transport",
        "genre_generation",
    ]
    capabilities: frozenset[str]


ALLOWLIST: dict[str, AllowlistEntry] = {
    "generation/runtime.py": AllowlistEntry(
        "genre_generation", frozenset({"provider_dispatch", "credential_read"})
    ),
    "api/routes/provider_profiles.py": AllowlistEntry(
        "provider_capability", frozenset({"provider_dispatch", "credential_read"})
    ),
}


RETIRED_PROVIDER_ENDPOINTS = frozenset(
    {
        "/writing-sessions/{session_id}/chapter-brief",
        "/writing-sessions/{session_id}/reader-simulation",
        "/writing-sessions/{session_id}/naturalness-editor",
        "/writing-sessions/{session_id}/checker-issues/revise",
        "/projects/{project_id}/formal-title-renames/prepare",
        "/writing-sessions/{session_id}/formal-title-renames/apply",
    }
)


def _dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _call_capability(call: ast.Call) -> str | None:
    if not isinstance(call.func, ast.Attribute):
        return None
    if call.func.attr == "generate":
        return "provider_dispatch"
    if call.func.attr == "get_api_key":
        return "credential_read"
    if call.func.attr != "get":
        return None
    owner = _dotted_name(call.func.value)
    if owner == "providers" or owner.endswith(".providers"):
        return "provider_lookup"
    return None


def audit_provider_boundaries(source_root: Path = SOURCE_ROOT) -> dict[str, Any]:
    observations: list[dict[str, Any]] = []
    retired_references: list[dict[str, Any]] = []
    for path in sorted(source_root.rglob("*.py")):
        relative = path.relative_to(source_root).as_posix()
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                capability = _call_capability(node)
                if capability is not None:
                    observations.append(
                        {
                            "file": relative,
                            "line": node.lineno,
                            "capability": capability,
                            "call": _dotted_name(node.func),
                        }
                    )
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value in RETIRED_PROVIDER_ENDPOINTS
            ):
                retired_references.append(
                    {"file": relative, "line": node.lineno, "endpoint": node.value}
                )

    observations.sort(
        key=lambda item: (str(item["file"]), int(item["line"]), str(item["capability"]))
    )
    errors: list[str] = []
    observed_by_file: dict[str, set[str]] = {}
    for item in observations:
        relative = str(item["file"])
        capability = str(item["capability"])
        observed_by_file.setdefault(relative, set()).add(capability)
        allowed = ALLOWLIST.get(relative)
        if allowed is None or capability not in allowed.capabilities:
            errors.append(
                f"unapproved {capability}: {relative}:{item['line']} ({item['call']})"
            )
    for item in retired_references:
        errors.append(
            f"retired Provider endpoint remains active: {item['file']}:{item['line']} "
            f"({item['endpoint']})"
        )
    warnings = [
        f"allowlist entry has no matching capability: {relative} / {capability}"
        for relative, entry in sorted(ALLOWLIST.items())
        for capability in sorted(entry.capabilities - observed_by_file.get(relative, set()))
    ]
    allowlist = {
        path: {
            **asdict(entry),
            "capabilities": sorted(entry.capabilities),
        }
        for path, entry in sorted(ALLOWLIST.items())
    }
    return {
        "contract_version": "provider-boundary-audit-v1",
        "source_root": source_root.relative_to(ROOT).as_posix(),
        "status": "error" if errors else "ok",
        "summary": {
            "observations": len(observations),
            "allowlisted_files": len(ALLOWLIST),
            "errors": len(errors),
            "warnings": len(warnings),
        },
        "allowlist": allowlist,
        "observations": observations,
        "retired_endpoint_references": retired_references,
        "errors": errors,
        "warnings": warnings,
    }


def _markdown(report: dict[str, Any]) -> str:
    summary = dict(report["summary"])
    lines = [
        "# Provider Boundary Audit",
        "",
        f"Status: **{report['status']}**",
        "",
        (
            f"Observations: {summary['observations']}; "
            f"errors: {summary['errors']}; warnings: {summary['warnings']}."
        ),
        "",
        "| File | Line | Capability | Call |",
        "| --- | ---: | --- | --- |",
    ]
    for item in report["observations"]:
        lines.append(
            f"| `{item['file']}` | {item['line']} | `{item['capability']}` | "
            f"`{item['call']}` |"
        )
    for heading, key in (("Errors", "errors"), ("Warnings", "warnings")):
        values = list(report[key])
        if values:
            lines.extend(["", f"## {heading}", ""])
            lines.extend(f"- {value}" for value in values)
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Provider execution boundaries")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit_provider_boundaries()
    rendered = (
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.format == "json"
        else _markdown(report)
    )
    if args.output is None:
        print(rendered, end="")
    else:
        target = args.output if args.output.is_absolute() else ROOT / args.output
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered, encoding="utf-8")
    return 1 if report["status"] == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
