"""NAS configuration API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from openblade.catalog.db import get_catalog_repository
from openblade.nas.service import NasService
from openblade.nas.sidecar import SidecarResolver
from openblade.nas.types import (
    SidecarValidationError,
    CacheDriveConfig,
    EffectivePolicy,
    NasShareDefinition,
    SourceStreamConfig,
    StoragePolicy,
)

router = APIRouter(prefix="/nas", tags=["NAS Config"])


def get_nas_service(repo=Depends(get_catalog_repository)) -> NasService:
    return NasService(repo)


def _bad_request(exc: ValueError | ValidationError | SidecarValidationError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


class ResolvePolicyRequest(BaseModel):
    directory: str
    share_id: str | None = None


@router.get("/policies", response_model=list[StoragePolicy])
async def list_policies(service: NasService = Depends(get_nas_service)) -> list[StoragePolicy]:
    return service.get_policies()


@router.post("/policies", response_model=StoragePolicy)
async def create_or_update_policy(
    policy: StoragePolicy,
    service: NasService = Depends(get_nas_service),
) -> JSONResponse:
    created = service.get_policy(policy.id) is None
    try:
        saved = service.upsert_policy(policy)
    except ValueError as exc:
        raise _bad_request(exc) from exc
    return JSONResponse(
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        content=saved.model_dump(mode="json"),
    )


@router.get("/policies/{policy_id}", response_model=StoragePolicy)
async def get_policy(policy_id: str, service: NasService = Depends(get_nas_service)) -> StoragePolicy:
    policy = service.get_policy(policy_id)
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Policy {policy_id} not found")
    return policy


@router.delete("/policies/{policy_id}")
async def delete_policy(policy_id: str, service: NasService = Depends(get_nas_service)) -> dict[str, bool]:
    try:
        deleted = service.delete_policy(policy_id)
    except ValueError as exc:
        raise _bad_request(exc) from exc
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Policy {policy_id} not found")
    return {"deleted": True}


@router.get("/cache-drives", response_model=list[CacheDriveConfig])
async def list_cache_drives(
    service: NasService = Depends(get_nas_service),
) -> list[CacheDriveConfig]:
    return service.get_cache_drives()


@router.post("/cache-drives", response_model=CacheDriveConfig)
async def create_or_update_cache_drive(
    drive: CacheDriveConfig,
    service: NasService = Depends(get_nas_service),
) -> JSONResponse:
    created = service.get_cache_drive(drive.id) is None
    try:
        saved = service.upsert_cache_drive(drive)
    except ValueError as exc:
        raise _bad_request(exc) from exc
    return JSONResponse(
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        content=saved.model_dump(mode="json"),
    )


@router.get("/cache-drives/{drive_id}", response_model=CacheDriveConfig)
async def get_cache_drive(
    drive_id: str,
    service: NasService = Depends(get_nas_service),
) -> CacheDriveConfig:
    drive = service.get_cache_drive(drive_id)
    if drive is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Cache drive {drive_id} not found")
    return drive


@router.delete("/cache-drives/{drive_id}")
async def delete_cache_drive(
    drive_id: str,
    service: NasService = Depends(get_nas_service),
) -> dict[str, bool]:
    deleted = service.delete_cache_drive(drive_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Cache drive {drive_id} not found")
    return {"deleted": True}


@router.get("/source-stream", response_model=SourceStreamConfig)
async def get_source_stream_config(
    service: NasService = Depends(get_nas_service),
) -> SourceStreamConfig:
    return service.get_source_stream_config()


@router.put("/source-stream", response_model=SourceStreamConfig)
async def update_source_stream_config(
    config: SourceStreamConfig,
    service: NasService = Depends(get_nas_service),
) -> SourceStreamConfig:
    try:
        return service.update_source_stream_config(config)
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.delete("/source-stream")
async def delete_source_stream_config(
    service: NasService = Depends(get_nas_service),
) -> dict[str, bool]:
    deleted = service.delete_source_stream_config()
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source stream config not found")
    return {"deleted": True}


@router.get("/shares", response_model=list[NasShareDefinition])
async def list_shares(service: NasService = Depends(get_nas_service)) -> list[NasShareDefinition]:
    return service.get_nas_shares()


@router.post("/shares", response_model=NasShareDefinition)
async def create_or_update_share(
    share: NasShareDefinition,
    service: NasService = Depends(get_nas_service),
) -> JSONResponse:
    created = service.get_share(share.path) is None
    try:
        saved = service.upsert_share(share)
    except ValueError as exc:
        raise _bad_request(exc) from exc
    return JSONResponse(
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        content=saved.model_dump(mode="json"),
    )


@router.get("/shares/{share_id:path}", response_model=NasShareDefinition)
async def get_share(
    share_id: str,
    service: NasService = Depends(get_nas_service),
) -> NasShareDefinition:
    share = service.get_share("/" + share_id.lstrip("/"))
    if share is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Share /{share_id.lstrip('/')} not found")
    return share


@router.delete("/shares/{share_id:path}")
async def delete_share(
    share_id: str,
    service: NasService = Depends(get_nas_service),
) -> dict[str, bool]:
    normalized_share_id = "/" + share_id.lstrip("/")
    deleted = service.delete_share(normalized_share_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Share {normalized_share_id} not found")
    return {"deleted": True}


@router.post("/resolve-policy", response_model=EffectivePolicy)
async def resolve_policy(
    request: ResolvePolicyRequest,
    service: NasService = Depends(get_nas_service),
) -> EffectivePolicy:
    normalized_share_id = None if request.share_id is None else "/" + request.share_id.lstrip("/")
    share_default_policy = None

    if normalized_share_id is not None:
        share = service.get_share(normalized_share_id)
        if share is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Share {normalized_share_id} not found",
            )
        if share.default_policy_id is not None:
            share_default_policy = service.get_policy(share.default_policy_id)

    resolver = SidecarResolver(nas_service=service)
    system_default_policy = service.get_policy("balanced")

    try:
        return resolver.resolve_effective_policy(
            directory=request.directory,
            share_default_policy=share_default_policy,
            system_default_policy=system_default_policy,
        )
    except (ValidationError, SidecarValidationError, ValueError) as exc:
        raise _bad_request(exc) from exc
