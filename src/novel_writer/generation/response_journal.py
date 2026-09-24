"""Durable response receipts; replay here means local persistence, never provider I/O."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy.engine import make_url

from novel_writer.core.private_files import atomic_private_write
from novel_writer.generation.content import fingerprint, json_text
from novel_writer.services.errors import WorkflowError


class ResponseJournal:
    def __init__(self, root: Path, database_url: str) -> None:
        database = make_url(database_url)
        identity = [database.drivername, database.host, database.port, database.database]
        self.root = root / fingerprint(identity)[:24]

    def save(
        self, batch_id: UUID, call_id: UUID, request_sha256: str, response: dict[str, Any]
    ) -> dict[str, Any]:
        entry = {
            "version": 1,
            "batch_id": str(batch_id),
            "call_id": str(call_id),
            "model_request_sha256": request_sha256,
            "response": response,
            "received_at": datetime.now(UTC).isoformat(),
        }
        path = self.root / f"{call_id}.json"
        if path.exists():
            previous = self.read(path)
            if any(previous[k] != entry[k] for k in entry if k != "received_at"):
                raise WorkflowError("响应缓冲已有不同内容，拒绝覆盖")
            return previous
        entry["sha256"] = fingerprint(entry)
        atomic_private_write(path, json_text(entry).encode("utf-8"))
        return entry

    def read(self, path: Path) -> dict[str, Any]:
        import json

        if path.is_symlink():
            raise WorkflowError("响应缓冲不能使用符号链接")
        entry: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(entry, dict) or not isinstance(entry.get("response"), dict):
            raise WorkflowError("响应缓冲结构无效")
        if (
            entry.get("version") != 1
            or path.stem != str(UUID(entry["call_id"]))
            or entry.get("sha256") != fingerprint({k: v for k, v in entry.items() if k != "sha256"})
            or entry["response"].get("sha256")
            != fingerprint({k: v for k, v in entry["response"].items() if k != "sha256"})
        ):
            raise WorkflowError("响应缓冲校验失败，拒绝恢复")
        return entry

    def pending(self) -> list[Path]:
        return sorted(self.root.glob("*.json"))

    def remove(self, call_id: UUID) -> None:
        (self.root / f"{call_id}.json").unlink(missing_ok=True)
