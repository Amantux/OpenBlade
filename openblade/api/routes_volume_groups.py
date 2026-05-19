"""Volume-group API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from openblade.bootstrap import AppContext, get_context

router = APIRouter()


class VolumeGroupCreateRequest(BaseModel):
    name: str


class AssignCartridgeRequest(BaseModel):
    barcode: str


class VolumeGroupResponse(BaseModel):
    id: str
    name: str
    barcodes: list[str]


@router.get("/", response_model=list[VolumeGroupResponse])
async def list_volume_groups(
    context: AppContext = Depends(get_context),
) -> list[VolumeGroupResponse]:
    return [
        VolumeGroupResponse(id=group.id, name=group.name, barcodes=group.barcodes)
        for group in context.catalog.list_volume_groups()
    ]


@router.post("/", response_model=VolumeGroupResponse, status_code=status.HTTP_201_CREATED)
async def create_volume_group(
    request: VolumeGroupCreateRequest,
    context: AppContext = Depends(get_context),
) -> VolumeGroupResponse:
    if context.catalog.get_volume_group(request.name) is not None:
        raise HTTPException(status_code=409, detail=f"Volume group {request.name} already exists")
    group = context.catalog.create_volume_group(request.name)
    return VolumeGroupResponse(id=group.id, name=group.name, barcodes=group.barcodes)


@router.post("/{name}/assign", response_model=VolumeGroupResponse)
async def assign_cartridge(
    name: str,
    request: AssignCartridgeRequest,
    context: AppContext = Depends(get_context),
) -> VolumeGroupResponse:
    group = context.catalog.get_volume_group(name)
    if group is None:
        raise HTTPException(status_code=404, detail=f"Volume group {name} not found")
    context.catalog.add_barcode_to_volume_group(group.id, request.barcode)
    group = context.catalog.get_volume_group(name)
    assert group is not None
    return VolumeGroupResponse(id=group.id, name=group.name, barcodes=group.barcodes)
