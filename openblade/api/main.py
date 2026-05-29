"""FastAPI application for OpenBlade."""

from __future__ import annotations

import os
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from openblade.api import (
    aml_scope,
    library_context,
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
    routes_aml_matrix_fallback,
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
    routes_storage_compat,
    routes_tape_ops,
    routes_tapes,
    routes_upload,
    routes_virtual_fs,
    routes_volume_groups,
)
from openblade.api.aml_latency import (
    apply_request_latency,
    capture_request_latency_metric,
    should_capture_latency_metrics,
)
from openblade.api.routes_gateway import router as gateway_router
from openblade.api.routes_test_runner import router as test_runner_router
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
    for o in os.environ.get(
        "OPENBLADE_CORS_ORIGINS", "http://localhost:5173,http://localhost:80"
    ).split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "X-Openblade-Service-Token",
        "X-OpenBlade-Library-Id",
    ],
)


@app.middleware("http")
async def apply_forwarded_root_path(request: Request, call_next: object) -> Response:
    forwarded_prefix = request.headers.get("x-forwarded-prefix", "").strip()
    if forwarded_prefix.startswith("/"):
        request.scope["root_path"] = forwarded_prefix.rstrip("/")
    return await call_next(request)  # type: ignore[operator]


@app.middleware("http")
async def add_security_headers(request: Request, call_next: object) -> Response:
    response: Response = await call_next(request)  # type: ignore[operator]
    response.headers["X-Content-Type-Options"] = "nosniff"
    if request.url.path.startswith("/docs") or request.url.path.startswith("/redoc"):
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
    else:
        response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    if os.environ.get("OPENBLADE_ENV", "development").lower() == "production":
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return response


@app.middleware("http")
async def bind_active_library_context(request: Request, call_next: object) -> Response:
    token = library_context.set_active_library_id(request.headers.get("X-OpenBlade-Library-Id", ""))
    try:
        return await call_next(request)  # type: ignore[operator]
    finally:
        library_context.reset_active_library_id(token)


@app.middleware("http")
async def apply_aml_emulator_latency(request: Request, call_next: object) -> Response:
    if not should_capture_latency_metrics(request.url.path):
        return await call_next(request)  # type: ignore[operator]

    start = time.perf_counter()
    simulated_delay = await apply_request_latency(request)
    status_code = 500
    try:
        response: Response = await call_next(request)  # type: ignore[operator]
        status_code = response.status_code
        return response
    finally:
        route = request.scope.get("route")
        endpoint = str(getattr(route, "path", request.url.path))
        capture_request_latency_metric(
            method=request.method,
            endpoint=endpoint,
            status_code=status_code,
            duration_seconds=time.perf_counter() - start,
            simulated_delay_seconds=simulated_delay,
        )


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
# Compatibility shims for frontend and i3 tests that expect /storage and /restore/plan
app.include_router(routes_storage_compat.router)
# Also mount NAS config under /storage for UI compatibility
app.include_router(nas_config.router, prefix="/storage")
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
app.include_router(test_runner_router)
routes_aml_matrix_fallback.register_missing_matrix_routes(app)


@app.on_event("startup")
async def initialize_aml_state() -> None:
    from openblade.api.aml_state import ensure_initialized

    context = get_context()
    ensure_initialized(
        context.config.db_url,
        emulator_latency_profile=context.config.emulator_latency_profile,
        emulator_latency_enabled=context.config.emulator_latency_enabled,
    )


def _is_aml_compatible_path(path: str) -> bool:
    return path.startswith("/aml") or path.startswith("/iblade")


_STRICT_NON_MATRIX_ALLOWED_PATHS = frozenset(
    {
        "/health",
        "/openapi.json",
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
    }
)


