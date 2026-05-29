"""AML media and cartridge management routes."""

from __future__ import annotations

import hashlib
import re
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from openblade.api import aml_state
from openblade.api.routes_aml_auth import WSResultCode, _ensure_state, _require_admin, require_auth
from openblade.api.service_auth import require_service_token
from openblade.bootstrap import AppContext, get_context
from openblade.catalog.models import AmlUser
from openblade.nas.tape_orchestrator import TapeOperationOrchestrator
from openblade.nas.types import TapeOpRequest, TapeOpStatus, TapeOpType

router = APIRouter()
logger = structlog.get_logger(__name__)

_MEDIA_TYPE_CATALOG: dict[str, dict[str, Any]] = {
    "LTO-7": {
        "name": "LTO-7",
        "description": "LTO Ultrium 7 data cartridge",
        "capacity": "6 TB / 15 TB compressed",
        "generations": ["LTO-7"],
    },
    "LTO-7-CLN": {
        "name": "LTO-7-CLN",
        "description": "LTO Ultrium 7 cleaning cartridge",
        "capacity": "N/A",
        "generations": ["LTO-7"],
    },
    "LTO-8": {
        "name": "LTO-8",
        "description": "LTO Ultrium 8 data cartridge",
        "capacity": "12 TB / 30 TB compressed",
        "generations": ["LTO-7", "LTO-8"],
    },
    "LTO-8-CLN": {
        "name": "LTO-8-CLN",
        "description": "LTO Ultrium 8 cleaning cartridge",
        "capacity": "N/A",
        "generations": ["LTO-8"],
    },
    "LTO-9": {
        "name": "LTO-9",
        "description": "LTO Ultrium 9 data cartridge",
        "capacity": "18 TB / 45 TB compressed",
        "generations": ["LTO-8", "LTO-9"],
    },
    "LTO-9-CLN": {
        "name": "LTO-9-CLN",
        "description": "LTO Ultrium 9 cleaning cartridge",
        "capacity": "N/A",
        "generations": ["LTO-9"],
    },
}


class Media(BaseModel):
    model_config = ConfigDict(extra="allow")

    barcode: str
    type: str
    partition: str | None = None
    slotAddress: str
    state: str
    writeProtected: bool
    worm: bool
    generations: int
    loadCount: int
    errorCount: int
    lastLoaded: str | None = None
    capacityGB: int | None = None
    usedGB: int | None = None
    percentUsed: int | None = None
    poolName: str | None = None


class MediaListResource(BaseModel):
    media: list[Media]


class MediaListResponse(BaseModel):
    mediaList: MediaListResource


class MediaResponse(BaseModel):
    media: Media


class MediaPatch(BaseModel):
    model_config = ConfigDict(extra="allow")

    barcode: str | None = None
    type: str | None = None
    partition: str | None = None
    slotAddress: str | None = None
    state: str | None = None
    writeProtected: bool | None = Field(default=None, validation_alias=AliasChoices("writeProtected", "writeProtect"))
    worm: bool | None = None
    generations: int | None = None
    loadCount: int | None = None
    errorCount: int | None = None
    lastLoaded: str | None = None
    description: str | None = None


class MediaUpdateRequest(BaseModel):
    media: MediaPatch


class HistoryEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    timestamp: str
    type: str
    source: str | None = None
    destination: str | None = None
    drive: str | None = None
    result: str


class HistoryListResource(BaseModel):
    event: list[HistoryEvent]


class HistoryListResponse(BaseModel):
    historyList: HistoryListResource


class MediaStats(BaseModel):
    loadCount: int
    errorCount: int
    writeErrors: int
    readErrors: int
    lastLoaded: str | None = None
    totalMounts: int
    totalHours: float


class MediaStatsResponse(BaseModel):
    mediaStats: MediaStats


