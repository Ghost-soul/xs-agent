from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from novel_writer.core.logging import get_logger, redact_secrets
from novel_writer.services.errors import (
    ApprovalRequiredError,
    ConflictError,
    NotFoundError,
    WorkflowError,
)

_logger = get_logger("novel_writer.api.errors")


def _record_error(request: Request, error: Exception, status_code: int) -> None:
    _logger.warning(
        "api.error",
        method=request.method,
        path=request.url.path,
        status_code=status_code,
        error_type=type(error).__name__,
        detail=redact_secrets(str(error))[:500],
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(NotFoundError)
    async def not_found(request: Request, error: NotFoundError) -> JSONResponse:
        _record_error(request, error, status.HTTP_404_NOT_FOUND)
        return JSONResponse({"detail": str(error)}, status_code=status.HTTP_404_NOT_FOUND)

    @app.exception_handler(ConflictError)
    async def conflict(request: Request, error: ConflictError) -> JSONResponse:
        _record_error(request, error, status.HTTP_409_CONFLICT)
        return JSONResponse({"detail": str(error)}, status_code=status.HTTP_409_CONFLICT)

    @app.exception_handler(ApprovalRequiredError)
    async def approval_required(request: Request, error: ApprovalRequiredError) -> JSONResponse:
        _record_error(request, error, status.HTTP_409_CONFLICT)
        return JSONResponse({"detail": str(error)}, status_code=status.HTTP_409_CONFLICT)

    @app.exception_handler(WorkflowError)
    async def workflow_error(request: Request, error: WorkflowError) -> JSONResponse:
        _record_error(request, error, status.HTTP_400_BAD_REQUEST)
        return JSONResponse({"detail": str(error)}, status_code=status.HTTP_400_BAD_REQUEST)
