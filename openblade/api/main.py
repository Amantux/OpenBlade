"""FastAPI application for OpenBlade."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from openblade.api import (
    routes_aml_access,
    routes_aml_advanced,
    routes_aml_auth,
    routes_aml_blades,
    routes_aml_diagnostics,
    routes_aml_drives,
    routes_aml_events,
    routes_aml_firmware,
    routes_aml_library,
    routes_aml_system,
    routes_aml_media,
    routes_aml_operations,
    routes_aml_partitions,
    routes_aml_physical,
    routes_archive,
    routes_proxy,
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
app.include_router(routes_aml_auth.router, prefix="/aml", tags=["aml-auth"])
app.include_router(routes_aml_access.router, prefix="/aml", tags=["aml-access"])
app.include_router(routes_aml_advanced.router, prefix="/aml", tags=["aml-advanced"])
app.include_router(routes_aml_events.router, prefix="/aml", tags=["aml-events"])
app.include_router(routes_aml_firmware.router, prefix="/aml", tags=["aml-firmware"])
app.include_router(routes_aml_library.router, prefix="/aml", tags=["aml-library"])
app.include_router(routes_aml_blades.router, prefix="/aml", tags=["aml-blades"])
app.include_router(routes_aml_diagnostics.router, prefix="/aml", tags=["aml-diagnostics"])
app.include_router(routes_aml_drives.router, prefix="/aml", tags=["aml-drives"])
app.include_router(routes_aml_partitions.router, prefix="/aml", tags=["aml-partitions"])
app.include_router(routes_aml_physical.router, prefix="/aml", tags=["aml-physical"])
app.include_router(routes_aml_media.router, prefix="/aml", tags=["aml-media"])
app.include_router(routes_aml_operations.router, prefix="/aml", tags=["aml-operations"])
app.include_router(routes_aml_system.router, prefix="/aml", tags=["aml-system"])
app.include_router(routes_proxy.router)


@app.on_event("startup")
async def initialize_aml_state() -> None:
    from openblade.api.aml_state import ensure_initialized

    ensure_initialized(get_context().config.db_url)


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
