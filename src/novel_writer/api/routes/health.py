import os
from pathlib import Path
from typing import Any, Literal

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text

from novel_writer.generation.schemas import LONGFORM_REVISION as NOVEL_REVISION

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["ok", "unavailable"]


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse | JSONResponse:
    database_ok = await request.app.state.database.ping()
    payload = HealthResponse(
        status="ok" if database_ok else "degraded",
        database="ok" if database_ok else "unavailable",
    )
    if database_ok:
        return payload
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=payload.model_dump()
    )


@router.get("/health/live")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", response_model=None)
async def readiness(request: Request) -> dict[str, Any] | JSONResponse:
    checks: dict[str, Any] = {}
    checks["database"] = "ok" if await request.app.state.database.ping() else "unavailable"
    if checks["database"] == "ok":
        try:
            async with request.app.state.database.session() as session:
                current_revision = await session.scalar(
                    text("SELECT version_num FROM alembic_version")
                )
            expected_revision = _alembic_head()
            checks["alembic"] = {
                "status": "ok" if current_revision == expected_revision else "mismatch",
                "current": current_revision,
                "expected": expected_revision,
            }
        except Exception as error:
            checks["alembic"] = {"status": "unavailable", "error": type(error).__name__}
    else:
        checks["alembic"] = {"status": "unavailable"}
    try:
        request.app.state.provider_profile_store.list_profiles()
        checks["provider_profiles"] = "ok"
    except Exception as error:
        checks["provider_profiles"] = f"invalid:{type(error).__name__}"
    content_root = Path(request.app.state.content_store_root)
    checks["content_store"] = (
        "ok" if content_root.is_dir() and os.access(content_root, os.R_OK) else "unavailable"
    )
    checks["generation"] = (
        NOVEL_REVISION
        if not request.app.state.generation.draining
        and await request.app.state.generation.check_owner()
        else "unavailable"
    )
    ready = (
        checks["database"] == "ok"
        and checks["alembic"].get("status") == "ok"
        and checks["provider_profiles"] == "ok"
        and checks["content_store"] == "ok"
        and checks["generation"] != "unavailable"
    )
    payload = {"status": "ok" if ready else "not_ready", "checks": checks}
    if ready:
        return payload
    return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=payload)


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _alembic_head() -> str:
    root = _repository_root()
    head = ScriptDirectory.from_config(Config(str(root / "alembic.ini"))).get_current_head()
    if head is None:
        raise RuntimeError("Alembic has no migration head")
    return head
