"""AML library overview routes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from openblade.api import aml_state
from openblade.api.routes_aml_auth import WSResultCode, _require_admin, require_auth
from openblade.bootstrap import AppContext, get_context
from openblade.catalog.models import AmlUser
from openblade.domain.models import CartridgeState, ChangerState, DriveState
from openblade.domain.scalar_coordinate import ScalarCoordinate

router = APIRouter()

_FIRMWARE_VERSION = "1.0.0-mock"
_LIBRARY_MODEL = "Scalar i3"
_LIBRARY_TYPE = "i3"


class LibraryResource(BaseModel):
    name: str
    type: str
    firmware: str
    serialNumber: str
    status: str
    roboticsState: str
    drivesOnline: int
    drivesOffline: int
    slotsTotal: int
    slotsOccupied: int
    slotsEmpty: int
    ieSlots: int
    cleaningSlots: int
    partitions: int


class LibraryResponse(BaseModel):
    library: LibraryResource


class PhysicalLibraryResource(BaseModel):
    name: str
    serialNumber: str
    firmware: str
    model: str
    type: str
    status: str
    roboticsState: str
    modules: int
    powerSupplies: int
    fans: int
    temperature: float


class PhysicalLibraryResponse(BaseModel):
    physicalLibrary: PhysicalLibraryResource


class PhysicalLibraryUpdatePayload(BaseModel):
    name: str


class PhysicalLibraryUpdateRequest(BaseModel):
    physicalLibrary: PhysicalLibraryUpdatePayload


class ElementResource(BaseModel):
    type: str
    address: int
    # Full physical coordinate object {frame,rack,section,column,row,type} per the
    # Web Services manual (Figure 23) — no longer a reduced string.
    coordinate: dict[str, int] | None = None
    state: str
    barcode: str | None = None


class ElementListResource(BaseModel):
    element: list[ElementResource]


class ElementListResponse(BaseModel):
    elementList: ElementListResource


class SensorValue(BaseModel):
    value: float
    unit: str
    status: str


class FanStatus(BaseModel):
    id: int
    status: str
    rpm: int


class PowerSupplyStatus(BaseModel):
    id: int
    status: str
    voltage: float


class EnvironmentResource(BaseModel):
    temperature: SensorValue
    humidity: SensorValue
    fans: list[FanStatus]
    powerSupplies: list[PowerSupplyStatus]


class EnvironmentResponse(BaseModel):
    environment: EnvironmentResource


class ModuleResource(BaseModel):
    id: int
    serialNumber: str
    model: str
    status: str
    slots: int
    drives: int


class ModuleListResource(BaseModel):
    module: list[ModuleResource]


class ModuleListResponse(BaseModel):
    moduleList: ModuleListResource


class ModeResource(BaseModel):
    value: str


class ModeResponse(BaseModel):
    mode: ModeResource


class LibraryStatusResource(BaseModel):
    overall: str
    robotics: str
    drives: str
    media: str
    connectivity: str
    power: str


class LibraryStatusResponse(BaseModel):
    libraryStatus: LibraryStatusResource


class Task(BaseModel):
    id: str
    componentId: str
    type: str
    opened: str
    closed: str | None = None
    state: int
    status: str
    description: str
    sessionId: str | None = None


class TaskListResource(BaseModel):
    task: list[Task]


class TaskListResponse(BaseModel):
    taskList: TaskListResource


class TaskResponse(BaseModel):
    task: Task


class PhysicalLibraryEnvironmentEmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipients: list[str] = Field(default_factory=list)
    subject: str | None = None
    reportCriteria: dict[str, Any] | None = None


class PhysicalLibraryConfigurationResource(BaseModel):
    name: str
    serialNumber: str
    firmware: str
    model: str
    type: str
    status: str
    modules: int


class PhysicalLibraryConfigurationResponse(BaseModel):
    physicalLibraryConfiguration: PhysicalLibraryConfigurationResource


class PhysicalLibraryRemoteAccessResource(BaseModel):
    enabled: bool
    mode: str
    allowRemoteAdmin: bool


class PhysicalLibraryRemoteAccessResponse(BaseModel):
    physicalLibraryRemoteAccess: PhysicalLibraryRemoteAccessResource


class PhysicalLibraryResourcesResource(BaseModel):
    slotsTotal: int
    slotsOccupied: int
    slotsEmpty: int
    drivesTotal: int
    drivesOnline: int
    ieSlots: int
    cleaningSlots: int


class PhysicalLibraryResourcesResponse(BaseModel):
    physicalLibraryResources: PhysicalLibraryResourcesResource


class PhysicalLibrarySettingsResource(BaseModel):
    mode: str
    partitions: int
    cleaningSegments: int
    remoteAccessEnabled: bool


class PhysicalLibrarySettingsResponse(BaseModel):
    physicalLibrarySettings: PhysicalLibrarySettingsResource


class SegmentCoordinate(BaseModel):
    frame: int
    rack: int
    section: int
    column: int
    row: int
    type: int


class Segment(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    coordinate: SegmentCoordinate
    size: int
    owner: str = ""
    configuredType: int = 0
    status: str = "available"
    type: str = "storage"


class SegmentListResource(BaseModel):
    segment: list[Segment]


class SegmentListResponse(BaseModel):
    segmentList: SegmentListResource


class SegmentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    coordinate: SegmentCoordinate | None = None
    size: int | None = None
    owner: str | None = None
    configuredType: int | None = None
    status: str | None = None
    type: str | None = None


class SegmentRequest(BaseModel):
    segment: SegmentPatch


class SegmentListPatchResource(BaseModel):
    segment: list[SegmentPatch] = Field(default_factory=list)


class SegmentListRequest(BaseModel):
    segmentList: SegmentListPatchResource


def _ensure_state(context: AppContext) -> None:
    aml_state.ensure_initialized(context.config.db_url)


def _serial_number(context: AppContext) -> str:
    return context.library.inventory().library_id.upper()


def _mode_status() -> str:
    return aml_state.get_library_mode()


def _ie_slot_count() -> int:
    return sum(len(station.get("slots", [])) for station in aml_state.get_aml_ie_stations().values())


def _library_counts(context: AppContext) -> tuple[int, int, int, int, int]:
    inventory = context.library.inventory()
    slots_total = len(inventory.slots)
    slots_occupied = sum(1 for slot in inventory.slots if slot.occupied)
    drives_online = sum(1 for drive in inventory.drives if drive.drive_state != DriveState.FAILED)
    drives_offline = len(inventory.drives) - drives_online
    cleaning_slots = sum(
        1
        for barcode in context.library.get_all_barcodes()
        if context.library.get_cartridge_state(barcode) == CartridgeState.CLEANING
    )
    return slots_total, slots_occupied, drives_online, drives_offline, cleaning_slots


def _build_library_resource(context: AppContext) -> LibraryResource:
    inventory = context.library.inventory()
    slots_total, slots_occupied, drives_online, drives_offline, cleaning_slots = _library_counts(context)
    return LibraryResource(
        name=aml_state.get_library_name(),
        type=_LIBRARY_TYPE,
        firmware=_FIRMWARE_VERSION,
        serialNumber=_serial_number(context),
        status=_mode_status(),
        roboticsState=inventory.changer_state.value,
        drivesOnline=drives_online,
        drivesOffline=drives_offline,
        slotsTotal=slots_total,
        slotsOccupied=slots_occupied,
        slotsEmpty=slots_total - slots_occupied,
        ieSlots=_ie_slot_count(),
        cleaningSlots=cleaning_slots,
        partitions=1,
    )


def _build_physical_library_resource(context: AppContext) -> PhysicalLibraryResource:
    inventory = context.library.inventory()
    modules = max(len(aml_state.get_aml_towers()), 1)
    return PhysicalLibraryResource(
        name=aml_state.get_library_name(),
        serialNumber=_serial_number(context),
        firmware=_FIRMWARE_VERSION,
        model=_LIBRARY_MODEL,
        type=_LIBRARY_TYPE,
        status=_mode_status(),
        roboticsState=inventory.changer_state.value,
        modules=modules,
        powerSupplies=2,
        fans=4,
        temperature=22.0,
    )


def _status_level(*, failed: bool = False, warning: bool = False) -> str:
    if failed:
        return "failed"
    if warning:
        return "warning"
    return "good"


def _validate_library_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="Library name is required")
    return normalized


def _validate_library_mode(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in {"online", "offline"}:
        raise HTTPException(status_code=400, detail="Invalid library mode")
    return normalized


def _validate_identifier(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail=f"{field_name} is required")
    return normalized


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _ws_result(summary: str) -> WSResultCode:
    return WSResultCode(summary=summary)


_STORAGE_ELEMENT_TYPE = 2  # Web Services manual: coordinate type 2 = storage


def _slot_coordinate(slot_id: int) -> dict[str, int]:
    """Full physical element coordinate for a storage slot.

    The real i3 returns a coordinate OBJECT (frame/rack/section/column/row/type),
    not a reduced string (Web Services Guide Rev D, Figure 23). The emulator maps
    its tower/bay geometry onto that shape: section = bay/module, row = slot within
    the module. See docs/reference/i3-contract-notes.md.
    """
    remaining = slot_id
    towers = sorted(
        aml_state.get_aml_towers().values(),
        key=lambda item: int(item.get("bay", 1)),
    )
    for tower in towers:
        bay = int(tower.get("bay", 1))
        slots = int(tower.get("slots", 0))
        if remaining <= slots:
            return ScalarCoordinate(
                frame=1, rack=1, section=bay, column=1, row=remaining,
                element_type=_STORAGE_ELEMENT_TYPE,
            ).to_dict()
        remaining -= slots
    return ScalarCoordinate(
        frame=1, rack=1, section=1, column=1, row=slot_id, element_type=_STORAGE_ELEMENT_TYPE
    ).to_dict()


def _serialize_task(item: dict[str, Any]) -> Task:
    return Task.model_validate(item)


def _serialize_segment(item: dict[str, Any]) -> Segment:
    return Segment.model_validate(item)


def _library_task_status(state: int) -> str:
    return {
        0: "Pending",
        1: "Running",
        2: "Cancelled",
        3: "Failed",
        4: "Unknown",
        5: "Completed",
    }.get(state, "Unknown")


def _create_library_task(
    *,
    context: AppContext,
    task_type: str,
    description: str,
    state: int = 5,
    session_id: str | None = None,
) -> dict[str, Any]:
    opened = _timestamp()
    task_id = f"task-{uuid4().hex[:12]}"
    return aml_state.set_aml_library_operation_task(
        task_id,
        {
            "id": task_id,
            "componentId": _serial_number(context),
            "type": task_type,
            "opened": opened,
            "closed": opened if state == 5 else None,
            "state": state,
            "status": _library_task_status(state),
            "description": description,
            "sessionId": session_id,
        },
    )


def _list_library_tasks(context: AppContext, *, task_type: str | None = None) -> list[Task]:
    tasks = aml_state.list_aml_library_operation_tasks(component_id=_serial_number(context), task_type=task_type)
    return [_serialize_task(task) for task in tasks]


def _get_library_task_or_404(context: AppContext, task_type: str, task_id: str) -> dict[str, Any]:
    task = aml_state.get_aml_library_operation_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if str(task.get("componentId")) != _serial_number(context) or str(task.get("type")) != task_type:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


def _delete_library_task_or_404(context: AppContext, task_type: str, task_id: str) -> dict[str, Any]:
    task = _get_library_task_or_404(context, task_type, task_id)
    deleted = aml_state.delete_aml_library_operation_task(str(task.get("id")))
    if deleted is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return deleted


def _segment_coordinate_key(coordinate: SegmentCoordinate) -> tuple[int, int, int, int, int, int]:
    return (
        coordinate.frame,
        coordinate.rack,
        coordinate.section,
        coordinate.column,
        coordinate.row,
        coordinate.type,
    )


def _segment_from_patch_or_404(segments: list[dict[str, Any]], patch: SegmentPatch) -> tuple[int, dict[str, Any]]:
    if patch.id:
        for idx, segment in enumerate(segments):
            if str(segment.get("id")) == patch.id:
                return idx, segment
    if patch.coordinate is not None:
        expected = _segment_coordinate_key(patch.coordinate)
        for idx, segment in enumerate(segments):
            coordinate = SegmentCoordinate.model_validate(segment.get("coordinate", {}))
            if _segment_coordinate_key(coordinate) == expected:
                return idx, segment
    raise HTTPException(status_code=404, detail="Segment not found")


def _filtered_segments(
    *,
    partition: str | None,
    status: str | None,
    type_filter: str | None,
    start: int,
    length: int,
    frame: int | None,
    rack: int | None,
) -> list[dict[str, Any]]:
    segments = aml_state.list_aml_physical_segments()
    if partition:
        segments = [item for item in segments if str(item.get("owner", "")) == partition]
    if status:
        normalized_status = status.strip().lower()
        segments = [item for item in segments if str(item.get("status", "")).strip().lower() == normalized_status]
    if type_filter:
        normalized_type = type_filter.strip().lower()
        segments = [item for item in segments if str(item.get("type", "")).strip().lower() == normalized_type]
    if frame is not None:
        segments = [item for item in segments if int(item.get("coordinate", {}).get("frame", -1)) == frame]
    if rack is not None:
        segments = [item for item in segments if int(item.get("coordinate", {}).get("rack", -1)) == rack]
    start_index = max(start, 0)
    if length < 0:
        return segments[start_index:]
    return segments[start_index : start_index + length]


@router.get("/", response_model=LibraryResponse)
async def get_library_root(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> LibraryResponse:
    _ensure_state(context)
    return LibraryResponse(library=_build_library_resource(context))


@router.get("/physicalLibrary", response_model=PhysicalLibraryResponse)
async def get_physical_library(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PhysicalLibraryResponse:
    _ensure_state(context)
    return PhysicalLibraryResponse(physicalLibrary=_build_physical_library_resource(context))


@router.put("/physicalLibrary", response_model=PhysicalLibraryResponse)
async def put_physical_library(
    payload: PhysicalLibraryUpdateRequest,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PhysicalLibraryResponse:
    _ensure_state(context)
    aml_state.set_library_name(_validate_library_name(payload.physicalLibrary.name))
    return PhysicalLibraryResponse(physicalLibrary=_build_physical_library_resource(context))


@router.get("/physicalLibrary/elements", response_model=ElementListResponse)
async def get_library_elements(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> ElementListResponse:
    _ensure_state(context)
    inventory = context.library.inventory()
    elements = [
        ElementResource(
            type="slot",
            address=slot.slot_id,
            coordinate=_slot_coordinate(slot.slot_id),
            state="occupied" if slot.occupied else "empty",
            barcode=str(slot.barcode) if slot.barcode else None,
        )
        for slot in inventory.slots
    ]
    elements.extend(
        ElementResource(
            type="drive",
            address=drive.drive_id,
            state=drive.drive_state.value,
            barcode=str(drive.barcode) if drive.barcode else None,
        )
        for drive in inventory.drives
    )
    elements.extend(
        ElementResource(type="ieSlot", address=index, state="empty", barcode=None)
        for index in range(1, _ie_slot_count() + 1)
    )
    return ElementListResponse(elementList=ElementListResource(element=elements))


@router.get("/physicalLibrary/environment", response_model=EnvironmentResponse)
async def get_library_environment(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> EnvironmentResponse:
    _ensure_state(context)
    return EnvironmentResponse(
        environment=EnvironmentResource(
            temperature=SensorValue(value=22.0, unit="C", status="good"),
            humidity=SensorValue(value=45.0, unit="%", status="good"),
            fans=[
                FanStatus(id=1, status="good", rpm=4200),
                FanStatus(id=2, status="good", rpm=4150),
                FanStatus(id=3, status="good", rpm=4180),
                FanStatus(id=4, status="good", rpm=4210),
            ],
            powerSupplies=[
                PowerSupplyStatus(id=1, status="good", voltage=230.0),
                PowerSupplyStatus(id=2, status="good", voltage=230.0),
            ],
        )
    )


@router.post("/physicalLibrary/environment/email", response_model=WSResultCode)
async def email_library_environment(
    _: PhysicalLibraryEnvironmentEmailRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    return _ws_result("Environment report email queued")


@router.get("/physicalLibrary/i3-i6/modules", response_model=ModuleListResponse)
async def get_library_modules(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> ModuleListResponse:
    _ensure_state(context)
    inventory = context.library.inventory()
    towers = aml_state.get_aml_towers()
    if not towers:
        module = ModuleResource(
            id=1,
            serialNumber=f"{_serial_number(context)}-M1",
            model=_LIBRARY_MODEL,
            status="good" if inventory.changer_state != ChangerState.ERROR else "failed",
            slots=len(inventory.slots),
            drives=len(inventory.drives),
        )
        return ModuleListResponse(moduleList=ModuleListResource(module=[module]))
    modules = [
        ModuleResource(
            id=index,
            serialNumber=f"{_serial_number(context)}-M{index}",
            model=str(tower.get("model", _LIBRARY_MODEL)),
            status="good" if str(tower.get("status", "online")).lower() == "online" else "failed",
            slots=int(tower.get("slots", 0)),
            drives=len(tower.get("drives", [])),
        )
        for index, tower in enumerate(sorted(towers.values(), key=lambda item: str(item.get("id", ""))), start=1)
    ]
    return ModuleListResponse(moduleList=ModuleListResource(module=modules))


@router.get("/physicalLibrary/quattro/modules", response_model=ModuleListResponse)
async def get_library_quattro_modules(
    user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> ModuleListResponse:
    return await get_library_modules(user, context)


@router.get("/physicalLibrary/mode", response_model=ModeResponse)
async def get_library_mode(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> ModeResponse:
    _ensure_state(context)
    return ModeResponse(mode=ModeResource(value=aml_state.get_library_mode()))


@router.put("/physicalLibrary/mode", response_model=ModeResponse)
async def put_library_mode(
    payload: ModeResponse,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> ModeResponse:
    _ensure_state(context)
    return ModeResponse(mode=ModeResource(value=aml_state.set_library_mode(_validate_library_mode(payload.mode.value))))


@router.get("/physicalLibrary/operations", response_model=TaskListResponse)
async def list_physical_library_operations(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> TaskListResponse:
    _ensure_state(context)
    tasks = [task for task in _list_library_tasks(context) if task.type in {"inventory", "shutdown", "reboot", "reset", "teach"}]
    return TaskListResponse(taskList=TaskListResource(task=tasks))


@router.get("/physicalLibrary/operations/inventory", response_model=TaskListResponse)
async def list_physical_library_inventory_tasks(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> TaskListResponse:
    _ensure_state(context)
    return TaskListResponse(taskList=TaskListResource(task=_list_library_tasks(context, task_type="inventory")))


@router.post("/physicalLibrary/operations/inventory", response_model=WSResultCode)
async def start_physical_library_inventory(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    started = _timestamp()
    elements_total = max(len(aml_state.list_aml_media()) + len(aml_state.list_aml_drives()), 1)
    aml_state.set_aml_inventory_status(
        {
            "state": "completed",
            "startTime": started,
            "completedTime": started,
            "progress": 100,
            "elementsScanned": elements_total,
            "elementsTotal": elements_total,
        }
    )
    _create_library_task(context=context, task_type="inventory", description="Physical library inventory completed")
    return _ws_result("Inventory completed")


@router.get("/physicalLibrary/operations/inventory/{id}", response_model=TaskResponse)
async def get_physical_library_inventory_task(
    id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> TaskResponse:
    _ensure_state(context)
    task_id = _validate_identifier(id, field_name="id")
    return TaskResponse(task=_serialize_task(_get_library_task_or_404(context, "inventory", task_id)))


@router.delete("/physicalLibrary/operations/inventory/{id}", response_model=WSResultCode)
async def delete_physical_library_inventory_task(
    id: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    task_id = _validate_identifier(id, field_name="id")
    _delete_library_task_or_404(context, "inventory", task_id)
    return _ws_result(f"Deleted inventory task {task_id}")


@router.get("/physicalLibrary/operations/shutdown", response_model=TaskListResponse)
async def list_physical_library_shutdown_tasks(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> TaskListResponse:
    _ensure_state(context)
    return TaskListResponse(taskList=TaskListResource(task=_list_library_tasks(context, task_type="shutdown")))


@router.post("/physicalLibrary/operations/shutdown", response_model=WSResultCode)
async def start_physical_library_shutdown(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    _create_library_task(context=context, task_type="shutdown", description="Physical library shutdown requested")
    return _ws_result("Shutdown requested")


@router.get("/physicalLibrary/operations/shutdown/{id}", response_model=TaskResponse)
async def get_physical_library_shutdown_task(
    id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> TaskResponse:
    _ensure_state(context)
    task_id = _validate_identifier(id, field_name="id")
    return TaskResponse(task=_serialize_task(_get_library_task_or_404(context, "shutdown", task_id)))


@router.delete("/physicalLibrary/operations/shutdown/{id}", response_model=WSResultCode)
async def delete_physical_library_shutdown_task(
    id: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    task_id = _validate_identifier(id, field_name="id")
    _delete_library_task_or_404(context, "shutdown", task_id)
    return _ws_result(f"Deleted shutdown task {task_id}")


@router.get("/physicalLibrary/operations/reboot", response_model=TaskListResponse)
async def list_physical_library_reboot_tasks(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> TaskListResponse:
    _ensure_state(context)
    return TaskListResponse(taskList=TaskListResource(task=_list_library_tasks(context, task_type="reboot")))


@router.post("/physicalLibrary/operations/reboot", response_model=WSResultCode)
async def start_physical_library_reboot(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    _create_library_task(context=context, task_type="reboot", description="Physical library reboot requested")
    return _ws_result("Reboot requested")


@router.get("/physicalLibrary/operations/reboot/{id}", response_model=TaskResponse)
async def get_physical_library_reboot_task(
    id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> TaskResponse:
    _ensure_state(context)
    task_id = _validate_identifier(id, field_name="id")
    return TaskResponse(task=_serialize_task(_get_library_task_or_404(context, "reboot", task_id)))


@router.delete("/physicalLibrary/operations/reboot/{id}", response_model=WSResultCode)
async def delete_physical_library_reboot_task(
    id: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    task_id = _validate_identifier(id, field_name="id")
    _delete_library_task_or_404(context, "reboot", task_id)
    return _ws_result(f"Deleted reboot task {task_id}")


@router.get("/physicalLibrary/operations/reset", response_model=TaskListResponse)
async def list_physical_library_reset_tasks(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> TaskListResponse:
    _ensure_state(context)
    return TaskListResponse(taskList=TaskListResource(task=_list_library_tasks(context, task_type="reset")))


@router.post("/physicalLibrary/operations/reset", response_model=WSResultCode)
async def start_physical_library_reset(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    _create_library_task(context=context, task_type="reset", description="Physical library reset requested")
    return _ws_result("Reset requested")


@router.get("/physicalLibrary/operations/reset/{id}", response_model=TaskResponse)
async def get_physical_library_reset_task(
    id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> TaskResponse:
    _ensure_state(context)
    task_id = _validate_identifier(id, field_name="id")
    return TaskResponse(task=_serialize_task(_get_library_task_or_404(context, "reset", task_id)))


@router.delete("/physicalLibrary/operations/reset/{id}", response_model=WSResultCode)
async def delete_physical_library_reset_task(
    id: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    task_id = _validate_identifier(id, field_name="id")
    _delete_library_task_or_404(context, "reset", task_id)
    return _ws_result(f"Deleted reset task {task_id}")


@router.get("/physicalLibrary/operations/teach", response_model=TaskListResponse)
async def list_physical_library_teach_tasks(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> TaskListResponse:
    _ensure_state(context)
    return TaskListResponse(taskList=TaskListResource(task=_list_library_tasks(context, task_type="teach")))


@router.post("/physicalLibrary/operations/teach", response_model=WSResultCode)
async def start_physical_library_teach(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    _create_library_task(context=context, task_type="teach", description="Physical library teach requested")
    return _ws_result("Teach requested")


@router.get("/physicalLibrary/operations/teach/{id}", response_model=TaskResponse)
async def get_physical_library_teach_task(
    id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> TaskResponse:
    _ensure_state(context)
    task_id = _validate_identifier(id, field_name="id")
    return TaskResponse(task=_serialize_task(_get_library_task_or_404(context, "teach", task_id)))


@router.delete("/physicalLibrary/operations/teach/{id}", response_model=WSResultCode)
async def delete_physical_library_teach_task(
    id: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    task_id = _validate_identifier(id, field_name="id")
    _delete_library_task_or_404(context, "teach", task_id)
    return _ws_result(f"Deleted teach task {task_id}")


@router.get("/physicalLibrary/subset/configuration", response_model=PhysicalLibraryConfigurationResponse)
async def get_physical_library_subset_configuration(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PhysicalLibraryConfigurationResponse:
    _ensure_state(context)
    physical = _build_physical_library_resource(context)
    return PhysicalLibraryConfigurationResponse(
        physicalLibraryConfiguration=PhysicalLibraryConfigurationResource(
            name=physical.name,
            serialNumber=physical.serialNumber,
            firmware=physical.firmware,
            model=physical.model,
            type=physical.type,
            status=physical.status,
            modules=physical.modules,
        )
    )


@router.get("/physicalLibrary/subset/remoteAccess", response_model=PhysicalLibraryRemoteAccessResponse)
async def get_physical_library_subset_remote_access(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PhysicalLibraryRemoteAccessResponse:
    _ensure_state(context)
    return PhysicalLibraryRemoteAccessResponse(
        physicalLibraryRemoteAccess=PhysicalLibraryRemoteAccessResource(
            enabled=aml_state.get_library_mode() == "online",
            mode="https-only",
            allowRemoteAdmin=True,
        )
    )


@router.get("/physicalLibrary/subset/resources", response_model=PhysicalLibraryResourcesResponse)
async def get_physical_library_subset_resources(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PhysicalLibraryResourcesResponse:
    _ensure_state(context)
    slots_total, slots_occupied, drives_online, _, cleaning_slots = _library_counts(context)
    return PhysicalLibraryResourcesResponse(
        physicalLibraryResources=PhysicalLibraryResourcesResource(
            slotsTotal=slots_total,
            slotsOccupied=slots_occupied,
            slotsEmpty=slots_total - slots_occupied,
            drivesTotal=len(context.library.inventory().drives),
            drivesOnline=drives_online,
            ieSlots=_ie_slot_count(),
            cleaningSlots=cleaning_slots,
        )
    )


@router.get("/physicalLibrary/subset/settings", response_model=PhysicalLibrarySettingsResponse)
async def get_physical_library_subset_settings(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PhysicalLibrarySettingsResponse:
    _ensure_state(context)
    segments = aml_state.list_aml_physical_segments()
    cleaning_segments = sum(1 for item in segments if int(item.get("configuredType", 0)) == 2)
    return PhysicalLibrarySettingsResponse(
        physicalLibrarySettings=PhysicalLibrarySettingsResource(
            mode=aml_state.get_library_mode(),
            partitions=1,
            cleaningSegments=cleaning_segments,
            remoteAccessEnabled=True,
        )
    )


@router.get("/physicalLibrary/segments", response_model=SegmentListResponse)
async def get_physical_library_segments(
    partition: str | None = Query(default=None),
    status: str | None = Query(default=None),
    type_filter: str | None = Query(default=None, alias="type"),
    start: int = Query(default=0),
    length: int = Query(default=-1),
    frame: int | None = Query(default=None),
    rack: int | None = Query(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> SegmentListResponse:
    _ensure_state(context)
    segments = _filtered_segments(
        partition=partition,
        status=status,
        type_filter=type_filter,
        start=start,
        length=length,
        frame=frame,
        rack=rack,
    )
    return SegmentListResponse(segmentList=SegmentListResource(segment=[_serialize_segment(item) for item in segments]))


@router.get("/physicalLibrary/segments/amp", response_model=SegmentListResponse)
async def get_physical_library_amp_segments(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> SegmentListResponse:
    _ensure_state(context)
    segments = [item for item in aml_state.list_aml_physical_segments() if int(item.get("configuredType", 0)) == 1]
    return SegmentListResponse(segmentList=SegmentListResource(segment=[_serialize_segment(item) for item in segments]))


@router.put("/physicalLibrary/segments/amp", response_model=WSResultCode)
async def put_physical_library_amp_segments(
    payload: SegmentListRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    segments = aml_state.list_aml_physical_segments()
    for candidate in payload.segmentList.segment:
        idx, current = _segment_from_patch_or_404(segments, candidate)
        current["configuredType"] = 1
        current["type"] = "storage"
        if candidate.owner is not None:
            current["owner"] = candidate.owner
        current["status"] = "used"
        segments[idx] = current
    aml_state.set_aml_physical_segments(segments)
    return _ws_result("AMP segment reassignment completed")


@router.get("/physicalLibrary/segments/cleaning", response_model=SegmentListResponse)
async def get_physical_library_cleaning_segments(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> SegmentListResponse:
    _ensure_state(context)
    segments = [item for item in aml_state.list_aml_physical_segments() if int(item.get("configuredType", 0)) == 2]
    return SegmentListResponse(segmentList=SegmentListResource(segment=[_serialize_segment(item) for item in segments]))


@router.post("/physicalLibrary/segments/cleaning", response_model=WSResultCode)
async def create_physical_library_cleaning_segment(
    payload: SegmentRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    segments = aml_state.list_aml_physical_segments()
    idx, current = _segment_from_patch_or_404(segments, payload.segment)
    current["configuredType"] = 2
    current["type"] = "cleaning"
    current["status"] = "used"
    if payload.segment.owner is not None:
        current["owner"] = payload.segment.owner
    segments[idx] = current
    aml_state.set_aml_physical_segments(segments)
    return _ws_result("Cleaning segment created")


@router.delete("/physicalLibrary/segments/cleaning", response_model=WSResultCode)
async def delete_physical_library_cleaning_segment(
    payload: SegmentRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    segments = aml_state.list_aml_physical_segments()
    idx, current = _segment_from_patch_or_404(segments, payload.segment)
    current["configuredType"] = 0
    current["type"] = "storage"
    current["status"] = "available"
    current["owner"] = ""
    segments[idx] = current
    aml_state.set_aml_physical_segments(segments)
    return _ws_result("Cleaning segment deleted")


@router.post("/physicalLibrary/segments/operations/inventory", response_model=WSResultCode)
async def start_physical_library_segment_inventory(
    payload: SegmentListRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    segment_count = len(payload.segmentList.segment)
    _create_library_task(
        context=context,
        task_type="segment-inventory",
        description=f"Segment inventory requested for {segment_count} segment(s)",
    )
    return _ws_result("Segment inventory completed")


@router.get("/physicalLibrary/status", response_model=LibraryStatusResponse)
async def get_library_status(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> LibraryStatusResponse:
    _ensure_state(context)
    inventory = context.library.inventory()
    cartridge_states = [context.library.get_cartridge_state(barcode) for barcode in context.library.get_all_barcodes()]
    robotics = _status_level(
        failed=inventory.changer_state == ChangerState.ERROR,
        warning=inventory.changer_state == ChangerState.MOVING,
    )
    failed_drives = sum(1 for drive in inventory.drives if drive.drive_state == DriveState.FAILED)
    drives = _status_level(
        failed=failed_drives == len(inventory.drives) and len(inventory.drives) > 0,
        warning=0 < failed_drives < len(inventory.drives),
    )
    media = _status_level(
        failed=all(state in {CartridgeState.MISSING, CartridgeState.EXPORTED} for state in cartridge_states)
        if cartridge_states
        else False,
        warning=any(state in {CartridgeState.MISSING, CartridgeState.CLEANING} for state in cartridge_states),
    )
    connectivity = _status_level(warning=aml_state.get_library_mode() == "offline")
    power = _status_level()
    overall = _status_level(
        failed=any(level == "failed" for level in (robotics, drives, media, connectivity, power)),
        warning=any(level == "warning" for level in (robotics, drives, media, connectivity, power)),
    )
    return LibraryStatusResponse(
        libraryStatus=LibraryStatusResource(
            overall=overall,
            robotics=robotics,
            drives=drives,
            media=media,
            connectivity=connectivity,
            power=power,
        )
    )


# ---------------------------------------------------------------------------
# Backwards-compatible aliases for tests/UI that expect /aml/library paths
# ---------------------------------------------------------------------------

@router.get("/library", response_model=LibraryResponse)
async def get_library_alias(
    user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> LibraryResponse:
    return await get_library_root(user, context)


@router.get("/library/physical", response_model=PhysicalLibraryResponse)
async def get_library_physical_alias(
    user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PhysicalLibraryResponse:
    return await get_physical_library(user, context)


@router.get("/library/inventory")
async def get_library_inventory_alias(
    user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict:
    """Backwards-compatible inventory response shape expected by UI/tests."""
    _ensure_state(context)
    inventory = context.library.inventory()
    return {
        "library_id": inventory.library_id,
        "changer_state": inventory.changer_state.value,
        "slots": [
            {
                "slotId": slot.slot_id,
                "id": slot.slot_id,
                "occupied": slot.occupied,
                "barcode": str(slot.barcode) if slot.barcode else None,
            }
            for slot in inventory.slots
        ],
        "drives": [
            {
                "driveId": drive.drive_id,
                "id": drive.drive_id,
                "loaded": bool(drive.barcode),
                "barcode": str(drive.barcode) if drive.barcode else None,
                "drive_state": drive.drive_state.value,
                "mount_state": drive.mount_state.value,
            }
            for drive in inventory.drives
        ],
    }
