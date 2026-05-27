"""Library instance CRUD endpoints."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from openblade.api.library_context import get_library_profile, resolve_emulator_url
from openblade.api.routes_aml_auth import require_auth
from openblade.catalog.db import get_catalog_repository as get_repository
from openblade.catalog.models import LibraryInstance
from openblade.catalog.repository import CatalogRepository

router = APIRouter(prefix="/api/libraries", tags=["libraries"])

class LibraryCreate(BaseModel):
    name: str
    emulator_url: str
    serial_number: str | None = None
    model: str = "Scalar i3"
    enabled: bool = True
    role: str = "primary"
    sort_order: int = 0


class LibraryUpdate(BaseModel):
    name: str | None = None
    emulator_url: str | None = None
    serial_number: str | None = None
    model: str | None = None
    enabled: bool | None = None
    role: str | None = None
    sort_order: int | None = None


class LibraryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    emulator_url: str
    serial_number: str | None
    model: str
    enabled: bool
    role: str
    sort_order: int
    status: str
    drive_count: int
    tape_count: int
    active_job_count: int
    slot_count: int
    occupied_slot_count: int
    slot_utilization_percent: float
    alerts_count: int
    response_ms: float | None
    last_seen_at: str | None


class LibraryProbeResult(BaseModel):
    status: str
    drive_count: int
    tape_count: int
    active_job_count: int
    slot_count: int
    occupied_slot_count: int
    slot_utilization_percent: float
    alerts_count: int
    response_ms: float | None
    last_seen_at: str | None


async def _probe_library(library: LibraryInstance) -> dict[str, object]:
    profile = get_library_profile(library)
    occupied_slot_count = profile["occupied_slot_count"]
    slot_count = profile["slot_count"]
    utilization = round((occupied_slot_count / slot_count) * 100, 1) if slot_count else 0.0
    defaults: dict[str, object] = {
        "status": "offline" if not library.enabled else "error",
        "drive_count": profile["drive_count"],
        "tape_count": occupied_slot_count,
        "active_job_count": profile["active_job_count"],
        "slot_count": slot_count,
        "occupied_slot_count": occupied_slot_count,
        "slot_utilization_percent": utilization,
        "alerts_count": profile["alerts_count"],
        "response_ms": None,
        "last_seen_at": None,
    }
    if not library.enabled:
        return defaults

    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"{resolve_emulator_url(library.emulator_url)}/health")
        response_ms = round((time.perf_counter() - started) * 1000, 2)
        last_seen_at = datetime.now(timezone.utc).isoformat()
        if response.is_success:
            defaults["status"] = "online"
            defaults["response_ms"] = response_ms
            defaults["last_seen_at"] = last_seen_at
            return defaults
        defaults["status"] = "error"
        defaults["response_ms"] = response_ms
        defaults["last_seen_at"] = last_seen_at
        defaults["alerts_count"] = max(profile["alerts_count"], 1)
        return defaults
    except Exception:  # noqa: BLE001 — network/DNS/OS errors must not fail the listing
        defaults["status"] = "offline"
        defaults["alerts_count"] = max(profile["alerts_count"], 1)
        return defaults


def _library_response_payload(library: LibraryInstance, probe: dict[str, object]) -> dict[str, object]:
    return {
        "id": library.id,
        "name": library.name,
        "emulator_url": library.emulator_url,
        "serial_number": library.serial_number,
        "model": library.model,
        "enabled": library.enabled,
        "role": library.role,
        "sort_order": library.sort_order,
        **probe,
    }


def _enabled_library_count(repo: CatalogRepository) -> int:
    return sum(1 for library in repo.list_library_instances() if library.enabled)


@router.get("", response_model=list[LibraryResponse], dependencies=[Depends(require_auth)])
async def list_libraries(repo: CatalogRepository = Depends(get_repository)) -> list[LibraryResponse]:
    libraries = sorted(repo.list_library_instances(), key=lambda library: (library.sort_order, library.name.lower()))
    probes = await asyncio.gather(*(_probe_library(library) for library in libraries))
    return [
        LibraryResponse.model_validate(_library_response_payload(library, probe))
        for library, probe in zip(libraries, probes, strict=False)
    ]


@router.post("", response_model=LibraryResponse, dependencies=[Depends(require_auth)])
async def create_library(
    data: LibraryCreate,
    repo: CatalogRepository = Depends(get_repository),
) -> LibraryResponse:
    library = repo.create_library_instance(**data.model_dump())
    return LibraryResponse.model_validate(_library_response_payload(library, await _probe_library(library)))


@router.get("/{library_id}", response_model=LibraryResponse, dependencies=[Depends(require_auth)])
async def get_library(
    library_id: int,
    repo: CatalogRepository = Depends(get_repository),
) -> LibraryResponse:
    library = repo.get_library_instance(library_id)
    if not library:
        raise HTTPException(status_code=404, detail="Library not found")
    return LibraryResponse.model_validate(_library_response_payload(library, await _probe_library(library)))


@router.put("/{library_id}", response_model=LibraryResponse, dependencies=[Depends(require_auth)])
async def update_library(
    library_id: int,
    data: LibraryUpdate,
    repo: CatalogRepository = Depends(get_repository),
) -> LibraryResponse:
    updates = {key: value for key, value in data.model_dump().items() if value is not None}
    library = repo.get_library_instance(library_id)
    if not library:
        raise HTTPException(status_code=404, detail="Library not found")
    if updates.get("enabled") is False and library.enabled and _enabled_library_count(repo) <= 1:
        raise HTTPException(status_code=400, detail="At least one enabled library is required")
    updated = repo.update_library_instance(library_id, **updates)
    if not updated:
        raise HTTPException(status_code=404, detail="Library not found")
    return LibraryResponse.model_validate(_library_response_payload(updated, await _probe_library(updated)))


@router.delete("/{library_id}", dependencies=[Depends(require_auth)])
async def delete_library(
    library_id: int,
    repo: CatalogRepository = Depends(get_repository),
) -> dict[str, int]:
    library = repo.get_library_instance(library_id)
    if not library:
        raise HTTPException(status_code=404, detail="Library not found")
    if library.enabled and _enabled_library_count(repo) <= 1:
        raise HTTPException(status_code=400, detail="At least one enabled library is required")
    if not repo.delete_library_instance(library_id):
        raise HTTPException(status_code=404, detail="Library not found")
    return {"deleted": library_id}
