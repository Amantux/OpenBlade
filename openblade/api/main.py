"""FastAPI application for OpenBlade."""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from openblade.api import (
    nas_config,
    routes_aml_access,
    routes_aml_advanced,
    routes_aml_auth,
    routes_aml_blades,
    routes_aml_diagnostics,
    routes_aml_drives,
    routes_aml_events,
    routes_aml_firmware,
    routes_aml_library,
    routes_aml_media,
    routes_aml_operations,
    routes_aml_partitions,
    routes_aml_physical,
    routes_aml_system,
    routes_archive,
    routes_catalog,
    routes_dashboard,
    routes_health,
    routes_iblade,
    routes_inventory,
    routes_jobs,
    routes_libraries,
    routes_ltfs,
    routes_proxy,
    routes_rbac,
    routes_restore,
    routes_safety,
    routes_tape_ops,
    routes_tapes,
    routes_upload,
    routes_virtual_fs,
    routes_volume_groups,
)
from openblade.api.routes_gateway import router as gateway_router
from openblade.api.service_auth import ServiceTokenForbiddenError, controller_only_error
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
from openblade.nas.version import OPENBLADE_VERSION


class ErrorResponse(BaseModel):
    error: str
    detail: str


app = FastAPI(title="OpenBlade", version=OPENBLADE_VERSION)

# ---------------------------------------------------------------------------
# Security middleware
# ---------------------------------------------------------------------------
_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get("OPENBLADE_CORS_ORIGINS", "http://localhost:5173,http://localhost:80").split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Openblade-Service-Token"],
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next: object) -> Response:
    response: Response = await call_next(request)  # type: ignore[operator]
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    if os.environ.get("OPENBLADE_ENV", "development").lower() == "production":
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return response

app.include_router(routes_health.router, tags=["health"])
app.include_router(routes_inventory.router, prefix="/inventory", tags=["inventory"])
app.include_router(routes_tapes.router, prefix="/cartridges", tags=["cartridges"])
app.include_router(routes_volume_groups.router, prefix="/volume-groups", tags=["volume-groups"])
app.include_router(routes_archive.router, prefix="/archive", tags=["archive"])
app.include_router(routes_catalog.router, prefix="/catalog", tags=["catalog"])
app.include_router(routes_dashboard.router, prefix="/dashboard", tags=["dashboard"])
app.include_router(routes_ltfs.router, prefix="/ltfs", tags=["ltfs"])
app.include_router(routes_restore.router, prefix="/restore", tags=["restore"])
app.include_router(routes_jobs.router, prefix="/jobs", tags=["jobs"])
app.include_router(routes_libraries.router)
app.include_router(routes_safety.router)
app.include_router(routes_iblade.router, prefix="/iblade", tags=["iblade"])
app.include_router(gateway_router)
app.include_router(routes_virtual_fs.router, prefix="/virtual", tags=["virtual"])
app.include_router(routes_tape_ops.router)
app.include_router(nas_config.router)
app.include_router(routes_aml_auth.router, prefix="/aml", tags=["aml-auth"])
app.include_router(routes_rbac.router, prefix="/aml", tags=["rbac"])
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
app.include_router(routes_upload.router)


@app.on_event("startup")
async def initialize_aml_state() -> None:
    from openblade.api.aml_state import ensure_initialized

    ensure_initialized(get_context().config.db_url)


def _is_aml_compatible_path(path: str) -> bool:
    return path.startswith("/aml") or path.startswith("/iblade")



def _aml_error_payload(status_code: int, detail: object) -> dict[str, object]:
    code_map = {
        400: "AML_BAD_REQUEST",
        401: "AML_AUTH_REQUIRED",
        403: "AML_FORBIDDEN",
        404: "AML_NOT_FOUND",
        409: "AML_CONFLICT",
        422: "AML_VALIDATION_ERROR",
        503: "AML_UNAVAILABLE",
    }
    action_map = {
        400: "Review the request payload and retry.",
        401: "Authenticate and retry the request.",
        403: "Use an account with the required privileges.",
        404: "Verify the requested resource identifier and retry.",
        409: "Resolve the conflicting state and retry.",
        422: "Correct the submitted values and retry.",
        503: "Retry after the service becomes available.",
    }
    if isinstance(detail, dict) and {"code", "summary", "description", "action", "customCode"}.issubset(detail):
        return detail
    description = detail if isinstance(detail, str) else str(detail)
    return {
        "code": code_map.get(status_code, "AML_ERROR"),
        "summary": description,
        "description": description,
        "action": action_map.get(status_code, "Review the error details and retry."),
        "customCode": None,
    }


@app.exception_handler(HTTPException)
async def handle_http_exception(request: Request, exc: HTTPException):
    if not _is_aml_compatible_path(request.url.path):
        return await http_exception_handler(request, exc)
    return JSONResponse(status_code=exc.status_code, content=_aml_error_payload(exc.status_code, exc.detail))


@app.exception_handler(RequestValidationError)
async def handle_request_validation_error(request: Request, exc: RequestValidationError):
    if not _is_aml_compatible_path(request.url.path):
        return await request_validation_exception_handler(request, exc)
    description = "; ".join(
        f"{'.'.join(str(part) for part in err.get('loc', []))}: {err.get('msg', 'Invalid value')}"
        for err in exc.errors()
    ) or "Validation error"
    return JSONResponse(status_code=422, content=_aml_error_payload(422, description))


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


@app.exception_handler(ServiceTokenForbiddenError)
async def handle_service_token_forbidden(_: Request, __: ServiceTokenForbiddenError) -> JSONResponse:
    return JSONResponse(status_code=403, content=controller_only_error())


@app.get("/health")
async def health() -> dict[str, str]:
    context = get_context()
    return {"status": "ok", "backend": context.config.backend.value}
