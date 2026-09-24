import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.gzip import GZipMiddleware

from novel_writer import __version__
from novel_writer.api.errors import register_error_handlers
from novel_writer.api.routes.generation import router as generation_router
from novel_writer.api.routes.health import router as health_router
from novel_writer.api.routes.local_tasks import router as local_tasks_router
from novel_writer.api.routes.longform import router as longform_router
from novel_writer.api.routes.management import router as management_router
from novel_writer.api.routes.portability import router as portability_router
from novel_writer.api.routes.project_deletion import router as project_deletion_router
from novel_writer.api.routes.provider_profiles import router as provider_profiles_router
from novel_writer.api.routes.reference_styles import router as reference_styles_router
from novel_writer.api.routes.styles import router as styles_router
from novel_writer.api.routes.workflow import router as workflow_router
from novel_writer.core.config import Settings, get_settings
from novel_writer.core.credentials import create_credential_store
from novel_writer.core.logging import RequestLoggingMiddleware, configure_logging, get_logger
from novel_writer.core.maintenance import storage_lease
from novel_writer.core.request_limits import RequestBodyLimitMiddleware
from novel_writer.core.security import LocalSecurityMiddleware
from novel_writer.db.engine import Database
from novel_writer.generation.runtime import GenerationRuntime
from novel_writer.services.local_tasks import LocalTaskRunner
from novel_writer.services.provider_profiles import ProviderProfileStore


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(
        resolved_settings.log_level,
        resolved_settings.log_dir,
        max_bytes=resolved_settings.log_max_bytes,
        backup_count=resolved_settings.log_backup_count,
    )
    logger = get_logger("novel_writer.app")

    @asynccontextmanager
    async def running_lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "application.starting",
            environment=resolved_settings.environment,
            host=resolved_settings.host,
            port=resolved_settings.port,
            log_file=str(resolved_settings.log_dir / "system.log"),
        )
        app.state.database = Database(resolved_settings.database_url)
        app.state.generation = GenerationRuntime(
            app.state.database,
            app.state.provider_profile_store,
            app.state.credential_store,
            response_root=resolved_settings.content_store_root / "generation-response-buffer",
            recovery_poll_seconds=resolved_settings.generation_recovery_poll_seconds,
            shutdown_grace_seconds=resolved_settings.generation_shutdown_grace_seconds,
        )
        await app.state.generation.initialize()
        local_task_stop: asyncio.Event | None = None
        local_task_worker: asyncio.Task[None] | None = None
        if resolved_settings.local_task_worker_enabled:
            local_task_stop = asyncio.Event()
            local_task_worker = asyncio.create_task(
                LocalTaskRunner(
                    app.state.database,
                    owner=f"local-task-{os.getpid()}",
                    artifact_root=app.state.content_store_root,
                ).run(
                    local_task_stop,
                    poll_seconds=resolved_settings.local_task_poll_seconds,
                )
            )
        logger.info("application.started")
        try:
            yield
        finally:
            deadline = (
                asyncio.get_running_loop().time()
                + resolved_settings.generation_shutdown_grace_seconds
            )
            if local_task_stop is not None:
                local_task_stop.set()
            await app.state.generation.close()
            if local_task_worker is not None:
                try:
                    await asyncio.wait_for(
                        local_task_worker,
                        timeout=max(0.1, deadline - asyncio.get_running_loop().time()),
                    )
                except TimeoutError:
                    logger.warning("local_task.shutdown_deferred_to_lease_recovery")
            await app.state.database.dispose()
            logger.info("application.stopped")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        with storage_lease(resolved_settings.content_store_root):
            async with running_lifespan(app):
                yield

    app = FastAPI(
        title="Novel Writer API",
        version=__version__,
        docs_url="/docs" if resolved_settings.environment == "development" else None,
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    profile_store = ProviderProfileStore(
        resolved_settings.provider_profiles_path,
        legacy_compatible_base_url=(
            str(resolved_settings.openai_compatible_base_url)
            if resolved_settings.openai_compatible_base_url is not None
            else None
        ),
    )
    app.state.provider_profile_store = profile_store
    app.state.providers = profile_store.build_enabled_providers()
    app.state.content_store_root = resolved_settings.content_store_root
    app.state.credential_store = create_credential_store(
        resolved_settings.credential_backend,
        resolved_settings.credentials_root
        or resolved_settings.provider_profiles_path.parent / "credentials",
    )
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_bytes=resolved_settings.max_request_body_bytes,
    )
    app.add_middleware(LocalSecurityMiddleware, settings=resolved_settings)
    app.add_middleware(RequestLoggingMiddleware)
    app.include_router(health_router)
    app.include_router(generation_router)
    app.include_router(longform_router)
    app.include_router(local_tasks_router)
    app.include_router(portability_router)
    app.include_router(project_deletion_router)
    app.include_router(provider_profiles_router)
    app.include_router(reference_styles_router)
    app.include_router(styles_router)
    app.include_router(workflow_router)
    app.include_router(management_router)
    register_error_handlers(app)
    return app
