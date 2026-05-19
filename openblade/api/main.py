"""FastAPI application for OpenBlade."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from openblade.api import (
    routes_archive,
    routes_inventory,
    routes_jobs,
    routes_restore,
    routes_tapes,
    routes_volume_groups,
)
from openblade.bootstrap import get_context
from openblade.domain.errors import (
    BarcodeMismatchError,
    CartridgeOfflineError,
    ChecksumMismatchError,
    FileNotFoundError,
    FormatRequiresConfirmationError,
    JobNotFoundError,
    NoScratchMediaError,
    OpenBladeError,
    TapeFullError,
)


class ErrorResponse(BaseModel):
    error: str
    detail: str


app = FastAPI(title="OpenBlade", version="0.1.0")
app.include_router(routes_inventory.router, prefix="/inventory", tags=["inventory"])
app.include_router(routes_tapes.router, prefix="/cartridges", tags=["cartridges"])
app.include_router(routes_volume_groups.router, prefix="/volume-groups", tags=["volume-groups"])
app.include_router(routes_archive.router, prefix="/archive", tags=["archive"])
app.include_router(routes_restore.router, prefix="/restore", tags=["restore"])
app.include_router(routes_jobs.router, prefix="/jobs", tags=["jobs"])


@app.exception_handler(OpenBladeError)
async def handle_openblade_error(_: Request, exc: OpenBladeError) -> JSONResponse:
    status_code = 400
    if isinstance(exc, (FileNotFoundError, JobNotFoundError)):
        status_code = 404
    elif isinstance(exc, CartridgeOfflineError):
        status_code = 409
    elif isinstance(exc, (NoScratchMediaError, TapeFullError)):
        status_code = 503
    elif isinstance(
        exc,
        (BarcodeMismatchError, FormatRequiresConfirmationError, ChecksumMismatchError),
    ):
        status_code = 400
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(error=exc.__class__.__name__, detail=str(exc)).model_dump(),
    )


@app.get("/health")
async def health() -> dict[str, str]:
    context = get_context()
    return {"status": "ok", "backend": context.config.backend.value}
