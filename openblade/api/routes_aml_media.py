"""AML media and cartridge management routes."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from openblade.api import aml_state
from openblade.api.routes_aml_auth import WSResultCode, _ensure_state, _require_admin, require_auth
from openblade.bootstrap import AppContext, get_context
from openblade.catalog.models import AmlUser

router = APIRouter()

_MEDIA_TYPE_CATALOG: dict[str, dict[str, Any]] = {
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


@router.put("/media/pools/{pool_id}", response_model=PoolResponse)
async def update_media_pool(
    pool_id: str,
    payload: PoolUpdateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
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


@router.post("/media/move", response_model=WSResultCode)
async def move_media(
    payload: MoveRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    barcode = _validate_identifier(payload.move.barcode, field_name="barcode")
    destination = _validate_identifier(payload.move.destination, field_name="destination")
    moved = aml_state.move_aml_media(barcode, destination)
    if moved is None:
        raise HTTPException(status_code=404, detail="Media not found")
    return _ws_result(f"Moved media {barcode} to {destination}")


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
