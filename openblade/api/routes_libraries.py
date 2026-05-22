"""Library instance CRUD endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from openblade.api.routes_aml_auth import require_auth
from openblade.catalog.db import get_catalog_repository as get_repository
from openblade.catalog.repository import CatalogRepository

router = APIRouter(prefix="/api/libraries", tags=["libraries"])


class LibraryCreate(BaseModel):
    name: str
    emulator_url: str
    serial_number: Optional[str] = None
    model: str = "Scalar i3"


class LibraryUpdate(BaseModel):
    name: Optional[str] = None
    emulator_url: Optional[str] = None
    serial_number: Optional[str] = None
    model: Optional[str] = None
    enabled: Optional[bool] = None


class LibraryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    emulator_url: str
    serial_number: Optional[str]
    model: str
    enabled: bool
    status: str
    drive_count: int
    tape_count: int


def _library_response_payload(library: object) -> dict[str, object]:
    return {
        "id": getattr(library, "id"),
        "name": getattr(library, "name"),
        "emulator_url": getattr(library, "emulator_url"),
        "serial_number": getattr(library, "serial_number"),
        "model": getattr(library, "model"),
        "enabled": getattr(library, "enabled"),
        "status": "online" if getattr(library, "enabled") else "offline",
        "drive_count": 3,
        "tape_count": 12,
    }


@router.get("", response_model=list[LibraryResponse], dependencies=[Depends(require_auth)])
async def list_libraries(repo: CatalogRepository = Depends(get_repository)) -> list[LibraryResponse]:
    return [LibraryResponse.model_validate(_library_response_payload(library)) for library in repo.list_library_instances()]


@router.post("", response_model=LibraryResponse, dependencies=[Depends(require_auth)])
async def create_library(
    data: LibraryCreate,
    repo: CatalogRepository = Depends(get_repository),
) -> LibraryResponse:
    library = repo.create_library_instance(**data.model_dump())
    return LibraryResponse.model_validate(_library_response_payload(library))


@router.get("/{library_id}", response_model=LibraryResponse, dependencies=[Depends(require_auth)])
async def get_library(
    library_id: int,
    repo: CatalogRepository = Depends(get_repository),
) -> LibraryResponse:
    library = repo.get_library_instance(library_id)
    if not library:
        raise HTTPException(status_code=404, detail="Library not found")
    return LibraryResponse.model_validate(_library_response_payload(library))


@router.put("/{library_id}", response_model=LibraryResponse, dependencies=[Depends(require_auth)])
async def update_library(
    library_id: int,
    data: LibraryUpdate,
    repo: CatalogRepository = Depends(get_repository),
) -> LibraryResponse:
    library = repo.update_library_instance(
        library_id,
        **{key: value for key, value in data.model_dump().items() if value is not None},
    )
    if not library:
        raise HTTPException(status_code=404, detail="Library not found")
    return LibraryResponse.model_validate(_library_response_payload(library))


@router.delete("/{library_id}", dependencies=[Depends(require_auth)])
async def delete_library(
    library_id: int,
    repo: CatalogRepository = Depends(get_repository),
) -> dict[str, int]:
    if not repo.delete_library_instance(library_id):
        raise HTTPException(status_code=404, detail="Library not found")
    return {"deleted": library_id}
