"""AML operations and job-management routes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from openblade.api import aml_state
from openblade.api.routes_aml_auth import WSResultCode, _ensure_state, _require_admin, require_auth
from openblade.api.service_auth import require_service_token
from openblade.bootstrap import AppContext, get_context
from openblade.catalog.models import AmlUser

router = APIRouter()


def _normalize_move_address(address: str) -> str:
    value = address.strip()
    if not value:
        return value
    upper = value.upper()
    if upper.startswith("DRV-") or upper.startswith("IE-"):
        return upper
    if value.isdigit():
        return f"1,1,{int(value)}"
    parts = [part.strip() for part in value.split(",")]
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        return f"{int(parts[0])},{int(parts[1])},{int(parts[2])}"
    return value


def _slot_suffix(address: str) -> str:
    normalized = _normalize_move_address(address)
    parts = normalized.split(",")
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        return str(int(parts[-1]))
    return normalized


class MoveOperation(BaseModel):
    id: str
    source: str
    destination: str
    barcode: str
    status: str
    startTime: str
    completedTime: str | None = None


class MoveRequestPayload(BaseModel):
    source: str
    destination: str
    barcode: str


class MoveRequest(BaseModel):
    move: MoveRequestPayload


class MoveResource(BaseModel):
    move: MoveOperation


class MoveListResource(BaseModel):
    move: list[MoveOperation]


class MoveResponse(BaseModel):
    move: MoveOperation


class MoveListResponse(BaseModel):
    moveList: MoveListResource


class MountOperation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    barcode: str
    drive: str
    partition: str | None = None
    mountTime: str
    state: str


class MountRequestPayload(BaseModel):
    barcode: str
    drive: str
    partition: str | None = None


class MountRequest(BaseModel):
    mount: MountRequestPayload


class UnmountRequestPayload(BaseModel):
    barcode: str
    drive: str


class UnmountRequest(BaseModel):
    unmount: UnmountRequestPayload


class MountResponse(BaseModel):
    mount: MountOperation


class MountListResource(BaseModel):
    mount: list[MountOperation]


class MountListResponse(BaseModel):
    mountList: MountListResource


class InventoryStatus(BaseModel):
    state: str
    startTime: str | None = None
    completedTime: str | None = None
    progress: int
    elementsScanned: int
    elementsTotal: int


class InventoryStatusResponse(BaseModel):
    inventoryStatus: InventoryStatus


class InventoryResult(BaseModel):
    timestamp: str | None = None
    elementsScanned: int
    mediaFound: int
    emptySlots: int
    errors: list[str] = Field(default_factory=list)


class InventoryResultResponse(BaseModel):
    inventoryResult: InventoryResult


class ImportRequestPayload(BaseModel):
    partition: str
    ieStation: str


class ImportRequest(BaseModel):
    import_: ImportRequestPayload = Field(validation_alias="import", serialization_alias="import")


class ExportRequestPayload(BaseModel):
    barcodes: list[str]
    ieStation: str


class ExportRequest(BaseModel):
    export: ExportRequestPayload


class OperationState(BaseModel):
    model_config = ConfigDict(extra="allow")

    state: str
    startTime: str | None = None
    completedTime: str | None = None


class ImportStatusResponse(BaseModel):
    importStatus: OperationState


class ExportStatusResponse(BaseModel):
    exportStatus: OperationState


class ShutdownRequestPayload(BaseModel):
    delay: int = 0
    force: bool = False


class ShutdownRequest(BaseModel):
    shutdown: ShutdownRequestPayload


class RestartRequestPayload(BaseModel):
    delay: int = 0


class RestartRequest(BaseModel):
    restart: RestartRequestPayload


class OperationsStatus(BaseModel):
    state: str
    activeJobs: int
    pendingJobs: int
    lastOperation: str | None = None


class OperationsStatusResponse(BaseModel):
    operationsStatus: OperationsStatus


class Job(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    type: str
    status: str
    priority: str
    startTime: str
    completedTime: str | None = None
    progress: int = 0
    result: str | None = None


class JobResponse(BaseModel):
    job: Job


class JobListResource(BaseModel):
    job: list[Job]


class JobListResponse(BaseModel):
    jobList: JobListResource


class VerifyRequestPayload(BaseModel):
    barcodes: list[str]


class VerifyRequest(BaseModel):
    verify: VerifyRequestPayload


class ScratchAssignPayload(BaseModel):
    barcode: str
    partition: str


class ScratchAssignRequest(BaseModel):
    assign: ScratchAssignPayload


class ScratchReclaimPayload(BaseModel):
    barcode: str


class ScratchReclaimRequest(BaseModel):
    reclaim: ScratchReclaimPayload


class CleanRequestPayload(BaseModel):
    drives: list[str]


class CleanRequest(BaseModel):
    clean: CleanRequestPayload


class CleaningStatus(BaseModel):
    state: str
    startTime: str | None = None
    completedTime: str | None = None
    drives: list[str] = Field(default_factory=list)


class CleaningStatusResponse(BaseModel):
    cleaningStatus: CleaningStatus


class RoboticsStatus(BaseModel):
    state: str
    robotsOnline: int
    robotsBusy: int
    lastTestTime: str | None = None


class RoboticsStatusResponse(BaseModel):
    roboticsStatus: RoboticsStatus


class Capacity(BaseModel):
    totalSlots: int
    usedSlots: int
    freeSlots: int
    totalDrives: int
    activeDrives: int
    mediaCount: int
    scratchCount: int


class CapacityResponse(BaseModel):
    capacity: Capacity


class ThroughputWindow(BaseModel):
    mounts: int
    unmounts: int
    moves: int


class Throughput(BaseModel):
    mountsPerHour: int
    unmountsPerHour: int
    movesPerHour: int
    lastHour: ThroughputWindow
    lastDay: ThroughputWindow
    lastWeek: ThroughputWindow


class ThroughputResponse(BaseModel):
    throughput: Throughput


class QueueStatus(BaseModel):
    pending: int
    active: int
    completed: int
    failed: int
    paused: int


class QueueStatusResponse(BaseModel):
    queueStatus: QueueStatus


class GenericStatusResponse(BaseModel):
    status: OperationState



def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")



def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None



def _ws_result(summary: str = "Operation completed") -> WSResultCode:
    return WSResultCode(summary=summary)



def _validate_identifier(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail=f"{field_name} is required")
    return normalized



def _validate_barcodes(barcodes: list[str]) -> list[str]:
    values = [_validate_identifier(barcode, field_name="barcode") for barcode in barcodes]
    if not values:
        raise HTTPException(status_code=400, detail="At least one barcode is required")
    return values



def _serialize_move(item: dict[str, Any]) -> MoveOperation:
    return MoveOperation.model_validate(item)



def _serialize_mount(item: dict[str, Any]) -> MountOperation:
    return MountOperation.model_validate(item)



def _serialize_job(item: dict[str, Any]) -> Job:
    return Job.model_validate(item)



def _sorted_jobs(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda item: (item.get("startTime") or "", item.get("id") or ""), reverse=True)



def _get_media_or_404(barcode: str) -> dict[str, Any]:
    media = aml_state.get_aml_media(barcode)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    return media



def _get_drive_or_404(drive: str) -> dict[str, Any]:
    drive_data = aml_state.get_aml_drive(drive)
    if drive_data is None:
        raise HTTPException(status_code=404, detail="Drive not found")
    return drive_data



def _get_partition_or_404(name: str) -> dict[str, Any]:
    partition = aml_state.get_aml_partition(name)
    if partition is None:
        raise HTTPException(status_code=404, detail="Partition not found")
    return partition



def _get_ie_station_or_404(name: str) -> dict[str, Any]:
    station = aml_state.get_aml_ie_station(name)
    if station is None:
        raise HTTPException(status_code=404, detail="IE station not found")
    return station



def _get_move_or_404(move_id: str) -> dict[str, Any]:
    move = aml_state.get_aml_move(move_id)
    if move is None:
        raise HTTPException(status_code=404, detail="Move not found")
    return move



def _get_mount_or_404(mount_id: str) -> dict[str, Any]:
    mount = aml_state.get_aml_mount(mount_id)
    if mount is None:
        raise HTTPException(status_code=404, detail="Mount not found")
    return mount



def _get_job_or_404(job_id: str) -> dict[str, Any]:
    job = aml_state.get_aml_job(job_id)
    if job is not None:
        return job
    for history_job in aml_state.list_aml_job_history():
        if history_job.get("id") == job_id:
            return history_job
    raise HTTPException(status_code=404, detail="Job not found")



def _create_job(operation_type: str, *, priority: str = "normal", result: str | None = None) -> dict[str, Any]:
    job_id = str(uuid4())
    job = {
        "id": job_id,
        "type": operation_type,
        "status": "pending",
        "priority": priority,
        "startTime": _timestamp(),
        "completedTime": None,
        "progress": 0,
        "result": result,
    }
    return aml_state.set_aml_job(job_id, job)



def _archive_job(job_id: str, *, status: str, result: str) -> dict[str, Any] | None:
    job = aml_state.pop_aml_job(job_id)
    if job is None:
        return None
    job["status"] = status
    job["completedTime"] = _timestamp()
    job["progress"] = 100 if status == "completed" else int(job.get("progress", 0))
    job["result"] = result
    return aml_state.append_aml_job_history(job)



def _find_mount(*, barcode: str | None = None, drive: str | None = None) -> tuple[str, dict[str, Any]] | None:
    for mount in aml_state.list_aml_mounts():
        if barcode is not None and mount.get("barcode") != barcode:
            continue
        if drive is not None and mount.get("drive") != drive:
            continue
        return str(mount["id"]), mount
    return None



def _queue_counts() -> QueueStatus:
    active_jobs = aml_state.list_aml_jobs()
    history_jobs = aml_state.list_aml_job_history()
    return QueueStatus(
        pending=sum(1 for job in active_jobs if job.get("status") == "pending"),
        active=sum(1 for job in active_jobs if job.get("status") == "active"),
        completed=sum(1 for job in history_jobs if job.get("status") == "completed"),
        failed=sum(1 for job in history_jobs if job.get("status") in {"failed", "cancelled"}),
        paused=sum(1 for job in active_jobs if job.get("status") == "paused"),
    )



def _throughput_window(since: datetime) -> ThroughputWindow:
    counts = {"mount": 0, "unmount": 0, "move": 0}
    for job in aml_state.list_aml_job_history():
        completed_at = _parse_timestamp(job.get("completedTime"))
        if completed_at is None or completed_at < since:
            continue
        job_type = str(job.get("type", "")).lower()
        if job_type in counts:
            counts[job_type] += 1
    return ThroughputWindow(mounts=counts["mount"], unmounts=counts["unmount"], moves=counts["move"])



def _operations_status() -> OperationsStatus:
    active_jobs = aml_state.list_aml_jobs()
    active_count = sum(1 for job in active_jobs if job.get("status") == "active")
    pending_count = sum(1 for job in active_jobs if job.get("status") == "pending")
    last_operation: dict[str, Any] | None = None
    combined = active_jobs + aml_state.list_aml_job_history()
    if combined:
        last_operation = max(
            combined,
            key=lambda item: (item.get("completedTime") or item.get("startTime") or "", item.get("id") or ""),
        )
    if active_count:
        state = "active"
    elif pending_count:
        state = "pending"
    elif aml_state.get_library_mode() == "offline":
        state = "offline"
    else:
        state = "idle"
    return OperationsStatus(
        state=state,
        activeJobs=active_count,
        pendingJobs=pending_count,
        lastOperation=None if last_operation is None else str(last_operation.get("type")),
    )



def _capacity() -> Capacity:
    partitions = aml_state.list_aml_partitions()
    drives = aml_state.list_aml_drives()
    media = aml_state.list_aml_media()
    total_slots = sum(int(partition.get("slotCount", 0)) for partition in partitions)
    used_slots = len(media)
    return Capacity(
        totalSlots=total_slots,
        usedSlots=used_slots,
        freeSlots=max(total_slots - used_slots, 0),
        totalDrives=len(drives),
        activeDrives=sum(1 for drive in drives if str(drive.get("status", "")).lower() == "online"),
        mediaCount=len(media),
        scratchCount=len(aml_state.list_aml_scratch_media()),
    )



def _throughput() -> Throughput:
    now = datetime.now(timezone.utc)
    last_hour = _throughput_window(now - timedelta(hours=1))
    last_day = _throughput_window(now - timedelta(days=1))
    last_week = _throughput_window(now - timedelta(days=7))
    return Throughput(
        mountsPerHour=last_hour.mounts,
        unmountsPerHour=last_hour.unmounts,
        movesPerHour=last_hour.moves,
        lastHour=last_hour,
        lastDay=last_day,
        lastWeek=last_week,
    )



def _robotics_status() -> RoboticsStatus:
    robots = aml_state.get_aml_robots()
    robot_items = list(robots.values())
    return RoboticsStatus(
        state="busy" if any(str(robot.get("state", "")).lower() not in {"idle", "homed"} for robot in robot_items) else "ready",
        robotsOnline=sum(1 for robot in robot_items if str(robot.get("status", "")).lower() == "online"),
        robotsBusy=sum(1 for robot in robot_items if str(robot.get("state", "")).lower() not in {"idle", "homed"}),
        lastTestTime=aml_state.get_aml_robotics_last_test_time(),
    )


@router.post("/operations/move", response_model=WSResultCode, dependencies=[Depends(require_auth)])
@router.post("/move", response_model=WSResultCode, dependencies=[Depends(require_auth)])
async def create_move(
    current_user: AmlUser = Depends(require_auth),
    payload: dict = Body(...),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    # Support both new shape {"move": {"source":..., "destination":..., "barcode":...}}
    # and legacy UI shape {"sourceSlot": X, "targetDrive": Y, "barcode": Z}
    if isinstance(payload, dict) and "move" in payload and isinstance(payload["move"], dict):
        data = payload["move"]
    else:
        data = payload

    def _first_present(d: dict, keys: list[str]):
        for k in keys:
            if k in d:
                return d.get(k)
        return None

    source_raw = _first_present(data, ["source", "sourceSlot", "sourceAddress", "slot"])
    dest_raw = _first_present(data, ["destination", "targetDrive", "drive", "target"])
    barcode_raw = _first_present(data, ["barcode", "label", "volumeLabel"]) or ""

    # Debug logging to help diagnose legacy payload handling (temporary)
    try:
        print(f"DEBUG create_move payload={payload} source_raw={source_raw} dest_raw={dest_raw} barcode_raw={barcode_raw}")
    except Exception:
        pass

    # Debug logging to help diagnose legacy payload handling (temporary)
    try:
        print(f"DEBUG create_move payload={payload} source_raw={source_raw} dest_raw={dest_raw} barcode_raw={barcode_raw}")
    except Exception:
        pass

    # If barcode is missing, try to infer it from the source slot in the current inventory
    if not barcode_raw and source_raw:
        try:
            inventory = context.library.inventory()
            # source_raw is often an integer slot id
            target_slot_id = int(source_raw) if isinstance(source_raw, (int, str)) and str(source_raw).isdigit() else None
            found = None
            if target_slot_id is not None:
                for slot in inventory.slots:
                    if slot.slot_id == target_slot_id:
                        found = slot
                        break
            # Fallback: try matching string address
            if found is None:
                for slot in inventory.slots:
                    if str(slot.slot_id) == str(source_raw):
                        found = slot
                        break
            if found and getattr(found, "barcode", None):
                barcode_raw = str(found.barcode)
            else:
                # Fallback: scan aml_state media entries for a matching slotAddress
                try:
                    for media in aml_state.list_aml_media().values():
                        slot_addr = str(media.get("slotAddress", ""))
                        if slot_addr and (slot_addr == str(source_raw) or slot_addr.endswith("," + str(source_raw))):
                            barcode_raw = str(media.get("barcode"))
                            break
                except Exception:
                    pass
        except Exception:
            # If inventory lookup fails, continue and let validation handle it
            barcode_raw = barcode_raw

    # If any core move fields are missing, return 422 (validation error) so clients see a clear validation response
    def _is_missing(val: object) -> bool:
        if val is None:
            return True
        if isinstance(val, str) and not val.strip():
            return True
        return False

    if _is_missing(source_raw) or _is_missing(dest_raw) or _is_missing(barcode_raw):
        raise HTTPException(status_code=422, detail="Missing move fields")

    source = _normalize_move_address(_validate_identifier(str(source_raw or ""), field_name="source"))
    destination = _normalize_move_address(_validate_identifier(str(dest_raw or ""), field_name="destination"))
    barcode = _validate_identifier(str(barcode_raw or ""), field_name="barcode")

    media = _get_media_or_404(barcode)
    media_source = _normalize_move_address(str(media.get("slotAddress") or ""))
    if media_source not in {source, _slot_suffix(source)} and _slot_suffix(media_source) != _slot_suffix(source):
        raise HTTPException(status_code=409, detail="Media is not at the requested source")
    job = _create_job("move")
    aml_state.set_aml_move(
        job["id"],
        {
            "id": job["id"],
            "source": source,
            "destination": destination,
            "barcode": barcode,
            "status": "pending",
            "startTime": job["startTime"],
            "completedTime": None,
        },
    )
    return _ws_result(f"Queued move for {barcode}")


@router.get("/moves", response_model=MoveListResponse)
async def list_moves(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> MoveListResponse:
    _ensure_state(context)
    moves = [_serialize_move(item) for item in _sorted_jobs(aml_state.list_aml_moves())]
    return MoveListResponse(moveList=MoveListResource(move=moves))


@router.get("/move/{id}", response_model=MoveResponse)
async def get_move(
    resource_id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> MoveResponse:
    _ensure_state(context)
    move_id = _validate_identifier(resource_id, field_name="id")
    return MoveResponse(move=_serialize_move(_get_move_or_404(move_id)))


@router.delete("/move/{id}", response_model=WSResultCode)
async def cancel_move(
    resource_id: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    move_id = _validate_identifier(resource_id, field_name="id")
    move = _get_move_or_404(move_id)
    if move.get("status") != "pending":
        raise HTTPException(status_code=409, detail="Only pending moves can be cancelled")
    aml_state.pop_aml_move(move_id)
    _archive_job(move_id, status="cancelled", result="Move cancelled")
    return _ws_result(f"Cancelled move {move_id}")


@router.post("/mount", response_model=WSResultCode, dependencies=[Depends(require_service_token)])
async def create_mount(
    payload: MountRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    barcode = _validate_identifier(payload.mount.barcode, field_name="barcode")
    drive_name = _validate_identifier(payload.mount.drive, field_name="drive")
    partition = None if payload.mount.partition is None else _validate_identifier(payload.mount.partition, field_name="partition")
    media = _get_media_or_404(barcode)
    drive = _get_drive_or_404(drive_name)
    if partition is not None:
        _get_partition_or_404(partition)
    if drive.get("loadedMedia"):
        raise HTTPException(status_code=409, detail="Drive already has mounted media")
    if _find_mount(barcode=barcode) is not None:
        raise HTTPException(status_code=409, detail="Media is already mounted")
    job = _create_job("mount")
    mount_time = job["startTime"]
    aml_state.update_aml_drive(drive_name, {"loadedMedia": {"barcode": barcode, "type": media.get("type", "LTO-9"), "state": "loaded"}, "state": "mounted"})
    aml_state.update_aml_media(
        barcode,
        {
            "partition": partition or media.get("partition"),
            "slotAddress": drive_name,
            "state": "loaded",
            "lastLoaded": mount_time,
            "loadCount": int(media.get("loadCount", 0)) + 1,
        },
    )
    aml_state.set_aml_mount(
        job["id"],
        {
            "id": job["id"],
            "barcode": barcode,
            "drive": drive_name,
            "partition": partition or media.get("partition"),
            "mountTime": mount_time,
            "state": "mounted",
            "previousSlot": media.get("slotAddress"),
        },
    )
    aml_state.set_aml_drive_operation_task(
        job["id"],
        {
            "id": job["id"],
            "componentId": drive_name,
            "type": "load",
            "opened": mount_time,
            "closed": mount_time,
            "state": 5,
            "status": "Completed",
            "description": f"Loaded {barcode} into drive {drive_name}",
            "sessionId": None,
        },
    )
    aml_state.update_aml_job(job["id"], {"status": "active", "progress": 50, "result": f"Mounted {barcode} on {drive_name}"})
    _archive_job(job["id"], status="completed", result=f"Mounted {barcode} on {drive_name}")
    return _ws_result(f"Mounted {barcode} on {drive_name}")


@router.post("/unmount", response_model=WSResultCode, dependencies=[Depends(require_service_token)])
async def create_unmount(
    payload: UnmountRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    barcode = _validate_identifier(payload.unmount.barcode, field_name="barcode")
    drive_name = _validate_identifier(payload.unmount.drive, field_name="drive")
    _get_media_or_404(barcode)
    _get_drive_or_404(drive_name)
    located = _find_mount(barcode=barcode, drive=drive_name)
    if located is None:
        raise HTTPException(status_code=404, detail="Mount not found")
    mount_id, mount = located
    job = _create_job("unmount")
    previous_slot = str(mount.get("previousSlot") or "1,1,1")
    aml_state.pop_aml_mount(mount_id)
    aml_state.update_aml_drive(drive_name, {"loadedMedia": None, "state": "idle"})
    aml_state.update_aml_media(barcode, {"slotAddress": previous_slot, "state": "home"})
    aml_state.set_aml_drive_operation_task(
        job["id"],
        {
            "id": job["id"],
            "componentId": drive_name,
            "type": "unload",
            "opened": job["startTime"],
            "closed": job["startTime"],
            "state": 5,
            "status": "Completed",
            "description": f"Unloaded {barcode} from drive {drive_name}",
            "sessionId": None,
        },
    )
    _archive_job(job["id"], status="completed", result=f"Unmounted {barcode} from {drive_name}")
    return _ws_result(f"Unmounted {barcode} from {drive_name}")


@router.get("/mounts", response_model=MountListResponse)
async def list_mounts(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> MountListResponse:
    _ensure_state(context)
    mounts = [_serialize_mount(item) for item in _sorted_jobs(aml_state.list_aml_mounts())]
    return MountListResponse(mountList=MountListResource(mount=mounts))


@router.get("/mount/{id}", response_model=MountResponse)
async def get_mount(
    resource_id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> MountResponse:
    _ensure_state(context)
    mount_id = _validate_identifier(resource_id, field_name="id")
    return MountResponse(mount=_serialize_mount(_get_mount_or_404(mount_id)))


@router.post("/inventory", response_model=WSResultCode)
@router.post("/operations/inventory", response_model=WSResultCode)
async def trigger_inventory(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    job = _create_job("inventory")
    aml_state.set_aml_inventory_status(
        {
            "state": "running",
            "startTime": job["startTime"],
            "completedTime": None,
            "progress": 0,
            "elementsScanned": 0,
            "elementsTotal": max(len(aml_state.list_aml_media()) + len(aml_state.list_aml_drives()), 1),
        }
    )
    return _ws_result("Inventory started")


@router.get("/inventory/status", response_model=InventoryStatusResponse)
@router.get("/operations/inventory/status", response_model=InventoryStatusResponse)
async def get_inventory_status(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> InventoryStatusResponse:
    _ensure_state(context)
    return InventoryStatusResponse(inventoryStatus=InventoryStatus.model_validate(aml_state.get_aml_inventory_status()))


@router.post("/inventory/partition/{name}", response_model=WSResultCode)
async def trigger_partition_inventory(
    name: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    partition_name = _validate_identifier(name, field_name="partition")
    _get_partition_or_404(partition_name)
    job = _create_job("inventory-partition")
    partition_media = [item for item in aml_state.list_aml_media() if item.get("partition") == partition_name]
    aml_state.set_aml_inventory_status(
        {
            "state": "running",
            "startTime": job["startTime"],
            "completedTime": None,
            "progress": 0,
            "elementsScanned": 0,
            "elementsTotal": max(len(partition_media), 1),
            "partition": partition_name,
        }
    )
    return _ws_result(f"Partition inventory started for {partition_name}")


@router.get("/inventory/results", response_model=InventoryResultResponse)
async def get_inventory_results(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> InventoryResultResponse:
    _ensure_state(context)
    inventory_status = aml_state.get_aml_inventory_status()
    capacity = _capacity()
    return InventoryResultResponse(
        inventoryResult=InventoryResult(
            timestamp=inventory_status.get("completedTime") or inventory_status.get("startTime"),
            elementsScanned=int(inventory_status.get("elementsScanned", 0)),
            mediaFound=capacity.mediaCount,
            emptySlots=capacity.freeSlots,
            errors=list(inventory_status.get("errors", [])),
        )
    )


@router.post("/import", response_model=WSResultCode)
async def start_import(
    payload: ImportRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    partition = _validate_identifier(payload.import_.partition, field_name="partition")
    ie_station = _validate_identifier(payload.import_.ieStation, field_name="ieStation")
    _get_partition_or_404(partition)
    _get_ie_station_or_404(ie_station)
    job = _create_job("import")
    aml_state.set_aml_import_status(
        {
            "state": "running",
            "startTime": job["startTime"],
            "completedTime": None,
            "partition": partition,
            "ieStation": ie_station,
        }
    )
    return _ws_result(f"Import started on {ie_station}")


@router.get("/import/status", response_model=ImportStatusResponse)
@router.get("/operations/import/status", response_model=ImportStatusResponse)
async def get_import_status(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> ImportStatusResponse:
    _ensure_state(context)
    return ImportStatusResponse(importStatus=OperationState.model_validate(aml_state.get_aml_import_status()))


@router.post("/export", response_model=WSResultCode)
async def start_export(
    payload: ExportRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    barcodes = _validate_barcodes(payload.export.barcodes)
    ie_station = _validate_identifier(payload.export.ieStation, field_name="ieStation")
    _get_ie_station_or_404(ie_station)
    for barcode in barcodes:
        _get_media_or_404(barcode)
    job = _create_job("export")
    aml_state.set_aml_export_status(
        {
            "state": "running",
            "startTime": job["startTime"],
            "completedTime": None,
            "barcodes": barcodes,
            "ieStation": ie_station,
        }
    )
    return _ws_result(f"Export started on {ie_station}")


@router.get("/export/status", response_model=ExportStatusResponse)
@router.get("/operations/export/status", response_model=ExportStatusResponse)
async def get_export_status(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> ExportStatusResponse:
    _ensure_state(context)
    return ExportStatusResponse(exportStatus=OperationState.model_validate(aml_state.get_aml_export_status()))


@router.post("/shutdown", response_model=WSResultCode)
async def shutdown_library(
    payload: ShutdownRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    _create_job("shutdown")
    aml_state.set_library_mode("offline")
    return _ws_result(f"Shutdown scheduled in {payload.shutdown.delay} seconds")


@router.post("/restart", response_model=WSResultCode)
async def restart_library(
    payload: RestartRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    _create_job("restart")
    aml_state.set_library_mode("online")
    return _ws_result(f"Restart scheduled in {payload.restart.delay} seconds")


@router.get("/operations/status", response_model=OperationsStatusResponse)
async def get_operations_status(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> OperationsStatusResponse:
    _ensure_state(context)
    return OperationsStatusResponse(operationsStatus=_operations_status())


@router.get("/jobs", response_model=JobListResponse)
async def list_jobs(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> JobListResponse:
    _ensure_state(context)
    jobs = [_serialize_job(item) for item in _sorted_jobs(aml_state.list_aml_jobs())]
    return JobListResponse(jobList=JobListResource(job=jobs))


@router.get("/jobs/history", response_model=JobListResponse)
async def list_job_history(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> JobListResponse:
    _ensure_state(context)
    jobs = [item for item in aml_state.list_aml_job_history() if item.get("status") in {"completed", "cancelled", "failed"}]
    return JobListResponse(jobList=JobListResource(job=[_serialize_job(item) for item in _sorted_jobs(jobs)]))


@router.delete("/jobs/history", response_model=WSResultCode)
async def clear_job_history(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    aml_state.clear_aml_job_history()
    return _ws_result("Cleared job history")


@router.post("/operations/audit", response_model=WSResultCode)
async def run_audit(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    _create_job("audit")
    return _ws_result("Audit started")


@router.post("/operations/calibrate", response_model=WSResultCode)
async def run_calibration(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    _create_job("calibrate")
    return _ws_result("Calibration started")


@router.post("/operations/verify", response_model=WSResultCode)
async def verify_media(
    payload: VerifyRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    barcodes = _validate_barcodes(payload.verify.barcodes)
    for barcode in barcodes:
        _get_media_or_404(barcode)
    _create_job("verify", result=f"Queued verification for {len(barcodes)} media")
    return _ws_result("Verification started")


@router.post("/operations/scratch/assign", response_model=WSResultCode)
async def assign_scratch(
    payload: ScratchAssignRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    barcode = _validate_identifier(payload.assign.barcode, field_name="barcode")
    partition = _validate_identifier(payload.assign.partition, field_name="partition")
    _get_partition_or_404(partition)
    _get_media_or_404(barcode)
    _create_job("scratch-assign")
    aml_state.update_aml_media(barcode, {"partition": partition, "state": "scratch"})
    return _ws_result(f"Assigned scratch media {barcode} to {partition}")


@router.post("/operations/scratch/reclaim", response_model=WSResultCode)
async def reclaim_scratch(
    payload: ScratchReclaimRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    barcode = _validate_identifier(payload.reclaim.barcode, field_name="barcode")
    _get_media_or_404(barcode)
    _create_job("scratch-reclaim")
    aml_state.update_aml_media(barcode, {"state": "home"})
    return _ws_result(f"Reclaimed scratch media {barcode}")


@router.post("/operations/clean", response_model=WSResultCode)
async def clean_drives(
    payload: CleanRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    drives = [_validate_identifier(drive, field_name="drive") for drive in payload.clean.drives]
    if not drives:
        raise HTTPException(status_code=400, detail="At least one drive is required")
    cleaned_at = _timestamp()
    for drive_name in drives:
        drive = _get_drive_or_404(drive_name)
        aml_state.update_aml_drive(
            drive_name,
            {
                "state": "cleaning",
                "cleaningCount": int(drive.get("cleaningCount", 0)) + 1,
                "lastCleaned": cleaned_at,
            },
        )
        aml_state.set_aml_drive_operation_task(
            f"{drive_name}-{uuid4().hex[:8]}",
            {
                "componentId": drive_name,
                "type": "clean",
                "opened": cleaned_at,
                "closed": None,
                "state": 1,
                "status": "Running",
                "description": f"Cleaning drive {drive_name}",
                "sessionId": None,
            },
        )
    job = _create_job("clean")
    aml_state.set_aml_cleaning_status({"state": "running", "startTime": job["startTime"], "completedTime": None, "drives": drives})
    aml_state.update_aml_job(job["id"], {"status": "active", "progress": 25, "result": f"Cleaning {len(drives)} drives"})
    return _ws_result("Cleaning started")


@router.post("/operations/cleaning", response_model=WSResultCode)
async def clean_drives_compat(
    payload: dict[str, Any] = Body(default_factory=dict),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    raw_payload = payload.get("clean") if isinstance(payload.get("clean"), dict) else payload
    drives = raw_payload.get("drives")
    if not isinstance(drives, list):
        raise HTTPException(status_code=422, detail="drives must be provided")
    validated_drives = [_validate_identifier(drive, field_name="drive") for drive in drives]
    if not validated_drives:
        raise HTTPException(status_code=400, detail="At least one drive is required")
    cleaned_at = _timestamp()
    for drive_name in validated_drives:
        drive = _get_drive_or_404(drive_name)
        aml_state.update_aml_drive(
            drive_name,
            {
                "state": "cleaning",
                "cleaningCount": int(drive.get("cleaningCount", 0)) + 1,
                "lastCleaned": cleaned_at,
            },
        )
        aml_state.set_aml_drive_operation_task(
            f"{drive_name}-{uuid4().hex[:8]}",
            {
                "componentId": drive_name,
                "type": "clean",
                "opened": cleaned_at,
                "closed": None,
                "state": 1,
                "status": "Running",
                "description": f"Cleaning drive {drive_name}",
                "sessionId": None,
            },
        )
    job = _create_job("clean")
    aml_state.set_aml_cleaning_status({"state": "running", "startTime": job["startTime"], "completedTime": None, "drives": validated_drives})
    aml_state.update_aml_job(job["id"], {"status": "active", "progress": 25, "result": f"Cleaning {len(validated_drives)} drives"})
    return _ws_result("Cleaning started")


@router.get("/operations/cleaning/status", response_model=CleaningStatusResponse)
async def get_cleaning_status(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> CleaningStatusResponse:
    _ensure_state(context)
    return CleaningStatusResponse(cleaningStatus=CleaningStatus.model_validate(aml_state.get_aml_cleaning_status()))


@router.post("/operations/robotics/home", response_model=WSResultCode)
async def home_robots(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    for robot_id in aml_state.get_aml_robots():
        aml_state.update_aml_robot(robot_id, {"state": "homed"})
    _create_job("robotics-home")
    return _ws_result("Robots homed")


@router.get("/operations/robotics/status", response_model=RoboticsStatusResponse)
async def get_robotics_status(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> RoboticsStatusResponse:
    _ensure_state(context)
    return RoboticsStatusResponse(roboticsStatus=_robotics_status())


@router.post("/operations/robotics/test", response_model=WSResultCode)
async def test_robotics(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    aml_state.set_aml_robotics_last_test_time(_timestamp())
    for robot_id in aml_state.get_aml_robots():
        aml_state.update_aml_robot(robot_id, {"state": "testing"})
    _create_job("robotics-test")
    return _ws_result("Robotics test started")


@router.get("/operations/capacity", response_model=CapacityResponse)
async def get_capacity(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> CapacityResponse:
    _ensure_state(context)
    return CapacityResponse(capacity=_capacity())


@router.get("/operations/throughput", response_model=ThroughputResponse)
async def get_throughput(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> ThroughputResponse:
    _ensure_state(context)
    return ThroughputResponse(throughput=_throughput())


@router.get("/operations/queue", response_model=QueueStatusResponse)
async def get_queue_status(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> QueueStatusResponse:
    _ensure_state(context)
    return QueueStatusResponse(queueStatus=_queue_counts())


@router.get("/job/{id}", response_model=JobResponse)
async def get_job(
    resource_id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> JobResponse:
    _ensure_state(context)
    job_id = _validate_identifier(resource_id, field_name="id")
    return JobResponse(job=_serialize_job(_get_job_or_404(job_id)))


@router.delete("/job/{id}", response_model=WSResultCode)
async def cancel_job(
    resource_id: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    job_id = _validate_identifier(resource_id, field_name="id")
    job = aml_state.get_aml_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") not in {"pending", "active", "paused"}:
        raise HTTPException(status_code=409, detail="Job cannot be cancelled")
    if job.get("type") == "move":
        aml_state.pop_aml_move(job_id)
    _archive_job(job_id, status="cancelled", result="Job cancelled")
    return _ws_result(f"Cancelled job {job_id}")


@router.post("/job/{id}/pause", response_model=WSResultCode)
async def pause_job(
    resource_id: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    job_id = _validate_identifier(resource_id, field_name="id")
    job = aml_state.get_aml_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") not in {"pending", "active"}:
        raise HTTPException(status_code=409, detail="Job cannot be paused")
    aml_state.update_aml_job(job_id, {"status": "paused"})
    return _ws_result(f"Paused job {job_id}")


@router.post("/job/{id}/resume", response_model=WSResultCode)
async def resume_job(
    resource_id: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    job_id = _validate_identifier(resource_id, field_name="id")
    job = aml_state.get_aml_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") != "paused":
        raise HTTPException(status_code=409, detail="Job is not paused")
    aml_state.update_aml_job(job_id, {"status": "active"})
    return _ws_result(f"Resumed job {job_id}")