@app.middleware("http")
async def enforce_scalar_api_scope(request: Request, call_next: object) -> Response:
    if request.method.upper() == "OPTIONS":
        return await call_next(request)  # type: ignore[operator]

    context = get_context()
    if not context.config.scalar_api_only:
        return await call_next(request)  # type: ignore[operator]

    path = aml_scope.normalize_aml_path(request.url.path)
    if path in _STRICT_NON_MATRIX_ALLOWED_PATHS:
        return await call_next(request)  # type: ignore[operator]

    if path.startswith("/aml") or path.startswith("/iblade"):
        if aml_scope.is_matrix_endpoint(request.method, path):
            return await call_next(request)  # type: ignore[operator]
        return JSONResponse(status_code=404, content=_aml_error_payload(404, "Endpoint not available in matrix scope"))

    return JSONResponse(status_code=404, content={"detail": "Not Found"})


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
    if isinstance(detail, dict) and {
        "code",
        "summary",
        "description",
        "action",
        "customCode",
    }.issubset(detail):
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
    return JSONResponse(
        status_code=exc.status_code, content=_aml_error_payload(exc.status_code, exc.detail)
    )


@app.exception_handler(RequestValidationError)
async def handle_request_validation_error(request: Request, exc: RequestValidationError):
    if not _is_aml_compatible_path(request.url.path):
        return await request_validation_exception_handler(request, exc)
    description = (
        "; ".join(
            f"{'.'.join(str(part) for part in err.get('loc', []))}: {err.get('msg', 'Invalid value')}"
            for err in exc.errors()
        )
        or "Validation error"
    )
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
async def handle_service_token_forbidden(
    _: Request, __: ServiceTokenForbiddenError
) -> JSONResponse:
    return JSONResponse(status_code=403, content=controller_only_error())


@app.get("/health")
async def health() -> dict[str, str]:
    context = get_context()
    return {"status": "ok", "backend": context.config.backend.value}


_OPENAPI_CACHE: dict[str, dict[str, object]] = {}


def _collect_component_refs(payload: object) -> set[tuple[str, str]]:
    refs: set[tuple[str, str]] = set()
    if isinstance(payload, dict):
        raw_ref = payload.get("$ref")
        if isinstance(raw_ref, str) and raw_ref.startswith("#/components/"):
            parts = raw_ref.split("/")
            if len(parts) >= 4:
                refs.add((parts[2], parts[3]))
        for value in payload.values():
            refs.update(_collect_component_refs(value))
    elif isinstance(payload, list):
        for item in payload:
            refs.update(_collect_component_refs(item))
    return refs


def _prune_unreferenced_components(schema: dict[str, object]) -> dict[str, object]:
    components = schema.get("components")
    if not isinstance(components, dict):
        return schema

    queue = list(_collect_component_refs(schema.get("paths", {})))
    seen: set[tuple[str, str]] = set()
    while queue:
        kind, name = queue.pop()
        key = (kind, name)
        if key in seen:
            continue
        bucket = components.get(kind)
        if not isinstance(bucket, dict):
            continue
        component_payload = bucket.get(name)
        if component_payload is None:
            continue
        seen.add(key)
        queue.extend(_collect_component_refs(component_payload))

    pruned: dict[str, Any] = {}
    for kind, bucket in components.items():
        if not isinstance(bucket, dict):
            continue
        if kind == "securitySchemes":
            pruned[kind] = bucket
            continue
        kept = {name: payload for name, payload in bucket.items() if (kind, name) in seen}
        if kept:
            pruned[kind] = kept
    schema["components"] = pruned
    return schema


def _filtered_scalar_openapi(schema: dict[str, object]) -> dict[str, object]:
    paths = schema.get("paths")
    if not isinstance(paths, dict):
        return schema

    allowed_matrix_paths = {
        aml_scope.normalize_aml_path(path) for _, path in aml_scope.matrix_endpoint_set()
    }
    allowed_paths = allowed_matrix_paths | {"/health"}
    schema["paths"] = {
        path: value for path, value in paths.items() if aml_scope.normalize_aml_path(path) in allowed_paths
    }
    return _prune_unreferenced_components(schema)


def custom_openapi() -> dict[str, object]:
    context = get_context()
    cache_key = "scalar" if context.config.scalar_api_only else "default"
    cached = _OPENAPI_CACHE.get(cache_key)
    if cached is not None:
        return cached

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    if context.config.scalar_api_only:
        schema = _filtered_scalar_openapi(schema)
    _OPENAPI_CACHE[cache_key] = schema
    return schema


app.openapi = custom_openapi