class MediaType(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    description: str
    capacity: str
    generations: list[str] = Field(default_factory=list)


class TypeListResource(BaseModel):
    type: list[MediaType]


class TypeListResponse(BaseModel):
    typeList: TypeListResource


class MediaPool(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    policy: str
    maxDrives: int
    targetLtoGeneration: str | None = None
    quotaGB: int | None = None
    color: str
    assignedBarcodes: list[str] = Field(default_factory=list)
    createdAt: str
    mediaCount: int = 0
    type: str | None = None


class PoolListResource(BaseModel):
    pool: list[MediaPool]


class PoolListResponse(BaseModel):
    poolList: PoolListResource


class PoolResponse(BaseModel):
    pool: MediaPool


class MediaPoolConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str | None = None
    policy: str | None = None
    maxDrives: int | None = None
    targetLtoGeneration: str | None = None
    quotaGB: int | None = None
    color: str | None = None
    type: str | None = None


class MediaPoolPatch(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str | None = None
    policy: str | None = None
    maxDrives: int | None = None
    targetLtoGeneration: str | None = None
    quotaGB: int | None = None
    color: str | None = None
    type: str | None = None


class PoolCreateRequest(BaseModel):
    pool: MediaPoolConfig | None = None

    name: str | None = None
    policy: str | None = None
    maxDrives: int | None = None
    targetLtoGeneration: str | None = None
    quotaGB: int | None = None
    color: str | None = None


class PoolUpdateRequest(BaseModel):
    pool: MediaPoolPatch | None = None

    name: str | None = None
    policy: str | None = None
    maxDrives: int | None = None
    targetLtoGeneration: str | None = None
    quotaGB: int | None = None
    color: str | None = None


class PoolAssignmentRequest(BaseModel):
    barcodes: list[str] = Field(default_factory=list)


class MediaImportItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    barcode: str
    type: str
    partition: str | None = None


class MediaImportListResource(BaseModel):
    media: list[MediaImportItem]


class MediaImportRequest(BaseModel):
    mediaList: MediaImportListResource


class BarcodeListResource(BaseModel):
    barcode: list[str]


class BarcodeListRequest(BaseModel):
    barcodeList: BarcodeListResource


class MoveResource(BaseModel):
    barcode: str
    destination: str


class MoveRequest(BaseModel):
    move: MoveResource


def _ws_result(summary: str = "Operation completed") -> WSResultCode:
    return WSResultCode(summary=summary)


def _validate_identifier(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail=f"{field_name} is required")
    return normalized


def _validate_media_patch(payload: MediaPatch) -> dict[str, Any]:
    updates = payload.model_dump(exclude_none=True)
    updates.pop("barcode", None)
    for field_name in ("type", "partition", "slotAddress", "state", "description"):
        if field_name in updates:
            updates[field_name] = _validate_identifier(str(updates[field_name]), field_name=field_name)
    return updates


def _validate_pool_payload(payload: MediaPoolConfig | MediaPoolPatch | PoolCreateRequest | PoolUpdateRequest) -> dict[str, Any]:
    if isinstance(payload, (PoolCreateRequest, PoolUpdateRequest)):
        nested = payload.pool
        updates = nested.model_dump(exclude_unset=True) if nested is not None else payload.model_dump(exclude_unset=True, exclude={"pool"})
    else:
        updates = payload.model_dump(exclude_unset=True)

    if "type" in updates and "targetLtoGeneration" not in updates:
        updates["targetLtoGeneration"] = updates.pop("type")
    else:
        updates.pop("type", None)

    for field_name in ("name", "policy", "color"):
        if field_name in updates:
            updates[field_name] = _validate_identifier(str(updates[field_name]), field_name=field_name)
    if "targetLtoGeneration" in updates and updates["targetLtoGeneration"] is not None:
        updates["targetLtoGeneration"] = _validate_identifier(str(updates["targetLtoGeneration"]), field_name="targetLtoGeneration")

    if "maxDrives" in updates:
        try:
            updates["maxDrives"] = max(1, int(updates["maxDrives"]))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="maxDrives must be a positive integer") from exc

    if "quotaGB" in updates:
        quota = updates["quotaGB"]
        if quota is None:
            updates["quotaGB"] = None
        else:
            try:
                updates["quotaGB"] = max(1, int(quota))
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="quotaGB must be a positive integer") from exc

    return updates


def _lto_capacity_gb(tape_type: str) -> int:
    match = re.search(r"LTO-(\d+)", tape_type.upper())
    generation = int(match.group(1)) if match else None
    return {
        6: 2500,
        7: 6000,
        8: 12000,
        9: 18000,
    }.get(generation, 6000)



def _estimate_used_gb(barcode: str, capacity_gb: int) -> int:
    if capacity_gb <= 0:
        return 0
    seed = int.from_bytes(hashlib.sha256(barcode.encode("utf-8")).digest()[:8], "big")
    percent_used = 20 + (seed % 66)
    return round(capacity_gb * percent_used / 100)



def _find_pool_name(barcode: str) -> str | None:
    return aml_state.find_aml_media_pool_name(barcode)



def _enrich_media(media: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(media)
    capacity_gb = _lto_capacity_gb(str(enriched.get("type", "")))
    used_gb = _estimate_used_gb(str(enriched.get("barcode", "")), capacity_gb)
    percent_used = round((used_gb / capacity_gb) * 100) if capacity_gb > 0 else 0
    enriched.update({
        "capacityGB": capacity_gb,
        "usedGB": used_gb,
        "percentUsed": percent_used,
        "poolName": _find_pool_name(str(enriched.get("barcode", ""))),
    })
    return enriched



def _serialize_media(media: dict[str, Any]) -> Media:
    return Media.model_validate(_enrich_media(media))


def _serialize_pool(pool: dict[str, Any]) -> MediaPool:
    return MediaPool.model_validate(pool)


def _serialize_type(media_type: dict[str, Any]) -> MediaType:
    return MediaType.model_validate(media_type)


def _get_media_or_404(barcode: str) -> dict[str, Any]:
    media = aml_state.get_aml_media(barcode)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    return media


def _pool_id_from_name(name: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return normalized or f"pool-{hashlib.sha256(name.encode('utf-8')).hexdigest()[:8]}"



def _get_pool_or_404(pool_id: str) -> dict[str, Any]:
    pool = aml_state.get_aml_media_pool(pool_id)
    if pool is None:
        raise HTTPException(status_code=404, detail="Pool not found")
    return pool


def _parse_drive_address(address: str) -> int | None:
    match = re.fullmatch(r"DRV-(\d+)", address.strip().upper())
    if match is None:
        return None
    drive_number = int(match.group(1))
    if drive_number < 1:
        raise HTTPException(status_code=400, detail="Invalid destination drive")
    return drive_number - 1


def _parse_slot_address(address: str) -> int | None:
    parts = [part.strip() for part in address.split(",")]
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    slot_id = int(parts[-1])
    if slot_id < 1:
        raise HTTPException(status_code=400, detail="Invalid destination slot")
    return slot_id


def _drive_address(drive_id: int) -> str:
    return f"DRV-{drive_id + 1:03d}"


def _slot_address(slot_id: int) -> str:
    return f"1,1,{slot_id}"


def _record_media_move_audit(
    current_user: AmlUser,
    *,
    barcode: str,
    source: str | None,
    destination: str,
    result: str,
) -> None:
    audit_log = aml_state.get_aml_audit_log()
    audit_log.append(
        {
            "timestamp": aml_state._isoformat(aml_state._utcnow()),
            "user": current_user.name,
            "action": "move_media",
            "resource": f"media/{barcode} {source or 'unknown'}->{destination}",
            "result": result,
            "ip": None,
        }
    )
    if len(audit_log) > 1000:
        del audit_log[:-1000]


def _media_history(media: dict[str, Any]) -> list[HistoryEvent]:
    history = media.get("history")
    if isinstance(history, list):
        return [HistoryEvent.model_validate(item) for item in history]
    last_loaded = media.get("lastLoaded")
    if not last_loaded:
        return []
    slot_address = str(media.get("slotAddress", "unknown"))
    return [
        HistoryEvent(
            timestamp=last_loaded,
            type="mount",
            source=slot_address,
            destination="DRV-001",
            drive="DRV-001",
            result="success",
        ),
        HistoryEvent(
            timestamp=last_loaded,
            type="unmount",
            source="DRV-001",
            destination=slot_address,
            drive="DRV-001",
            result="success",
        ),
    ]


def _media_stats(media: dict[str, Any]) -> MediaStats:
    load_count = int(media.get("loadCount", 0))
    error_count = int(media.get("errorCount", 0))
    statistics = media.get("statistics") if isinstance(media.get("statistics"), dict) else {}
    return MediaStats(
        loadCount=load_count,
        errorCount=error_count,
        writeErrors=int(statistics.get("writeErrors", media.get("writeErrors", error_count))),
        readErrors=int(statistics.get("readErrors", media.get("readErrors", 0))),
        lastLoaded=media.get("lastLoaded"),
        totalMounts=int(statistics.get("totalMounts", load_count)),
        totalHours=float(statistics.get("totalHours", round(load_count * 1.5, 2))),
    )


@router.get("/media/types", response_model=TypeListResponse)
async def list_media_types(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> TypeListResponse:
    _ensure_state(context)
    types = dict(_MEDIA_TYPE_CATALOG)
    for media in aml_state.list_aml_media():
        media_type = str(media.get("type", ""))
        if media_type and media_type not in types:
            types[media_type] = {
                "name": media_type,
                "description": f"{media_type} cartridge",
                "capacity": "Unknown",
                "generations": [media_type],
            }
    return TypeListResponse(typeList=TypeListResource(type=[_serialize_type(item) for _, item in sorted(types.items())]))


@router.get("/media/pools", response_model=PoolListResponse)
async def list_media_pools(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PoolListResponse:
    _ensure_state(context)
    return PoolListResponse(poolList=PoolListResource(pool=[_serialize_pool(item) for item in aml_state.list_aml_media_pools()]))


@router.get("/media/pools/{pool_id}", response_model=PoolResponse)
async def get_media_pool(
    pool_id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PoolResponse:
    _ensure_state(context)
    normalized_pool_id = _validate_identifier(pool_id, field_name="Pool id")
    return PoolResponse(pool=_serialize_pool(_get_pool_or_404(normalized_pool_id)))


@router.post("/media/pools", response_model=PoolResponse, status_code=status.HTTP_201_CREATED)
async def create_media_pool(
    payload: PoolCreateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PoolResponse:
    _ensure_state(context)
    _require_admin(current_user)
    updates = _validate_pool_payload(payload)
    pool_name = _validate_identifier(str(updates.get("name", "")), field_name="name")
    pool_id = _pool_id_from_name(pool_name)
    created = aml_state.create_aml_media_pool(pool_id, updates)
    if created is None:
        raise HTTPException(status_code=409, detail="Pool already exists")
    return PoolResponse(pool=_serialize_pool(created))


async def _update_media_pool(
    pool_id: str,
    payload: PoolUpdateRequest,
    current_user: AmlUser,
    context: AppContext,
) -> PoolResponse:
    _ensure_state(context)
    _require_admin(current_user)
    normalized_pool_id = _validate_identifier(pool_id, field_name="Pool id")
    if aml_state.get_aml_media_pool(normalized_pool_id) is None:
        raise HTTPException(status_code=404, detail="Pool not found")
    updated = aml_state.update_aml_media_pool(normalized_pool_id, _validate_pool_payload(payload))
    if updated is None:
        raise HTTPException(status_code=409, detail="Pool already exists")
    return PoolResponse(pool=_serialize_pool(updated))


@router.put("/media/pools/{pool_id}", response_model=PoolResponse)
async def update_media_pool(
    pool_id: str,
    payload: PoolUpdateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PoolResponse:
    return await _update_media_pool(pool_id, payload, current_user, context)


@router.patch("/media/pools/{pool_id}", response_model=PoolResponse)
async def patch_media_pool(
    pool_id: str,
    payload: PoolUpdateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PoolResponse:
    return await _update_media_pool(pool_id, payload, current_user, context)


@router.delete("/media/pools/{pool_id}", response_model=WSResultCode)
async def delete_media_pool(
    pool_id: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    normalized_pool_id = _validate_identifier(pool_id, field_name="Pool id")
    if not aml_state.delete_aml_media_pool(normalized_pool_id):
        raise HTTPException(status_code=404, detail="Pool not found")
    return _ws_result(f"Deleted media pool {normalized_pool_id}")


@router.post("/media/pools/{pool_id}/assign", response_model=PoolResponse)
async def assign_media_to_pool(
    pool_id: str,
    payload: PoolAssignmentRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PoolResponse:
    _ensure_state(context)
    _require_admin(current_user)
    normalized_pool_id = _validate_identifier(pool_id, field_name="Pool id")
    barcodes = [_validate_identifier(item, field_name="barcode") for item in payload.barcodes]
    missing = [barcode for barcode in barcodes if aml_state.get_aml_media(barcode) is None]
    if missing:
        raise HTTPException(status_code=404, detail=f"Media not found: {missing[0]}")
    updated = aml_state.assign_aml_media_to_pool(normalized_pool_id, barcodes)
    if updated is None:
        raise HTTPException(status_code=404, detail="Pool not found")
    return PoolResponse(pool=_serialize_pool(updated))


@router.delete("/media/pools/{pool_id}/assign/{barcode}", response_model=PoolResponse)
async def unassign_media_from_pool(
    pool_id: str,
    barcode: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PoolResponse:
    _ensure_state(context)
    _require_admin(current_user)
    normalized_pool_id = _validate_identifier(pool_id, field_name="Pool id")
    normalized_barcode = _validate_identifier(barcode, field_name="barcode")
    updated = aml_state.unassign_aml_media_from_pool(normalized_pool_id, [normalized_barcode])
    if updated is None:
        raise HTTPException(status_code=404, detail="Pool not found")
    return PoolResponse(pool=_serialize_pool(updated))


@router.get("/media/pool/{name}", response_model=PoolResponse)
async def get_media_pool_legacy(
    name: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PoolResponse:
    return await get_media_pool(name, _, context)


@router.post("/media/pool/{name}", response_model=PoolResponse, status_code=status.HTTP_201_CREATED)
async def create_media_pool_legacy(
    name: str,
    payload: PoolCreateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PoolResponse:
    _ensure_state(context)
    _require_admin(current_user)
    updates = _validate_pool_payload(payload)
    updates.setdefault("name", _validate_identifier(name, field_name="Pool name"))
    created = aml_state.create_aml_media_pool(name, updates)
    if created is None:
        raise HTTPException(status_code=409, detail="Pool already exists")
    return PoolResponse(pool=_serialize_pool(created))


@router.put("/media/pool/{name}", response_model=PoolResponse)
async def update_media_pool_legacy(
    name: str,
    payload: PoolUpdateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PoolResponse:
    return await update_media_pool(name, payload, current_user, context)


@router.delete("/media/pool/{name}", response_model=WSResultCode)
async def delete_media_pool_legacy(
    name: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    return await delete_media_pool(name, current_user, context)


@router.post("/media/pool/{name}/assign", response_model=PoolResponse)
async def assign_media_to_pool_legacy(
    name: str,
    payload: BarcodeListRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PoolResponse:
    return await assign_media_to_pool(
        name,
        PoolAssignmentRequest(barcodes=payload.barcodeList.barcode),
        current_user,
        context,
    )


@router.post("/media/pool/{name}/unassign", response_model=PoolResponse)
async def unassign_media_from_pool_legacy(
    name: str,
    payload: BarcodeListRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PoolResponse:
    updated: PoolResponse | None = None
    for barcode in payload.barcodeList.barcode:
        updated = await unassign_media_from_pool(name, barcode, current_user, context)
    if updated is None:
        return PoolResponse(pool=_serialize_pool(_get_pool_or_404(name)))
    return updated


@router.post("/media/import", response_model=WSResultCode)
async def import_media(
    payload: MediaImportRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    aml_state.import_aml_media([
        {
            "barcode": _validate_identifier(item.barcode, field_name="barcode"),
            "type": _validate_identifier(item.type, field_name="type"),
            "partition": item.partition,
        }
        for item in payload.mediaList.media
    ])
    return _ws_result("Imported media")


@router.post("/media/export", response_model=WSResultCode)
async def export_media(
    payload: BarcodeListRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    barcodes = [_validate_identifier(item, field_name="barcode") for item in payload.barcodeList.barcode]
    missing = [barcode for barcode in barcodes if aml_state.get_aml_media(barcode) is None]
    if missing:
        raise HTTPException(status_code=404, detail=f"Media not found: {missing[0]}")
    aml_state.export_aml_media(barcodes)
    return _ws_result("Exported media")


@router.post("/media/move", response_model=WSResultCode, dependencies=[Depends(require_service_token)])
async def move_media(
    payload: MoveRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    barcode = _validate_identifier(payload.move.barcode, field_name="barcode")
    destination = _validate_identifier(payload.move.destination, field_name="destination")
    media = aml_state.get_aml_media(barcode)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")

    orchestrator = TapeOperationOrchestrator(context.catalog, context.library, context.ltfs)
    source = _validate_identifier(str(media.get("slotAddress") or ""), field_name="source")
    source_drive = _parse_drive_address(source)
    source_slot = None if source_drive is not None else _parse_slot_address(source)
    if source_drive is None and source_slot is None:
        raise HTTPException(status_code=400, detail="Source media must be in a slot or drive")

    try:
        drive_id = _parse_drive_address(destination)
        if drive_id is not None:
            if source_slot is None:
                raise HTTPException(status_code=400, detail="Source media must be in a slot before loading")
            request = TapeOpRequest(
                op_type=TapeOpType.LOAD,
                barcode=barcode,
                drive_id=drive_id,
                slot_id=source_slot,
                requested_by=current_user.name,
                extras={"source_slot_id": source_slot},
            )
        else:
            destination_slot = _parse_slot_address(destination)
            if destination_slot is None:
                raise HTTPException(
                    status_code=400,
                    detail="Destination must be a slot address like 1,1,11 or a drive like DRV-001",
                )
            if source_drive is not None:
                request = TapeOpRequest(
                    op_type=TapeOpType.UNLOAD,
                    barcode=barcode,
                    drive_id=source_drive,
                    slot_id=destination_slot,
                    requested_by=current_user.name,
                )
            else:
                request = TapeOpRequest(
                    op_type=TapeOpType.MOVE,
                    barcode=barcode,
                    slot_id=destination_slot,
                    requested_by=current_user.name,
                    extras={"source_slot_id": source_slot},
                )

        record = orchestrator.execute(request)
    except HTTPException as exc:
        _record_media_move_audit(
            current_user,
            barcode=barcode,
            source=source,
            destination=destination,
            result=f"failed: {exc.detail}",
        )
        raise
    except ValueError as exc:
        _record_media_move_audit(
            current_user,
            barcode=barcode,
            source=source,
            destination=destination,
            result=f"failed: {exc}",
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if record.status is TapeOpStatus.FAILED:
        result = record.error or "Tape move operation failed"
        _record_media_move_audit(
            current_user,
            barcode=barcode,
            source=source,
            destination=destination,
            result=f"failed: {result}",
        )
        logger.warning(
            "controller media move failed",
            barcode=barcode,
            source=source,
            destination=destination,
            requested_by=current_user.name,
            error=result,
        )
        raise HTTPException(status_code=409, detail=result)

    aml_state.move_aml_media(barcode, destination)
    _record_media_move_audit(
        current_user,
        barcode=barcode,
        source=source,
        destination=destination,
        result="success",
    )
    logger.info(
        "controller media move executed",
        barcode=barcode,
        source=source,
        destination=destination,
        requested_by=current_user.name,
    )
    return _ws_result(f"Moved media {barcode} to {destination}")


@router.get("/media/operations/moveMedium/maxAllowed", response_model=dict[str, int])
async def get_max_allowed_medium_moves(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, int]:
    _ensure_state(context)
    return {"maxAllowed": 4}


@router.get("/media/reports/usage", response_model=dict[str, Any])
async def get_media_usage_report(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    _ensure_state(context)
    items = []
    for media in aml_state.list_aml_media():
        enriched = _enrich_media(media)
        items.append(
            {
                "barcode": str(enriched.get("barcode")),
                "type": str(enriched.get("type")),
                "usedGB": int(enriched.get("usedGB", 0) or 0),
                "capacityGB": int(enriched.get("capacityGB", 0) or 0),
                "percentUsed": int(enriched.get("percentUsed", 0) or 0),
                "state": str(enriched.get("state", "unknown")),
            }
        )
    return {"generatedAt": "2024-01-15T10:10:00Z", "media": items}


@router.get("/media/search", response_model=MediaListResponse)
async def search_media(
    partition: str | None = Query(default=None),
    type: str | None = Query(default=None),
    state: str | None = Query(default=None),
    barcode: str | None = Query(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> MediaListResponse:
    _ensure_state(context)
    items = aml_state.search_aml_media(partition=partition, media_type=type, state=state, barcode=barcode)
    return MediaListResponse(mediaList=MediaListResource(media=[_serialize_media(item) for item in items]))


@router.get("/media/scratch", response_model=MediaListResponse)
async def list_scratch_media(
    partition: str | None = Query(default=None),
    type: str | None = Query(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> MediaListResponse:
    _ensure_state(context)
    items = aml_state.list_aml_scratch_media(partition=partition, media_type=type)
    return MediaListResponse(mediaList=MediaListResource(media=[_serialize_media(item) for item in items]))


@router.get("/media", response_model=MediaListResponse)
async def list_media(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> MediaListResponse:
    _ensure_state(context)
    return MediaListResponse(mediaList=MediaListResource(media=[_serialize_media(item) for item in aml_state.list_aml_media()]))


@router.get("/media/{barcode}/history", response_model=HistoryListResponse)
async def get_media_history(
    barcode: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> HistoryListResponse:
    _ensure_state(context)
    media_barcode = _validate_identifier(barcode, field_name="barcode")
    media = _get_media_or_404(media_barcode)
    return HistoryListResponse(historyList=HistoryListResource(event=_media_history(media)))


@router.get("/media/{barcode}/statistics", response_model=MediaStatsResponse)
async def get_media_statistics(
    barcode: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> MediaStatsResponse:
    _ensure_state(context)
    media_barcode = _validate_identifier(barcode, field_name="barcode")
    return MediaStatsResponse(mediaStats=_media_stats(_get_media_or_404(media_barcode)))


@router.get("/media/{barcode}", response_model=MediaResponse)
async def get_media(
    barcode: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> MediaResponse:
    _ensure_state(context)
    media_barcode = _validate_identifier(barcode, field_name="barcode")
    return MediaResponse(media=_serialize_media(_get_media_or_404(media_barcode)))


@router.put("/media/{barcode}", response_model=MediaResponse)
async def update_media(
    barcode: str,
    payload: MediaUpdateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> MediaResponse:
    _ensure_state(context)
    _require_admin(current_user)
    media_barcode = _validate_identifier(barcode, field_name="barcode")
    updated = aml_state.update_aml_media(media_barcode, _validate_media_patch(payload.media))
    if updated is None:
        raise HTTPException(status_code=404, detail="Media not found")
    return MediaResponse(media=_serialize_media(updated))


@router.delete("/media/{barcode}", response_model=WSResultCode)
async def delete_media(
    barcode: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    media_barcode = _validate_identifier(barcode, field_name="barcode")
    if not aml_state.delete_aml_media(media_barcode):
        raise HTTPException(status_code=404, detail="Media not found")
    return _ws_result(f"Deleted media {media_barcode}")
