"""AML diagnostics, drive task, and physical-library routes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from openblade.api import aml_state
from openblade.api.routes_aml_auth import WSResultCode, _ensure_state, _require_admin, require_auth
from openblade.bootstrap import AppContext, get_context
from openblade.catalog.models import AmlUser

router = APIRouter()


class DriveCleaningRecord(BaseModel):
    serialNumber: str
    lastCleaned: str | None = None
    mediaBarcode: str
    useCount: int
    expired: bool


class DriveCleaningListResource(BaseModel):
    driveCleaning: list[DriveCleaningRecord]


class DriveCleaningListResponse(BaseModel):
    driveCleaningList: DriveCleaningListResource


class CleaningReportEmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipients: list[str] = Field(default_factory=list)
    subject: str | None = None


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


class PhysicalLibraryElement(BaseModel):
    address: str
    elementType: str
    status: str
    barcode: str | None = None
    full: bool


class PhysicalLibraryElementListResource(BaseModel):
    element: list[PhysicalLibraryElement]


class PhysicalLibraryElementListResponse(BaseModel):
    elementList: PhysicalLibraryElementListResource


class PhysicalLibraryElementResponse(BaseModel):
    element: PhysicalLibraryElement


class RobotState(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    serialNumber: str
    model: str
    status: str
    state: str
    location: str
    homeSlot: str


class RobotStateListResource(BaseModel):
    robot: list[RobotState]


class RobotStateListResponse(BaseModel):
    robotList: RobotStateListResource


class RobotStateResponse(BaseModel):
    robot: RobotState


class TowerInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    serialNumber: str
    model: str
    status: str
    slots: int
    occupiedSlots: int
    drives: list[str] = Field(default_factory=list)


class TowerInfoListResource(BaseModel):
    tower: list[TowerInfo]


class TowerInfoListResponse(BaseModel):
    towerList: TowerInfoListResource


class TowerInfoResponse(BaseModel):
    tower: TowerInfo


class MagazineInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    location: str
    status: str
    slotCount: int
    occupiedSlots: int
    tapes: list[str] = Field(default_factory=list)


class MagazineInfoListResource(BaseModel):
    magazine: list[MagazineInfo]


class MagazineInfoListResponse(BaseModel):
    magazineList: MagazineInfoListResource


class MagazineInfoResponse(BaseModel):
    magazine: MagazineInfo


class DiagnosticTest(BaseModel):
    id: str
    name: str
    description: str
    category: str
    estimatedDuration: int


class DiagnosticTestListResource(BaseModel):
    diagnosticTest: list[DiagnosticTest]


class DiagnosticTestListResponse(BaseModel):
    diagnosticTestList: DiagnosticTestListResource


class DiagnosticRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    testIds: list[str] = Field(default_factory=list)
    suiteName: str | None = None


class DiagnosticResultDetail(BaseModel):
    name: str
    status: str
    message: str


class DiagnosticResult(BaseModel):
    id: str
    testId: str
    startTime: str
    endTime: str
    status: str
    passed: int
    failed: int
    details: list[DiagnosticResultDetail] = Field(default_factory=list)


class DiagnosticResultResponse(BaseModel):
    diagnosticResult: DiagnosticResult


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_identifier(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail=f"{field_name} is required")
    return normalized


def _ws_result(summary: str) -> WSResultCode:
    return WSResultCode(summary=summary)


def _serialize_task(item: dict[str, Any]) -> Task:
    return Task.model_validate(item)


def _serialize_robot(item: dict[str, Any]) -> RobotState:
    return RobotState.model_validate(item)


def _serialize_tower(item: dict[str, Any]) -> TowerInfo:
    return TowerInfo.model_validate(item)


def _serialize_magazine(item: dict[str, Any]) -> MagazineInfo:
    return MagazineInfo.model_validate(item)


def _serialize_diagnostic_test(item: dict[str, Any]) -> DiagnosticTest:
    return DiagnosticTest.model_validate(item)


def _serialize_diagnostic_result(item: dict[str, Any]) -> DiagnosticResult:
    return DiagnosticResult.model_validate(item)


def _get_drive_or_404(serial_number: str) -> dict[str, Any]:
    drive = aml_state.get_aml_drive(serial_number)
    if drive is None:
        raise HTTPException(status_code=404, detail="Drive not found")
    return drive


def _get_drive_task_or_404(serial_number: str, task_type: str, task_id: str) -> dict[str, Any]:
    task = aml_state.get_aml_drive_operation_task(task_id)
    if task is None or task.get("componentId") != serial_number or task.get("type") != task_type:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


def _cleaning_media_barcode() -> str:
    for media in aml_state.list_aml_media():
        if "CLN" in str(media.get("type", "")) or "CLN" in str(media.get("barcode", "")):
            return str(media.get("barcode"))
    return "CLN000L9"


def _next_cleaning_use_count(media_barcode: str) -> int:
    reports = aml_state.list_aml_drive_cleaning_reports()
    matching = [int(report.get("useCount", 0)) for report in reports if report.get("mediaBarcode") == media_barcode]
    return (max(matching) if matching else 0) + 1


def _create_drive_task(
    *,
    serial_number: str,
    task_type: str,
    description: str,
    state: int = 5,
    status: str | None = None,
    opened: str | None = None,
    closed: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    opened_at = opened or _timestamp()
    closed_at = closed if closed is not None else opened_at if state == 5 else None
    task_id = f"task-{uuid4().hex[:12]}"
    resolved_status = status or {0: "Pending", 1: "Running", 2: "Cancelled", 3: "Failed", 4: "Unknown", 5: "Completed"}.get(state, "Unknown")
    return aml_state.set_aml_drive_operation_task(
        task_id,
        {
            "id": task_id,
            "componentId": serial_number,
            "type": task_type,
            "opened": opened_at,
            "closed": closed_at,
            "state": state,
            "status": resolved_status,
            "description": description,
            "sessionId": session_id,
        },
    )


def _build_physical_library_elements() -> list[PhysicalLibraryElement]:
    elements: list[PhysicalLibraryElement] = []
    occupied_slots: dict[str, str] = {}
    for media in aml_state.list_aml_media():
        slot_address = str(media.get("slotAddress", "")).strip()
        if slot_address and "," in slot_address:
            occupied_slots[slot_address] = str(media.get("barcode"))

    represented_slots: set[str] = set()
    for tower in aml_state.get_aml_towers().values():
        for index in range(1, int(tower.get("slots", 0)) + 1):
            address = f"1,1,{index}"
            barcode = occupied_slots.get(address)
            represented_slots.add(address)
            elements.append(
                PhysicalLibraryElement(
                    address=address,
                    elementType="slot",
                    status="occupied" if barcode else "empty",
                    barcode=barcode,
                    full=barcode is not None,
                )
            )

    for address, barcode in sorted(occupied_slots.items()):
        if address in represented_slots:
            continue
        elements.append(
            PhysicalLibraryElement(
                address=address,
                elementType="slot",
                status="occupied",
                barcode=barcode,
                full=True,
            )
        )

    for drive in aml_state.list_aml_drives():
        loaded_media = drive.get("loadedMedia") or {}
        barcode = loaded_media.get("barcode") if isinstance(loaded_media, dict) else None
        elements.append(
            PhysicalLibraryElement(
                address=f"drive:{drive['serialNumber']}",
                elementType="drive",
                status=str(drive.get("state") or drive.get("status") or "idle"),
                barcode=None if barcode is None else str(barcode),
                full=barcode is not None,
            )
        )

    for station in aml_state.get_aml_ie_stations().values():
        slots = station.get("slots", [])
        station_barcode = next((slot.get("barcode") for slot in slots if isinstance(slot, dict) and slot.get("barcode")), None)
        elements.append(
            PhysicalLibraryElement(
                address=f"ie:{station['id']}",
                elementType="ieStation",
                status=str(station.get("state") or station.get("status") or "closed"),
                barcode=None if station_barcode is None else str(station_barcode),
                full=station_barcode is not None,
            )
        )

    return sorted(elements, key=lambda item: (item.elementType, item.address))


def _get_physical_library_element_or_404(address: str) -> PhysicalLibraryElement:
    normalized = _validate_identifier(address, field_name="address")
    for element in _build_physical_library_elements():
        if element.address == normalized:
            return element
    raise HTTPException(status_code=404, detail="Element not found")


@router.get("/drives/reports/cleaning", response_model=DriveCleaningListResponse)
async def list_drive_cleaning_reports(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> DriveCleaningListResponse:
    _ensure_state(context)
    reports = [DriveCleaningRecord.model_validate(item) for item in aml_state.list_aml_drive_cleaning_reports()]
    return DriveCleaningListResponse(driveCleaningList=DriveCleaningListResource(driveCleaning=reports))


@router.post("/drives/reports/cleaning/email", response_model=WSResultCode)
async def email_drive_cleaning_reports(
    _: CleaningReportEmailRequest = Body(default_factory=CleaningReportEmailRequest),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    return _ws_result("Cleaning report email queued")


@router.get("/drive/{serialNumber}/operations/clean", response_model=TaskListResponse)
async def list_drive_clean_tasks(
    serialNumber: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> TaskListResponse:
    _ensure_state(context)
    serial_number = _validate_identifier(serialNumber, field_name="serialNumber")
    _get_drive_or_404(serial_number)
    tasks = [
        _serialize_task(item)
        for item in aml_state.list_aml_drive_operation_tasks(task_type="clean", component_id=serial_number)
    ]
    return TaskListResponse(taskList=TaskListResource(task=tasks))


@router.post("/drive/{serialNumber}/operations/clean", response_model=WSResultCode)
async def start_drive_cleaning(
    serialNumber: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    serial_number = _validate_identifier(serialNumber, field_name="serialNumber")
    drive = _get_drive_or_404(serial_number)
    cleaned_at = _timestamp()
    media_barcode = _cleaning_media_barcode()
    aml_state.update_aml_drive(
        serial_number,
        {
            "state": "idle",
            "cleaningCount": int(drive.get("cleaningCount", 0)) + 1,
            "lastCleaned": cleaned_at,
        },
    )
    aml_state.set_aml_cleaning_status(
        {"state": "completed", "startTime": cleaned_at, "completedTime": cleaned_at, "drives": [serial_number]}
    )
    aml_state.append_aml_drive_cleaning_report(
        {
            "serialNumber": serial_number,
            "lastCleaned": cleaned_at,
            "mediaBarcode": media_barcode,
            "useCount": _next_cleaning_use_count(media_barcode),
            "expired": False,
        }
    )
    _create_drive_task(
        serial_number=serial_number,
        task_type="clean",
        description=f"Cleaned drive {serial_number} using {media_barcode}",
        session_id=f"clean-{uuid4().hex[:8]}",
        opened=cleaned_at,
        closed=cleaned_at,
    )
    return _ws_result(f"Drive {serial_number} cleaning completed")


@router.get("/drive/{serialNumber}/operations/clean/{id}", response_model=TaskResponse)
async def get_drive_clean_task(
    serialNumber: str,
    id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> TaskResponse:
    _ensure_state(context)
    serial_number = _validate_identifier(serialNumber, field_name="serialNumber")
    task_id = _validate_identifier(id, field_name="id")
    _get_drive_or_404(serial_number)
    return TaskResponse(task=_serialize_task(_get_drive_task_or_404(serial_number, "clean", task_id)))


@router.get("/drive/{serialNumber}/operations/load", response_model=TaskListResponse)
async def list_drive_load_tasks(
    serialNumber: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> TaskListResponse:
    _ensure_state(context)
    serial_number = _validate_identifier(serialNumber, field_name="serialNumber")
    _get_drive_or_404(serial_number)
    tasks = [
        _serialize_task(item)
        for item in aml_state.list_aml_drive_operation_tasks(task_type="load", component_id=serial_number)
    ]
    return TaskListResponse(taskList=TaskListResource(task=tasks))


@router.get("/drive/{serialNumber}/operations/load/{id}", response_model=TaskResponse)
async def get_drive_load_task(
    serialNumber: str,
    id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> TaskResponse:
    _ensure_state(context)
    serial_number = _validate_identifier(serialNumber, field_name="serialNumber")
    task_id = _validate_identifier(id, field_name="id")
    _get_drive_or_404(serial_number)
    return TaskResponse(task=_serialize_task(_get_drive_task_or_404(serial_number, "load", task_id)))


@router.get("/drive/{serialNumber}/operations/unload", response_model=TaskListResponse)
async def list_drive_unload_tasks(
    serialNumber: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> TaskListResponse:
    _ensure_state(context)
    serial_number = _validate_identifier(serialNumber, field_name="serialNumber")
    _get_drive_or_404(serial_number)
    tasks = [
        _serialize_task(item)
        for item in aml_state.list_aml_drive_operation_tasks(task_type="unload", component_id=serial_number)
    ]
    return TaskListResponse(taskList=TaskListResource(task=tasks))


@router.get("/drive/{serialNumber}/operations/unload/{id}", response_model=TaskResponse)
async def get_drive_unload_task(
    serialNumber: str,
    id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> TaskResponse:
    _ensure_state(context)
    serial_number = _validate_identifier(serialNumber, field_name="serialNumber")
    task_id = _validate_identifier(id, field_name="id")
    _get_drive_or_404(serial_number)
    return TaskResponse(task=_serialize_task(_get_drive_task_or_404(serial_number, "unload", task_id)))


@router.get("/physicalLibrary/elements", response_model=PhysicalLibraryElementListResponse)
async def list_physical_library_elements(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PhysicalLibraryElementListResponse:
    _ensure_state(context)
    return PhysicalLibraryElementListResponse(elementList=PhysicalLibraryElementListResource(element=_build_physical_library_elements()))


@router.get("/physicalLibrary/elements/{address}", response_model=PhysicalLibraryElementResponse)
async def get_physical_library_element(
    address: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PhysicalLibraryElementResponse:
    _ensure_state(context)
    return PhysicalLibraryElementResponse(element=_get_physical_library_element_or_404(address))


@router.get("/physicalLibrary/robotics", response_model=RobotStateListResponse)
async def list_physical_library_robotics(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> RobotStateListResponse:
    _ensure_state(context)
    robots = [_serialize_robot(item) for item in aml_state.get_aml_robots().values()]
    return RobotStateListResponse(robotList=RobotStateListResource(robot=robots))


@router.get("/physicalLibrary/robotics/{id}", response_model=RobotStateResponse)
async def get_physical_library_robot(
    id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> RobotStateResponse:
    _ensure_state(context)
    robot_id = _validate_identifier(id, field_name="id")
    robot = aml_state.get_aml_robot(robot_id)
    if robot is None:
        raise HTTPException(status_code=404, detail="Robot not found")
    return RobotStateResponse(robot=_serialize_robot(robot))


@router.get("/physicalLibrary/towers", response_model=TowerInfoListResponse)
async def list_physical_library_towers(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> TowerInfoListResponse:
    _ensure_state(context)
    towers = [_serialize_tower(item) for item in aml_state.get_aml_towers().values()]
    return TowerInfoListResponse(towerList=TowerInfoListResource(tower=towers))


@router.get("/physicalLibrary/towers/{id}", response_model=TowerInfoResponse)
async def get_physical_library_tower(
    id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> TowerInfoResponse:
    _ensure_state(context)
    tower_id = _validate_identifier(id, field_name="id")
    tower = aml_state.get_aml_tower(tower_id)
    if tower is None:
        raise HTTPException(status_code=404, detail="Tower not found")
    return TowerInfoResponse(tower=_serialize_tower(tower))


@router.get("/physicalLibrary/magazines", response_model=MagazineInfoListResponse)
async def list_physical_library_magazines(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> MagazineInfoListResponse:
    _ensure_state(context)
    magazines = [_serialize_magazine(item) for item in aml_state.get_aml_magazines().values()]
    return MagazineInfoListResponse(magazineList=MagazineInfoListResource(magazine=magazines))


@router.get("/physicalLibrary/magazines/{id}", response_model=MagazineInfoResponse)
async def get_physical_library_magazine(
    id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> MagazineInfoResponse:
    _ensure_state(context)
    magazine_id = _validate_identifier(id, field_name="id")
    magazine = aml_state.get_aml_magazine(magazine_id)
    if magazine is None:
        raise HTTPException(status_code=404, detail="Magazine not found")
    return MagazineInfoResponse(magazine=_serialize_magazine(magazine))


@router.get("/diagnostics/tests", response_model=DiagnosticTestListResponse)
async def list_diagnostic_tests(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> DiagnosticTestListResponse:
    _ensure_state(context)
    tests = [_serialize_diagnostic_test(item) for item in aml_state.list_aml_diagnostic_tests()]
    return DiagnosticTestListResponse(diagnosticTestList=DiagnosticTestListResource(diagnosticTest=tests))


@router.post("/diagnostics/tests/run", response_model=WSResultCode)
async def run_diagnostic_tests(
    payload: DiagnosticRunRequest = Body(default_factory=DiagnosticRunRequest),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    if payload.testIds:
        selected = []
        for raw_test_id in payload.testIds:
            test_id = _validate_identifier(raw_test_id, field_name="testId")
            test = aml_state.get_aml_diagnostic_test(test_id)
            if test is None:
                raise HTTPException(status_code=404, detail=f"Diagnostic test {test_id} not found")
            selected.append(test)
    else:
        selected = aml_state.list_aml_diagnostic_tests()

    started_at = _timestamp()
    result_id = f"diag-result-{uuid4().hex[:8]}"
    result = {
        "id": result_id,
        "testId": selected[0]["id"] if len(selected) == 1 else payload.suiteName or "suite:all",
        "startTime": started_at,
        "endTime": started_at,
        "status": "completed",
        "passed": len(selected),
        "failed": 0,
        "details": [
            {
                "name": str(test["name"]),
                "status": "passed",
                "message": f"{test['name']} completed successfully.",
            }
            for test in selected
        ],
    }
    aml_state.set_aml_diagnostic_result(result_id, result)
    return _ws_result(f"Ran {len(selected)} diagnostic test(s)")


@router.get("/diagnostics/tests/results", response_model=DiagnosticResultResponse)
async def get_latest_diagnostic_results(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> DiagnosticResultResponse:
    _ensure_state(context)
    result = aml_state.get_latest_aml_diagnostic_result()
    if result is None:
        raise HTTPException(status_code=404, detail="Diagnostic results not found")
    return DiagnosticResultResponse(diagnosticResult=_serialize_diagnostic_result(result))


@router.get("/diagnostics/tests/results/{id}", response_model=DiagnosticResultResponse)
async def get_diagnostic_result(
    id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> DiagnosticResultResponse:
    _ensure_state(context)
    result_id = _validate_identifier(id, field_name="id")
    result = aml_state.get_aml_diagnostic_result(result_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Diagnostic result not found")
    return DiagnosticResultResponse(diagnosticResult=_serialize_diagnostic_result(result))
