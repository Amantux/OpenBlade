"""Catalog-backed LTFS browse endpoints."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel

from openblade.bootstrap import AppContext, get_context
from openblade.catalog.repository import CatalogBrowseEntry

router = APIRouter()

# Simple in-memory mount registry for LTFS mounts (only for simulator/testing)
_active_mounts: dict[str, object] = {}  # barcode -> MountHandle



class LtfsBrowseEntryResponse(BaseModel):
    path: str
    size: int
    tape_barcode: str
    archived_at: datetime | None
    shard_count: int


def _serialize_entry(entry: CatalogBrowseEntry) -> LtfsBrowseEntryResponse:
    return LtfsBrowseEntryResponse(
        path=entry.path,
        size=entry.size,
        tape_barcode=entry.tape_barcode,
        archived_at=entry.archived_at,
        shard_count=entry.shard_count,
    )


@router.get("/browse", response_model=list[LtfsBrowseEntryResponse])
async def browse_ltfs_catalog(
    tape_barcode: str | None = Query(default=None, min_length=1),
    path_prefix: str = Query(default="/", min_length=1),
    context: AppContext = Depends(get_context),
) -> list[LtfsBrowseEntryResponse]:
    entries = context.catalog.list_ltfs_entries(
        tape_barcode=tape_barcode,
        path_prefix=path_prefix,
    )
    return [_serialize_entry(entry) for entry in entries]


@router.get("/tapes", response_model=list[str])
async def list_ltfs_catalog_tapes(
    context: AppContext = Depends(get_context),
) -> list[str]:
    return context.catalog.list_catalog_tape_barcodes()


# LTFS operational endpoints expected by i3 tests
@router.post("/format")
async def ltfs_format(payload: dict, context: AppContext = Depends(get_context)) -> dict:
    barcode = payload.get("barcode")
    if not barcode:
        raise HTTPException(status_code=422, detail="barcode is required")
    confirm = bool(payload.get("confirm", False))
    extras = {}
    if confirm:
        extras["confirmed_format"] = True
        extras["operator_note"] = payload.get("operatorNote")
    from openblade.nas.types import TapeOpRequest, TapeOpType
    from openblade.nas.tape_orchestrator import execute_tape_request

    req = TapeOpRequest(op_type=TapeOpType.FORMAT, barcode=barcode, extras=extras)
    record = execute_tape_request(None, context.library, context.ltfs, req)
    return {"status": record.status, "op_id": record.op_id}


@router.post("/mount")
async def ltfs_mount(payload: dict, context: AppContext = Depends(get_context)) -> dict:
    barcode = payload.get("barcode")
    if not barcode:
        raise HTTPException(status_code=422, detail="barcode is required")
    # Try to ensure the cartridge is loaded to a drive first (best-effort)
    drive_id = payload.get("driveId")
    try:
        from openblade.nas.tape_orchestrator import execute_tape_request
        from openblade.nas.types import TapeOpRequest, TapeOpType
        from openblade.domain.models import MountMode

        # If driveId provided, attempt a load operation so ltfs.mount will find it
        if drive_id is not None:
            load_req = TapeOpRequest(op_type=TapeOpType.LOAD, barcode=barcode, drive_id=int(drive_id))
            execute_tape_request(None, context.library, context.ltfs, load_req)
        # Now call ltfs.mount
        handle = context.ltfs.mount(barcode, MountMode.READ_ONLY)
        _active_mounts[barcode] = handle
        return {"mounted": True, "handle": handle.handle_id}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/unmount")
async def ltfs_unmount(payload: dict, context: AppContext = Depends(get_context)) -> dict:
    barcode = payload.get("barcode")
    if not barcode:
        raise HTTPException(status_code=422, detail="barcode is required")
    handle = _active_mounts.get(barcode)
    if handle is None:
        raise HTTPException(status_code=404, detail="Mount not found")
    try:
        result = context.ltfs.unmount(handle)
        _active_mounts.pop(barcode, None)
        return {"unmounted": True, "result": result.details if hasattr(result, "details") else {}}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/status")
async def ltfs_status(context: AppContext = Depends(get_context)) -> dict:
    # Return LTFS backend status if available
    try:
        return context.ltfs.to_json()
    except Exception:
        return {"status": "unknown"}

