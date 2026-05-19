"""Inventory API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from openblade.bootstrap import AppContext, get_context

router = APIRouter()


class SlotResponse(BaseModel):
    slot_id: int
    occupied: bool
    barcode: str | None


class DriveResponse(BaseModel):
    drive_id: int
    loaded: bool
    barcode: str | None
    drive_state: str
    mount_state: str


class InventoryResponse(BaseModel):
    library_id: str
    slots: list[SlotResponse]
    drives: list[DriveResponse]
    changer_state: str


@router.get("/", response_model=InventoryResponse)
async def get_inventory(context: AppContext = Depends(get_context)) -> InventoryResponse:
    """Get current library inventory."""
    inventory = context.library.inventory()
    return InventoryResponse(
        library_id=inventory.library_id,
        changer_state=inventory.changer_state.value,
        slots=[
            SlotResponse(
                slot_id=slot.slot_id,
                occupied=slot.occupied,
                barcode=str(slot.barcode) if slot.barcode else None,
            )
            for slot in inventory.slots
        ],
        drives=[
            DriveResponse(
                drive_id=drive.drive_id,
                loaded=drive.barcode is not None,
                barcode=str(drive.barcode) if drive.barcode else None,
                drive_state=drive.drive_state.value,
                mount_state=drive.mount_state.value,
            )
            for drive in inventory.drives
        ],
    )
