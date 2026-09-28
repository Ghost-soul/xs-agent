"""Minimal reading API. Only explicit GET routes exist; no background workers."""

import asyncio
from contextlib import asynccontextmanager
from uuid import UUID

import psycopg
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from starlette.middleware.gzip import GZipMiddleware

from reader.database import ReaderDatabase


def create_app(database: ReaderDatabase) -> FastAPI:
    application = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    application.add_middleware(GZipMiddleware, minimum_size=1024)
    slots = asyncio.Semaphore(8)

    @asynccontextmanager
    async def slot():
        try:
            await asyncio.wait_for(slots.acquire(), timeout=2)
        except TimeoutError:
            raise HTTPException(503, "阅读服务繁忙，请稍后重试") from None
        try:
            yield
        finally:
            slots.release()

    @application.exception_handler(psycopg.Error)
    async def database_error(_request, _error):
        # Driver errors may contain credentials or private text; never echo or log them.
        return JSONResponse({"detail": "数据库暂不可用，请核对连接、权限或迁移版本"}, 503)

    @application.get("/health/live")
    async def live():
        return {"status": "ok"}

    @application.get("/health/ready")
    async def ready():
        async with slot():
            await database.ready()
        return {"status": "ok"}

    @application.get("/api/projects")
    async def projects():
        async with slot():
            return await database.projects()

    @application.get("/api/projects/{project_id}/reader/manifest")
    async def manifest(project_id: UUID):
        async with slot():
            return await database.manifest(project_id)

    @application.get("/api/projects/{project_id}/reader/chapters/{chapter_id}")
    async def chapter(project_id: UUID, chapter_id: UUID, version_id: UUID):
        async with slot():
            return await database.chapter(project_id, chapter_id, version_id)

    @application.get("/api/projects/{project_id}/reader/search")
    async def search(
        project_id: UUID, version_id: UUID, q: str = Query(min_length=1, max_length=100)
    ):
        if not q.strip():
            raise HTTPException(422, "请输入搜索内容")
        async with slot():
            return await database.search(project_id, version_id, q.strip())

    return application
