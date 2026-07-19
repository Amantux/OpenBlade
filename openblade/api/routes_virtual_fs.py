"""API routes for the mock virtual filesystem."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from openblade.api.routes_aml_auth import require_auth
from openblade.catalog.db import get_catalog_repository
from openblade.catalog.models import AmlUser
from openblade.catalog.repository import CatalogRepository
from openblade.nas.types import (
    HydrationJob,
    HydrationRequest,
    VirtualDirectoryListing,
    VirtualFileEntry,
)
from openblade.nas.virtual_fs import VirtualFilesystem

logger = structlog.get_logger(__name__)
router = APIRouter()


def get_virtual_filesystem(repo: CatalogRepository = Depends(get_catalog_repository)) -> VirtualFilesystem:
    """Return the shared virtual filesystem instance for the active catalog session."""
    service = repo.session.info.get("virtual_filesystem")
    if not isinstance(service, VirtualFilesystem):
        service = VirtualFilesystem(repo)
        repo.session.info["virtual_filesystem"] = service
    return service


@router.get("/ls", response_model=VirtualDirectoryListing)
async def list_virtual_directory(
    path: str = Query("/"),
    _: AmlUser = Depends(require_auth),
    filesystem: VirtualFilesystem = Depends(get_virtual_filesystem),
) -> VirtualDirectoryListing:
    """List a virtual filesystem directory."""
    try:
        return filesystem.list_directory(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/stat", response_model=VirtualFileEntry)
async def stat_virtual_path(
    path: str,
    _: AmlUser = Depends(require_auth),
    filesystem: VirtualFilesystem = Depends(get_virtual_filesystem),
) -> VirtualFileEntry:
    """Return metadata for a virtual filesystem path."""
    try:
        return filesystem.stat_file(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/hydrate", response_model=HydrationJob)
async def create_hydration_job(
    request: HydrationRequest,
    _: AmlUser = Depends(require_auth),
    filesystem: VirtualFilesystem = Depends(get_virtual_filesystem),
) -> HydrationJob:
    """Queue a new mock hydration job."""
    try:
        job = filesystem.request_hydration(request)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info("virtual_fs.job_created", job_id=job.job_id, paths=job.paths)
    return job


@router.get("/jobs", response_model=list[HydrationJob])
async def list_hydration_jobs(
    _: AmlUser = Depends(require_auth),
    filesystem: VirtualFilesystem = Depends(get_virtual_filesystem),
) -> list[HydrationJob]:
    """List all mock hydration jobs."""
    return filesystem.list_hydration_jobs()


@router.get("/jobs/{job_id}", response_model=HydrationJob)
async def get_hydration_job(
    job_id: str,
    _: AmlUser = Depends(require_auth),
    filesystem: VirtualFilesystem = Depends(get_virtual_filesystem),
) -> HydrationJob:
    """Return a single mock hydration job."""
    try:
        return filesystem.get_hydration_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Hydration job {job_id} not found") from exc


@router.delete("/jobs/{job_id}", response_model=HydrationJob)
async def cancel_hydration_job(
    job_id: str,
    _: AmlUser = Depends(require_auth),
    filesystem: VirtualFilesystem = Depends(get_virtual_filesystem),
) -> HydrationJob:
    """Cancel a queued or running mock hydration job."""
    try:
        return filesystem.cancel_hydration_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Hydration job {job_id} not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
