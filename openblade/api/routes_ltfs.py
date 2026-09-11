"""Catalog-backed LTFS browse endpoints."""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from openblade.bootstrap import AppContext, get_context
from openblade.catalog.repository import CatalogBrowseEntry

logger = logging.getLogger(__name__)

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
    # This route used to hand the orchestrator a bare `confirmed_format: True` and
    # nothing else -- no token, no validation -- so anyone who could reach it could
    # erase a cartridge. Callers already send `safetyToken`; it was simply ignored.
    # Route through FormatService, which is where the persisted one-time token from
    # the format dry-run is actually checked.
    if not bool(payload.get("confirm", False)):
        raise HTTPException(status_code=422, detail="confirm must be true to format a cartridge")
    token = payload.get("safetyToken") or payload.get("safety_token") or payload.get("token")
    if not token:
        raise HTTPException(
            status_code=422,
            detail=(
                "safetyToken is required; obtain one from "
                "POST /cartridges/{barcode}/format/dry-run"
            ),
        )
    from openblade.domain.errors import FormatRequiresConfirmationError

    try:
        result = context.format_service.confirm(str(barcode), str(token))
    except FormatRequiresConfirmationError as exc:
        # Typed domain error -> its curated message, never raw exception text.
        raise HTTPException(status_code=403, detail=str(exc)) from None
    return {"status": "completed" if result.success else "failed", "message": result.message}


@router.post("/mount")
async def ltfs_mount(payload: dict, context: AppContext = Depends(get_context)) -> dict:
    barcode = payload.get("barcode")
    if not barcode:
        raise HTTPException(status_code=422, detail="barcode is required")
    # Try to ensure the cartridge is loaded to a drive first (best-effort)
    drive_id = payload.get("driveId")
    try:
        from openblade.domain.models import MountMode
        from openblade.nas.tape_orchestrator import execute_tape_request
        from openblade.nas.types import TapeOpRequest, TapeOpType

        # If driveId provided, attempt a load operation so ltfs.mount will find it
        if drive_id is not None:
            load_req = TapeOpRequest(op_type=TapeOpType.LOAD, barcode=barcode, drive_id=int(drive_id))
            execute_tape_request(None, context.library, context.ltfs, load_req)
        # Now call ltfs.mount
        handle = context.ltfs.mount(barcode, MountMode.READ_ONLY)
        _active_mounts[barcode] = handle
        return {"mounted": True, "handle": handle.handle_id}
    except Exception as exc:
        # `str(exc)` from a bare `except Exception` on an unauthenticated route
        # leaks argv, device paths and raw LTFS stderr. Curated message out, real
        # cause to the log.
        logger.warning("ltfs mount failed", exc_info=True, extra={"barcode": str(barcode)})
        raise HTTPException(status_code=400, detail="LTFS mount failed") from exc


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
        logger.warning("ltfs unmount failed", exc_info=True, extra={"barcode": str(barcode)})
        raise HTTPException(status_code=400, detail="LTFS unmount failed") from exc


@router.get("/status")
async def ltfs_status(context: AppContext = Depends(get_context)) -> dict:
    # Return LTFS backend status if available
    try:
        return context.ltfs.to_json()
    except Exception:
        return {"status": "unknown"}

