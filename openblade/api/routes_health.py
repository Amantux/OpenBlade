"""Operational health, readiness, and status routes."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from openblade.api.routes_aml_auth import no_auth, require_auth
from openblade.bootstrap import AppContext, get_context
from openblade.catalog.models import AmlUser
from openblade.config import load_config
from openblade.nas.error_codes import KNOWN_ERROR_CODES
from openblade.nas.health_service import HealthService
from openblade.nas.protocol_gateway import get_gateway
from openblade.nas.types import (
    CatalogStatusResponse,
    ErrorCodesResponse,
    HealthResponse,
    LibraryStatusResponse,
    ReadyResponse,
    VersionResponse,
)
from openblade.nas.version import OPENBLADE_VERSION, get_version_info

router = APIRouter()


class SystemConfigSummaryResponse(BaseModel):
    backend: str
    cors_origins: list[str]
    max_upload_bytes: int
    version: str
    nas_enabled: bool
    gateway_enabled: bool
    catalog_db_path: str
    library_count: int


def get_health_service(context: AppContext = Depends(get_context)) -> HealthService:
    return HealthService(repo=context.catalog, library=context.library, ltfs=context.ltfs)


@router.get("/healthz", response_model=HealthResponse, openapi_extra={"no_auth": True})
@no_auth
async def get_health(service: HealthService = Depends(get_health_service)) -> HealthResponse:
    return service.check_health()


@router.get("/readyz", response_model=ReadyResponse, openapi_extra={"no_auth": True})
@no_auth
async def get_ready(service: HealthService = Depends(get_health_service)) -> ReadyResponse:
    return service.check_ready()


@router.get("/version", response_model=VersionResponse, openapi_extra={"no_auth": True})
@no_auth
async def get_version() -> VersionResponse:
    return VersionResponse.model_validate(get_version_info())


@router.get("/error-codes", response_model=ErrorCodesResponse, openapi_extra={"no_auth": True})
@no_auth
async def get_error_codes() -> ErrorCodesResponse:
    return ErrorCodesResponse(error_codes=KNOWN_ERROR_CODES)


@router.get("/status/library", response_model=LibraryStatusResponse)
async def get_library_status(
    _: AmlUser = Depends(require_auth),
    service: HealthService = Depends(get_health_service),
) -> LibraryStatusResponse:
    return service.get_library_status()


@router.get("/status/catalog", response_model=CatalogStatusResponse)
async def get_catalog_status(
    _: AmlUser = Depends(require_auth),
    service: HealthService = Depends(get_health_service),
) -> CatalogStatusResponse:
    return service.get_catalog_status()


@router.get("/system/config-summary", response_model=SystemConfigSummaryResponse)
async def get_system_config_summary(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> SystemConfigSummaryResponse:
    config = load_config()
    cors_origins = [
        origin.strip()
        for origin in os.environ.get("OPENBLADE_CORS_ORIGINS", "http://localhost:5173,http://localhost:80").split(",")
        if origin.strip()
    ]
    gateway = get_gateway()
    return SystemConfigSummaryResponse(
        backend=context.config.backend.value,
        cors_origins=cors_origins,
        max_upload_bytes=int(os.environ.get("OPENBLADE_MAX_UPLOAD_BYTES", str(10 * 1024**3))),
        version=OPENBLADE_VERSION,
        nas_enabled=True,
        gateway_enabled=gateway.status.value != "disabled",
        catalog_db_path=config.db_url.replace("sqlite:///", "") if config.db_url.startswith("sqlite:///") else config.db_url,
        library_count=len(context.catalog.list_library_instances()),
    )
