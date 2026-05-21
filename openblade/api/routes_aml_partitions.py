"""AML partition management routes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from openblade.api import aml_state
from openblade.api.routes_aml_auth import WSResultCode, _ensure_state, _require_admin, require_auth
from openblade.bootstrap import AppContext, get_context
from openblade.catalog.models import AmlUser

router = APIRouter()


class Partition(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    id: str
    status: str
    type: str
    driveCount: int
    slotCount: int
    ieSlotCount: int
    cleaningSlots: int
    mediaCount: int


class PartitionListResource(BaseModel):
    partition: list[Partition]


class PartitionListResponse(BaseModel):
    partitionList: PartitionListResource


class PartitionEnvelope(BaseModel):
    partition: Partition


class PartitionConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    driveCount: int
    slotCount: int
    ieSlotCount: int | None = None
    cleaningSlots: int | None = None
    status: str | None = None


class PartitionConfigPatch(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str | None = None
    driveCount: int | None = None
    slotCount: int | None = None
    ieSlotCount: int | None = None
    cleaningSlots: int | None = None
    status: str | None = None


class PartitionCreateRequest(BaseModel):
    partition: PartitionConfig


class PartitionUpdateRequest(BaseModel):
    partition: PartitionConfigPatch


class DriveResource(BaseModel):
    serialNumber: str


class DriveListResource(BaseModel):
    drive: list[DriveResource]


class DriveListResponse(BaseModel):
    driveList: DriveListResource


class DriveRequest(BaseModel):
    drive: DriveResource


class MediaResource(BaseModel):
    barcode: str
    slotAddress: str
    state: str
    type: str


class MediaListResource(BaseModel):
    media: list[MediaResource]


class MediaListResponse(BaseModel):
    mediaList: MediaListResource


class SlotResource(BaseModel):
    id: str
    address: str
    state: str
    barcode: str | None = None
    type: str


class SlotListResource(BaseModel):
    slot: list[SlotResource]


class SlotListResponse(BaseModel):
    slotList: SlotListResource


class SlotResponse(BaseModel):
    slot: SlotResource


class PolicyResource(BaseModel):
    model_config = ConfigDict(extra="allow")

    autoClean: bool
    cleaningThreshold: int
    mediaAutoAssign: bool
    mountTimeout: int
    unmountTimeout: int
    ejectTimeout: int
    roboticsTimeout: int


class PolicyResponse(BaseModel):
    policy: PolicyResource


class AccessConfigResource(BaseModel):
    model_config = ConfigDict(extra="allow")

    mode: str
    groups: list[str] = Field(default_factory=list)
    hosts: list[str] = Field(default_factory=list)


class AccessConfigResponse(BaseModel):
    accessConfig: AccessConfigResource


class AccessGroupReference(BaseModel):
    groupName: str


class HostReference(BaseModel):
    WWPN: str


class CleaningConfigResource(BaseModel):
    model_config = ConfigDict(extra="allow")

    autoClean: bool
    threshold: int
    cleaningTapeBarcode: str | None = None
    lastCleaned: str | None = None


class CleaningConfigResponse(BaseModel):
    cleaningConfig: CleaningConfigResource


class MediaUsageResource(BaseModel):
    barcode: str
    mounts: int = 0


class StatisticsResource(BaseModel):
    mountCount: int
    unmountCount: int
    errorCount: int
    lastMount: str | None = None
    lastUnmount: str | None = None
    mediaUsage: list[MediaUsageResource] = Field(default_factory=list)


class StatisticsResponse(BaseModel):
    statistics: StatisticsResource


class PartitionStatusResource(BaseModel):
    overall: str
    drives: str
    media: str
    robotics: str
    connectivity: str


class PartitionStatusResponse(BaseModel):
    partitionStatus: PartitionStatusResource


class WormResource(BaseModel):
    enabled: bool
    mode: str


class WormResponse(BaseModel):
    worm: WormResource


class EncryptionResource(BaseModel):
    enabled: bool
    type: str
    keyManager: str | None = None


class EncryptionResponse(BaseModel):
    encryption: EncryptionResource


class QoSResource(BaseModel):
    maxMountsPerHour: int
    priority: str
    preemption: bool


class QoSResponse(BaseModel):
    qos: QoSResource


class GlobalConfigResource(BaseModel):
    defaultMountTimeout: int
    defaultCleaningThreshold: int
    maxPartitions: int
    currentPartitions: int


class GlobalConfigResponse(BaseModel):
    globalConfig: GlobalConfigResource


class LMEResource(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool
    exportPath: str | None = None


class LMEResponse(BaseModel):
    lme: LMEResource


class ReportResponse(BaseModel):
    report: dict[str, Any]


class ReportListResponse(BaseModel):
    report: list[dict[str, Any]]


class MoveJobResource(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    source: str
    destination: str
    media: str
    status: str
    priority: str


class MoveQueueListResource(BaseModel):
    moveJob: list[MoveJobResource]


class MoveQueueListResponse(BaseModel):
    moveQueueList: MoveQueueListResource


class AlertResource(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    severity: str
    message: str
    timestamp: str


class AlertListResource(BaseModel):
    alert: list[AlertResource]


class AlertListResponse(BaseModel):
    alertList: AlertListResource


class QuotaResource(BaseModel):
    totalSlots: int
    usedSlots: int
    totalDrives: int
    usedDrives: int


class QuotaResponse(BaseModel):
    quota: QuotaResource


class QuotaUpdateRequest(BaseModel):
    quota: QuotaResource


def _ws_result(summary: str) -> WSResultCode:
    return WSResultCode(summary=summary)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="Partition name is required")
    return normalized


def _validate_identifier(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail=f"{field_name} is required")
    return normalized


def _validate_non_negative(value: int | None, *, field_name: str) -> int | None:
    if value is None:
        return None
    if value < 0:
        raise HTTPException(status_code=400, detail=f"{field_name} must be non-negative")
    return value


def _validate_partition_config(payload: PartitionConfig | PartitionConfigPatch) -> dict[str, Any]:
    data = payload.model_dump(exclude_none=True)
    if "type" in data:
        data["type"] = _validate_identifier(str(data["type"]), field_name="Partition type")
    for field_name in ("driveCount", "slotCount", "ieSlotCount", "cleaningSlots"):
        if field_name in data:
            data[field_name] = _validate_non_negative(int(data[field_name]), field_name=field_name)
    if "status" in data:
        data["status"] = _validate_identifier(str(data["status"]), field_name="Partition status")
    return data


def _validate_drive(serial_number: str, context: AppContext) -> str:
    normalized = _validate_identifier(serial_number, field_name="Drive serial number")
    available = {f"DRV-{drive.drive_id:03d}" for drive in context.library.inventory().drives}
    if normalized not in available:
        raise HTTPException(status_code=404, detail="Drive not found")
    return normalized


def _get_partition_or_404(name: str) -> dict[str, Any]:
    partition = aml_state.get_aml_partition(name)
    if partition is None:
        raise HTTPException(status_code=404, detail="Partition not found")
    return partition


def _unique_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        normalized = _validate_identifier(value, field_name="Value")
        if normalized not in result:
            result.append(normalized)
    return result



def _policy_defaults(name: str) -> dict[str, Any]:
    defaults = {
        "activeVault": {"enabled": False, "retentionDays": 0, "mode": "manual"},
        "autoImport": {"enabled": True, "source": "ieStation", "scanInterval": 300},
        "autoExport": {"enabled": False, "destination": "mailSlot", "schedule": "manual"},
        "driveCleaning": {"enabled": True, "threshold": 100, "useScratchCleaning": True},
        "edlm": {"enabled": True, "mode": "passive", "verifyAfterMove": False},
        "ekm": {"enabled": False, "server": None, "keyGroup": None},
    }
    return dict(defaults[name])



def _global_policy(name: str) -> dict[str, Any]:
    global_config = aml_state.get_aml_partitions_global()
    policy = global_config.get(name)
    if isinstance(policy, dict):
        return policy
    policy = _policy_defaults(name)
    aml_state.set_aml_partitions_global({name: policy})
    return policy


def _partition_slots(partition: dict[str, Any], context: AppContext) -> list[SlotResource]:
    if partition["name"] != "partition1":
        return []
    occupied = {str(item["slotId"]): item["barcode"] for item in context.library.list_tapes()}
    slot_count = int(partition.get("slotCount", 0))
    cleaning_start = max(slot_count - int(partition.get("cleaningSlots", 0)) + 1, 1)
    slots: list[SlotResource] = []
    for address in range(1, slot_count + 1):
        address_str = str(address)
        barcode = occupied.get(address_str)
        slot_type = "cleaning" if address >= cleaning_start and int(partition.get("cleaningSlots", 0)) > 0 else "storage"
        slots.append(
            SlotResource(
                id=f"{partition['name']}-slot-{address_str}",
                address=address_str,
                state="occupied" if barcode else "empty",
                barcode=barcode,
                type=slot_type,
            )
        )
    return slots


def _partition_ie_slots(partition: dict[str, Any]) -> list[SlotResource]:
    count = int(partition.get("ieSlotCount", 0))
    return [
        SlotResource(
            id=f"{partition['name']}-ie-{index}",
            address=f"IE-{index}",
            state="empty",
            barcode=None,
            type="ie",
        )
        for index in range(1, count + 1)
    ]


def _partition_media(partition: dict[str, Any], context: AppContext) -> list[MediaResource]:
    return [
        MediaResource(barcode=slot.barcode or "", slotAddress=slot.address, state=slot.state, type=slot.type)
        for slot in _partition_slots(partition, context)
        if slot.barcode is not None
    ]


def _partition_quota_dict(partition: dict[str, Any], context: AppContext) -> dict[str, int]:
    default_quota = {
        "totalSlots": int(partition.get("slotCount", 0)),
        "usedSlots": len(_partition_media(partition, context)),
        "totalDrives": int(partition.get("driveCount", 0)),
        "usedDrives": len(partition.get("drives", [])),
    }
    quota = dict(default_quota)
    quota.update({key: int(value) for key, value in partition.get("quota", {}).items() if value is not None})
    return quota


def _partition_statistics(partition: dict[str, Any], context: AppContext) -> StatisticsResource:
    stats = partition.get("statistics") or {}
    media_usage = stats.get("mediaUsage")
    if media_usage is None:
        media_usage = [{"barcode": media.barcode, "mounts": 0} for media in _partition_media(partition, context)]
    return StatisticsResource(
        mountCount=int(stats.get("mountCount", 0)),
        unmountCount=int(stats.get("unmountCount", 0)),
        errorCount=int(stats.get("errorCount", 0)),
        lastMount=stats.get("lastMount"),
        lastUnmount=stats.get("lastUnmount"),
        mediaUsage=[MediaUsageResource.model_validate(item) for item in media_usage],
    )


def _health(value: str, *, online_ok: bool = False) -> str:
    normalized = value.lower()
    if normalized in {"failed", "error", "critical"}:
        return "failed"
    if normalized in {"offline", "warning", "degraded"}:
        return "warning" if online_ok else normalized
    if normalized in {"online", "good", "ok", "active"}:
        return "good"
    return "warning"


def _partition_status(partition: dict[str, Any], context: AppContext) -> PartitionStatusResource:
    media = _partition_media(partition, context)
    overall = _health(str(partition.get("status", "online")), online_ok=True)
    drives = "good" if partition.get("drives") else "warning"
    media_status = "good" if media else "warning"
    robotics = "warning" if str(partition.get("status", "online")).lower() == "offline" else "good"
    connectivity = "warning" if partition.get("access", {}).get("mode") == "disabled" else "good"
    if overall not in {"good", "warning", "failed"}:
        overall = "good"
    return PartitionStatusResource(
        overall=overall,
        drives=drives,
        media=media_status,
        robotics=robotics,
        connectivity=connectivity,
    )


def _serialize_partition(partition: dict[str, Any], context: AppContext) -> Partition:
    return Partition(
        name=str(partition["name"]),
        id=str(partition["id"]),
        status=str(partition.get("status", "online")),
        type=str(partition.get("type", "data")),
        driveCount=int(partition.get("driveCount", 0)),
        slotCount=int(partition.get("slotCount", 0)),
        ieSlotCount=int(partition.get("ieSlotCount", 0)),
        cleaningSlots=int(partition.get("cleaningSlots", 0)),
        mediaCount=len(_partition_media(partition, context)),
    )


def _partition_report(partition: dict[str, Any], context: AppContext) -> dict[str, Any]:
    return {
        "partition": _serialize_partition(partition, context).model_dump(),
        "drives": [{"serialNumber": serial_number} for serial_number in partition.get("drives", [])],
        "media": [item.model_dump() for item in _partition_media(partition, context)],
        "slots": [item.model_dump() for item in _partition_slots(partition, context)],
        "ieSlots": [item.model_dump() for item in _partition_ie_slots(partition)],
        "policy": PolicyResource.model_validate(partition.get("policy", {})).model_dump(),
        "access": AccessConfigResource.model_validate(partition.get("access", {})).model_dump(),
        "cleaning": CleaningConfigResource.model_validate(partition.get("cleaning", {})).model_dump(),
        "statistics": _partition_statistics(partition, context).model_dump(),
        "status": _partition_status(partition, context).model_dump(),
        "worm": WormResource.model_validate(partition.get("worm", {})).model_dump(),
        "encryption": EncryptionResource.model_validate(partition.get("encryption", {})).model_dump(),
        "qos": QoSResource.model_validate(partition.get("qos", {})).model_dump(),
        "lme": LMEResource.model_validate(partition.get("lme", {})).model_dump(),
        "quota": _partition_quota_dict(partition, context),
        "alerts": partition.get("alerts", []),
        "moveQueue": partition.get("moveQueue", []),
        "generatedAt": _timestamp(),
    }


@router.get("/partitions/policy/activeVault", response_model=dict[str, Any])
async def get_active_vault_policy(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    _ensure_state(context)
    return _global_policy("activeVault")


@router.put("/partitions/policy/activeVault", response_model=dict[str, Any])
async def put_active_vault_policy(
    payload: dict[str, Any],
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    _ensure_state(context)
    _require_admin(current_user)
    aml_state.set_aml_partitions_global({"activeVault": {**_global_policy("activeVault"), **payload}})
    return _global_policy("activeVault")


@router.get("/partitions/policy/autoImport", response_model=dict[str, Any])
async def get_auto_import_policy(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    _ensure_state(context)
    return _global_policy("autoImport")


@router.put("/partitions/policy/autoImport", response_model=dict[str, Any])
async def put_auto_import_policy(
    payload: dict[str, Any],
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    _ensure_state(context)
    _require_admin(current_user)
    aml_state.set_aml_partitions_global({"autoImport": {**_global_policy("autoImport"), **payload}})
    return _global_policy("autoImport")


@router.get("/partitions/policy/autoExport", response_model=dict[str, Any])
async def get_auto_export_policy(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    _ensure_state(context)
    return _global_policy("autoExport")


@router.put("/partitions/policy/autoExport", response_model=dict[str, Any])
async def put_auto_export_policy(
    payload: dict[str, Any],
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    _ensure_state(context)
    _require_admin(current_user)
    aml_state.set_aml_partitions_global({"autoExport": {**_global_policy("autoExport"), **payload}})
    return _global_policy("autoExport")


@router.get("/partitions/policy/driveCleaning", response_model=dict[str, Any])
async def get_drive_cleaning_policy(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    _ensure_state(context)
    return _global_policy("driveCleaning")


@router.put("/partitions/policy/driveCleaning", response_model=dict[str, Any])
async def put_drive_cleaning_policy(
    payload: dict[str, Any],
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    _ensure_state(context)
    _require_admin(current_user)
    aml_state.set_aml_partitions_global({"driveCleaning": {**_global_policy("driveCleaning"), **payload}})
    return _global_policy("driveCleaning")


@router.get("/partitions/policy/edlm", response_model=dict[str, Any])
async def get_edlm_policy(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    _ensure_state(context)
    return _global_policy("edlm")


@router.put("/partitions/policy/edlm", response_model=dict[str, Any])
async def put_edlm_policy(
    payload: dict[str, Any],
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    _ensure_state(context)
    _require_admin(current_user)
    aml_state.set_aml_partitions_global({"edlm": {**_global_policy("edlm"), **payload}})
    return _global_policy("edlm")


@router.get("/partitions/policy/ekm", response_model=dict[str, Any])
async def get_ekm_policy(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    _ensure_state(context)
    return _global_policy("ekm")


@router.put("/partitions/policy/ekm", response_model=dict[str, Any])
async def put_ekm_policy(
    payload: dict[str, Any],
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    _ensure_state(context)
    _require_admin(current_user)
    aml_state.set_aml_partitions_global({"ekm": {**_global_policy("ekm"), **payload}})
    return _global_policy("ekm")


@router.get("/partitions/global", response_model=GlobalConfigResponse)
async def get_global_partition_config(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> GlobalConfigResponse:
    _ensure_state(context)
    return GlobalConfigResponse(globalConfig=GlobalConfigResource.model_validate(aml_state.get_aml_partitions_global()))


@router.put("/partitions/global", response_model=GlobalConfigResponse)
async def put_global_partition_config(
    payload: GlobalConfigResponse,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> GlobalConfigResponse:
    _ensure_state(context)
    _require_admin(current_user)
    updated = aml_state.set_aml_partitions_global(payload.globalConfig.model_dump())
    return GlobalConfigResponse(globalConfig=GlobalConfigResource.model_validate(updated))


@router.get("/partitions/report", response_model=ReportListResponse)
async def get_all_partition_reports(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> ReportListResponse:
    _ensure_state(context)
    partitions = [_partition_report(item, context) for item in aml_state.list_aml_partitions()]
    return ReportListResponse(report=partitions)


@router.get("/partitions/reports/utilization", response_model=dict[str, Any])
async def get_partition_utilization_report(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    _ensure_state(context)
    items = []
    for partition in aml_state.list_aml_partitions():
        quota = _partition_quota_dict(partition, context)
        total_slots = max(int(quota.get("totalSlots", 0)), 1)
        items.append(
            {
                "name": str(partition.get("name")),
                "usedSlots": int(quota.get("usedSlots", 0)),
                "totalSlots": int(quota.get("totalSlots", 0)),
                "utilizationPercent": round((int(quota.get("usedSlots", 0)) / total_slots) * 100, 2),
                "usedDrives": int(quota.get("usedDrives", 0)),
                "totalDrives": int(quota.get("totalDrives", 0)),
            }
        )
    return {"generatedAt": _timestamp(), "partitions": items}


@router.get("/partitions", response_model=PartitionListResponse)
async def list_partitions(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PartitionListResponse:
    _ensure_state(context)
    return PartitionListResponse(
        partitionList=PartitionListResource(partition=[_serialize_partition(item, context) for item in aml_state.list_aml_partitions()])
    )


@router.get("/partition/{name}", response_model=PartitionEnvelope)
async def get_partition(
    name: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PartitionEnvelope:
    _ensure_state(context)
    partition_name = _validate_name(name)
    return PartitionEnvelope(partition=_serialize_partition(_get_partition_or_404(partition_name), context))


@router.post("/partition/{name}", response_model=PartitionEnvelope, status_code=status.HTTP_201_CREATED)
async def create_partition(
    name: str,
    payload: PartitionCreateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PartitionEnvelope:
    _ensure_state(context)
    _require_admin(current_user)
    partition_name = _validate_name(name)
    created = aml_state.create_aml_partition(partition_name, _validate_partition_config(payload.partition))
    if created is None:
        raise HTTPException(status_code=409, detail="Partition already exists")
    return PartitionEnvelope(partition=_serialize_partition(created, context))


@router.put("/partition/{name}", response_model=PartitionEnvelope)
async def update_partition(
    name: str,
    payload: PartitionUpdateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PartitionEnvelope:
    _ensure_state(context)
    _require_admin(current_user)
    partition_name = _validate_name(name)
    updated = aml_state.update_aml_partition(partition_name, _validate_partition_config(payload.partition))
    if updated is None:
        raise HTTPException(status_code=404, detail="Partition not found")
    return PartitionEnvelope(partition=_serialize_partition(updated, context))


@router.delete("/partition/{name}", response_model=WSResultCode)
async def delete_partition(
    name: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    partition_name = _validate_name(name)
    if not aml_state.delete_aml_partition(partition_name):
        raise HTTPException(status_code=404, detail="Partition not found")
    return _ws_result(f"Deleted partition {partition_name}")


@router.get("/partition/{name}/drives", response_model=DriveListResponse)
async def list_partition_drives(
    name: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> DriveListResponse:
    _ensure_state(context)
    partition = _get_partition_or_404(_validate_name(name))
    return DriveListResponse(driveList=DriveListResource(drive=[DriveResource(serialNumber=item) for item in partition.get("drives", [])]))


@router.post("/partition/{name}/drive", response_model=WSResultCode)
async def add_partition_drive(
    name: str,
    payload: DriveRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    partition_name = _validate_name(name)
    _get_partition_or_404(partition_name)
    serial_number = _validate_drive(payload.drive.serialNumber, context)
    aml_state.add_aml_partition_list_item(partition_name, "drives", serial_number)
    return _ws_result(f"Added drive {serial_number} to partition {partition_name}")


@router.delete("/partition/{name}/drive/{serialNumber}", response_model=WSResultCode)
async def remove_partition_drive(
    name: str,
    serialNumber: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    partition_name = _validate_name(name)
    _get_partition_or_404(partition_name)
    serial_number = _validate_identifier(serialNumber, field_name="Drive serial number")
    if not aml_state.remove_aml_partition_list_item(partition_name, "drives", serial_number):
        raise HTTPException(status_code=404, detail="Drive not assigned to partition")
    return _ws_result(f"Removed drive {serial_number} from partition {partition_name}")


@router.get("/partition/{name}/media", response_model=MediaListResponse)
async def list_partition_media(
    name: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> MediaListResponse:
    _ensure_state(context)
    partition = _get_partition_or_404(_validate_name(name))
    return MediaListResponse(mediaList=MediaListResource(media=_partition_media(partition, context)))


@router.get("/partition/{name}/slots", response_model=SlotListResponse)
async def list_partition_slots(
    name: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> SlotListResponse:
    _ensure_state(context)
    partition = _get_partition_or_404(_validate_name(name))
    return SlotListResponse(slotList=SlotListResource(slot=_partition_slots(partition, context)))


@router.get("/partition/{name}/slot/{address}", response_model=SlotResponse)
async def get_partition_slot(
    name: str,
    address: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> SlotResponse:
    _ensure_state(context)
    partition = _get_partition_or_404(_validate_name(name))
    normalized_address = _validate_identifier(address, field_name="Slot address")
    for slot in _partition_slots(partition, context):
        if slot.address == normalized_address:
            return SlotResponse(slot=slot)
    raise HTTPException(status_code=404, detail="Slot not found")


@router.get("/partition/{name}/ieSlots", response_model=SlotListResponse)
async def list_partition_ie_slots(
    name: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> SlotListResponse:
    _ensure_state(context)
    partition = _get_partition_or_404(_validate_name(name))
    return SlotListResponse(slotList=SlotListResource(slot=_partition_ie_slots(partition)))


@router.get("/partition/{name}/policy", response_model=PolicyResponse)
async def get_partition_policy(
    name: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PolicyResponse:
    _ensure_state(context)
    partition = _get_partition_or_404(_validate_name(name))
    return PolicyResponse(policy=PolicyResource.model_validate(partition.get("policy", {})))


@router.put("/partition/{name}/policy", response_model=PolicyResponse)
async def put_partition_policy(
    name: str,
    payload: PolicyResponse,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PolicyResponse:
    _ensure_state(context)
    _require_admin(current_user)
    updated = aml_state.set_aml_partition_section(_validate_name(name), "policy", payload.policy.model_dump())
    if updated is None:
        raise HTTPException(status_code=404, detail="Partition not found")
    return PolicyResponse(policy=PolicyResource.model_validate(updated))


@router.get("/partition/{name}/access", response_model=AccessConfigResponse)
async def get_partition_access(
    name: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> AccessConfigResponse:
    _ensure_state(context)
    partition = _get_partition_or_404(_validate_name(name))
    return AccessConfigResponse(accessConfig=AccessConfigResource.model_validate(partition.get("access", {})))


@router.put("/partition/{name}/access", response_model=AccessConfigResponse)
async def put_partition_access(
    name: str,
    payload: AccessConfigResponse,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> AccessConfigResponse:
    _ensure_state(context)
    _require_admin(current_user)
    access = payload.accessConfig.model_dump()
    access["groups"] = _unique_strings(access.get("groups", []))
    access["hosts"] = _unique_strings(access.get("hosts", []))
    updated = aml_state.set_aml_partition_section(_validate_name(name), "access", access)
    if updated is None:
        raise HTTPException(status_code=404, detail="Partition not found")
    return AccessConfigResponse(accessConfig=AccessConfigResource.model_validate(updated))


@router.post("/partition/{name}/access/group", response_model=WSResultCode)
async def add_partition_access_group(
    name: str,
    payload: AccessGroupReference,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    partition_name = _validate_name(name)
    _get_partition_or_404(partition_name)
    group_name = _validate_identifier(payload.groupName, field_name="Group name")
    aml_state.add_aml_partition_access_value(partition_name, "groups", group_name)
    return _ws_result(f"Added access group {group_name} to partition {partition_name}")


@router.delete("/partition/{name}/access/group/{groupName}", response_model=WSResultCode)
async def remove_partition_access_group(
    name: str,
    groupName: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    partition_name = _validate_name(name)
    _get_partition_or_404(partition_name)
    group_name = _validate_identifier(groupName, field_name="Group name")
    if not aml_state.remove_aml_partition_access_value(partition_name, "groups", group_name):
        raise HTTPException(status_code=404, detail="Access group not assigned to partition")
    return _ws_result(f"Removed access group {group_name} from partition {partition_name}")


@router.post("/partition/{name}/access/host", response_model=WSResultCode)
async def add_partition_access_host(
    name: str,
    payload: HostReference,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    partition_name = _validate_name(name)
    _get_partition_or_404(partition_name)
    wwpn = _validate_identifier(payload.WWPN, field_name="WWPN")
    aml_state.ensure_aml_host(wwpn)
    aml_state.add_aml_partition_access_value(partition_name, "hosts", wwpn)
    return _ws_result(f"Added host {wwpn} to partition {partition_name}")


@router.delete("/partition/{name}/access/host/{WWPN}", response_model=WSResultCode)
async def remove_partition_access_host(
    name: str,
    WWPN: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    partition_name = _validate_name(name)
    _get_partition_or_404(partition_name)
    wwpn = _validate_identifier(WWPN, field_name="WWPN")
    if not aml_state.remove_aml_partition_access_value(partition_name, "hosts", wwpn):
        raise HTTPException(status_code=404, detail="Host not assigned to partition")
    return _ws_result(f"Removed host {wwpn} from partition {partition_name}")


@router.post("/partition/{name}/inventory", response_model=WSResultCode)
async def inventory_partition(
    name: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    partition_name = _validate_name(name)
    _get_partition_or_404(partition_name)
    return _ws_result(f"Inventory completed for partition {partition_name}")


@router.post("/partition/{name}/audit", response_model=WSResultCode)
async def audit_partition(
    name: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    partition_name = _validate_name(name)
    _get_partition_or_404(partition_name)
    return _ws_result(f"Audit completed for partition {partition_name}")


@router.post("/partition/{name}/online", response_model=WSResultCode)
async def online_partition(
    name: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    partition_name = _validate_name(name)
    if aml_state.update_aml_partition(partition_name, {"status": "online"}) is None:
        raise HTTPException(status_code=404, detail="Partition not found")
    return _ws_result(f"Partition {partition_name} is online")


@router.post("/partition/{name}/offline", response_model=WSResultCode)
async def offline_partition(
    name: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    partition_name = _validate_name(name)
    if aml_state.update_aml_partition(partition_name, {"status": "offline"}) is None:
        raise HTTPException(status_code=404, detail="Partition not found")
    return _ws_result(f"Partition {partition_name} is offline")


@router.get("/partition/{name}/cleaning", response_model=CleaningConfigResponse)
async def get_partition_cleaning(
    name: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> CleaningConfigResponse:
    _ensure_state(context)
    partition = _get_partition_or_404(_validate_name(name))
    return CleaningConfigResponse(cleaningConfig=CleaningConfigResource.model_validate(partition.get("cleaning", {})))


@router.put("/partition/{name}/cleaning", response_model=CleaningConfigResponse)
async def put_partition_cleaning(
    name: str,
    payload: CleaningConfigResponse,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> CleaningConfigResponse:
    _ensure_state(context)
    _require_admin(current_user)
    updated = aml_state.set_aml_partition_section(_validate_name(name), "cleaning", payload.cleaningConfig.model_dump())
    if updated is None:
        raise HTTPException(status_code=404, detail="Partition not found")
    return CleaningConfigResponse(cleaningConfig=CleaningConfigResource.model_validate(updated))


@router.post("/partition/{name}/cleaning/manual", response_model=WSResultCode)
async def manual_partition_cleaning(
    name: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    partition_name = _validate_name(name)
    partition = _get_partition_or_404(partition_name)
    cleaning = dict(partition.get("cleaning", {}))
    cleaning["lastCleaned"] = _timestamp()
    aml_state.set_aml_partition_section(partition_name, "cleaning", cleaning)
    return _ws_result(f"Manual cleaning completed for partition {partition_name}")


@router.get("/partition/{name}/statistics", response_model=StatisticsResponse)
async def get_partition_statistics(
    name: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> StatisticsResponse:
    _ensure_state(context)
    partition = _get_partition_or_404(_validate_name(name))
    return StatisticsResponse(statistics=_partition_statistics(partition, context))


@router.get("/partition/{name}/status", response_model=PartitionStatusResponse)
async def get_partition_status(
    name: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PartitionStatusResponse:
    _ensure_state(context)
    partition = _get_partition_or_404(_validate_name(name))
    return PartitionStatusResponse(partitionStatus=_partition_status(partition, context))


@router.get("/partition/{name}/worm", response_model=WormResponse)
async def get_partition_worm(
    name: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WormResponse:
    _ensure_state(context)
    partition = _get_partition_or_404(_validate_name(name))
    return WormResponse(worm=WormResource.model_validate(partition.get("worm", {})))


@router.put("/partition/{name}/worm", response_model=WormResponse)
async def put_partition_worm(
    name: str,
    payload: WormResponse,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WormResponse:
    _ensure_state(context)
    _require_admin(current_user)
    updated = aml_state.set_aml_partition_section(_validate_name(name), "worm", payload.worm.model_dump())
    if updated is None:
        raise HTTPException(status_code=404, detail="Partition not found")
    return WormResponse(worm=WormResource.model_validate(updated))


@router.get("/partition/{name}/encryption", response_model=EncryptionResponse)
async def get_partition_encryption(
    name: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> EncryptionResponse:
    _ensure_state(context)
    partition = _get_partition_or_404(_validate_name(name))
    return EncryptionResponse(encryption=EncryptionResource.model_validate(partition.get("encryption", {})))


@router.put("/partition/{name}/encryption", response_model=EncryptionResponse)
async def put_partition_encryption(
    name: str,
    payload: EncryptionResponse,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> EncryptionResponse:
    _ensure_state(context)
    _require_admin(current_user)
    updated = aml_state.set_aml_partition_section(_validate_name(name), "encryption", payload.encryption.model_dump())
    if updated is None:
        raise HTTPException(status_code=404, detail="Partition not found")
    return EncryptionResponse(encryption=EncryptionResource.model_validate(updated))


@router.get("/partition/{name}/qos", response_model=QoSResponse)
async def get_partition_qos(
    name: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> QoSResponse:
    _ensure_state(context)
    partition = _get_partition_or_404(_validate_name(name))
    return QoSResponse(qos=QoSResource.model_validate(partition.get("qos", {})))


@router.put("/partition/{name}/qos", response_model=QoSResponse)
async def put_partition_qos(
    name: str,
    payload: QoSResponse,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> QoSResponse:
    _ensure_state(context)
    _require_admin(current_user)
    updated = aml_state.set_aml_partition_section(_validate_name(name), "qos", payload.qos.model_dump())
    if updated is None:
        raise HTTPException(status_code=404, detail="Partition not found")
    return QoSResponse(qos=QoSResource.model_validate(updated))


@router.get("/partition/{name}/lme", response_model=LMEResponse)
async def get_partition_lme(
    name: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> LMEResponse:
    _ensure_state(context)
    partition = _get_partition_or_404(_validate_name(name))
    return LMEResponse(lme=LMEResource.model_validate(partition.get("lme", {})))


@router.put("/partition/{name}/lme", response_model=LMEResponse)
async def put_partition_lme(
    name: str,
    payload: LMEResponse,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> LMEResponse:
    _ensure_state(context)
    _require_admin(current_user)
    updated = aml_state.set_aml_partition_section(_validate_name(name), "lme", payload.lme.model_dump())
    if updated is None:
        raise HTTPException(status_code=404, detail="Partition not found")
    return LMEResponse(lme=LMEResource.model_validate(updated))


@router.get("/partition/{name}/report", response_model=ReportResponse)
async def get_partition_report(
    name: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> ReportResponse:
    _ensure_state(context)
    partition = _get_partition_or_404(_validate_name(name))
    return ReportResponse(report=_partition_report(partition, context))


@router.get("/partition/{name}/moveQueue", response_model=MoveQueueListResponse)
async def get_partition_move_queue(
    name: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> MoveQueueListResponse:
    _ensure_state(context)
    partition = _get_partition_or_404(_validate_name(name))
    return MoveQueueListResponse(
        moveQueueList=MoveQueueListResource(moveJob=[MoveJobResource.model_validate(item) for item in partition.get("moveQueue", [])])
    )


@router.delete("/partition/{name}/moveQueue", response_model=WSResultCode)
async def clear_partition_move_queue(
    name: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    partition_name = _validate_name(name)
    _get_partition_or_404(partition_name)
    aml_state.set_aml_partition_section(partition_name, "moveQueue", [])
    return _ws_result(f"Cleared move queue for partition {partition_name}")


@router.get("/partition/{name}/alerts", response_model=AlertListResponse)
async def get_partition_alerts(
    name: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> AlertListResponse:
    _ensure_state(context)
    partition = _get_partition_or_404(_validate_name(name))
    return AlertListResponse(alertList=AlertListResource(alert=[AlertResource.model_validate(item) for item in partition.get("alerts", [])]))


@router.get("/partition/{name}/quota", response_model=QuotaResponse)
async def get_partition_quota(
    name: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> QuotaResponse:
    _ensure_state(context)
    partition = _get_partition_or_404(_validate_name(name))
    return QuotaResponse(quota=QuotaResource.model_validate(_partition_quota_dict(partition, context)))


@router.put("/partition/{name}/quota", response_model=QuotaResponse)
async def put_partition_quota(
    name: str,
    payload: QuotaUpdateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> QuotaResponse:
    _ensure_state(context)
    _require_admin(current_user)
    quota = payload.quota.model_dump()
    for field_name, value in quota.items():
        quota[field_name] = _validate_non_negative(int(value), field_name=field_name)
    updated = aml_state.set_aml_partition_section(_validate_name(name), "quota", quota)
    if updated is None:
        raise HTTPException(status_code=404, detail="Partition not found")
    return QuotaResponse(quota=QuotaResource.model_validate(updated))
