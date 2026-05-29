"""AML physical robotics, tower, IE station, and magazine routes."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from openblade.api import aml_state
from openblade.api.routes_aml_auth import WSResultCode, _ensure_state, _require_admin, require_auth
from openblade.bootstrap import AppContext, get_context
from openblade.catalog.models import AmlUser
from openblade.domain.models import DriveState

router = APIRouter()


class Slot(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    address: str
    state: str
    barcode: str | None = None
    type: str


class SlotListResource(BaseModel):
    slot: list[Slot]


class SlotListResponse(BaseModel):
    slotList: SlotListResource


class Drive(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    status: str
    state: str
    barcode: str | None = None


class DriveListResource(BaseModel):
    drive: list[Drive]


class DriveListResponse(BaseModel):
    driveList: DriveListResource


class Robot(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    serialNumber: str
    model: str
    status: str
    state: str
    location: str
    homeSlot: str


class RobotListResource(BaseModel):
    robot: list[Robot]


class RobotListResponse(BaseModel):
    robotList: RobotListResource


class RobotResponse(BaseModel):
    robot: Robot


class RobotPatch(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    serialNumber: str | None = None
    model: str | None = None
    status: str | None = None
    state: str | None = None
    location: str | None = None
    homeSlot: str | None = None


class RobotUpdateRequest(BaseModel):
    robot: RobotPatch


class RobotStatus(BaseModel):
    id: str
    status: str
    state: str
    health: str
    location: str


class RobotStatusResponse(BaseModel):
    robotStatus: RobotStatus


class Tower(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    serialNumber: str
    model: str
    status: str
    slots: int
    occupiedSlots: int
    drives: list[str] = Field(default_factory=list)


class TowerListResource(BaseModel):
    tower: list[Tower]


class TowerListResponse(BaseModel):
    towerList: TowerListResource


class TowerResponse(BaseModel):
    tower: Tower


class TowerPatch(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    serialNumber: str | None = None
    model: str | None = None
    status: str | None = None
    slots: int | None = None
    occupiedSlots: int | None = None
    drives: list[str] | None = None


class TowerUpdateRequest(BaseModel):
    tower: TowerPatch


class TowerStatus(BaseModel):
    id: str
    status: str
    health: str
    slots: int
    occupiedSlots: int
    drives: int


class TowerStatusResponse(BaseModel):
    towerStatus: TowerStatus


class IEStation(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    serialNumber: str
    status: str
    state: str
    slotCount: int
    slots: list[Slot] = Field(default_factory=list)


class IEStationListResource(BaseModel):
    ieStation: list[IEStation]


class IEStationListResponse(BaseModel):
    ieStationList: IEStationListResource


class IEStationResponse(BaseModel):
    ieStation: IEStation


class IEStationPatch(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    serialNumber: str | None = None
    status: str | None = None
    state: str | None = None
    slotCount: int | None = None
    slots: list[Slot] | None = None


class IEStationUpdateRequest(BaseModel):
    ieStation: IEStationPatch


class IEStationStatus(BaseModel):
    id: str
    status: str
    state: str
    health: str
    slotCount: int


class IEStationStatusResponse(BaseModel):
    ieStationStatus: IEStationStatus


class Magazine(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    location: str
    status: str
    slotCount: int
    occupiedSlots: int
    tapes: list[str] = Field(default_factory=list)


class MagazineListResource(BaseModel):
    magazine: list[Magazine]


class MagazineListResponse(BaseModel):
    magazineList: MagazineListResource


class MagazineResponse(BaseModel):
    magazine: Magazine


class MagazinePatch(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    location: str | None = None
    status: str | None = None
    slotCount: int | None = None
    occupiedSlots: int | None = None
    tapes: list[str] | None = None


class MagazineUpdateRequest(BaseModel):
    magazine: MagazinePatch


class PhysicalCategorySummary(BaseModel):
    count: int
    status: str


class PhysicalSummary(BaseModel):
    robots: PhysicalCategorySummary
    towers: PhysicalCategorySummary
    ieStations: PhysicalCategorySummary
    magazines: PhysicalCategorySummary


class PhysicalSummaryResponse(BaseModel):
    physicalSummary: PhysicalSummary


class PhysicalStatus(BaseModel):
    overall: str
    robotics: str
    towers: str
    ieStations: str
    magazines: str


class PhysicalStatusResponse(BaseModel):
    physicalStatus: PhysicalStatus


class Element(BaseModel):
    type: str
    id: str
    location: str
    status: str


class ElementListResource(BaseModel):
    element: list[Element]


class ElementListResponse(BaseModel):
    elementList: ElementListResource


def _ws_result(summary: str = "Operation completed") -> WSResultCode:
    return WSResultCode(summary=summary)



def _job_response(job_type: str, message: str) -> dict[str, str]:
    job_id = str(uuid4())
    aml_state.set_aml_job(job_id, {"type": job_type, "status": "queued", "result": message})
    return {"job_id": job_id, "status": "queued", "message": message}


def _validate_identifier(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail=f"{field_name} is required")
    return normalized


def _validate_patch(payload: BaseModel, *, identifier_field: str = "id") -> dict[str, Any]:
    updates = payload.model_dump(exclude_none=True)
    updates.pop(identifier_field, None)
    return updates


def _health_from_status(status_value: str) -> str:
    normalized = status_value.strip().lower()
    if normalized in {"failed", "fault", "offline", "critical"}:
        return "failed"
    if normalized in {"warning", "degraded", "standby", "resetting", "ejected"}:
        return "warning"
    return "good"


def _collection_health(items: list[dict[str, Any]]) -> str:
    levels = [_health_from_status(str(item.get("status", "good"))) for item in items]
    if "failed" in levels:
        return "failed"
    if "warning" in levels:
        return "warning"
    return "good"


def _serialize_slot(slot: dict[str, Any]) -> Slot:
    return Slot.model_validate(slot)


def _serialize_drive(drive: dict[str, Any]) -> Drive:
    return Drive.model_validate(drive)


def _serialize_robot(robot: dict[str, Any]) -> Robot:
    return Robot.model_validate(robot)


def _serialize_tower(tower: dict[str, Any]) -> Tower:
    return Tower.model_validate(tower)


def _serialize_ie_station(station: dict[str, Any]) -> IEStation:
    return IEStation.model_validate(station)


def _serialize_magazine(magazine: dict[str, Any]) -> Magazine:
    return Magazine.model_validate(magazine)


def _get_robot_or_404(robot_id: str) -> dict[str, Any]:
    robot = aml_state.get_aml_robot(robot_id)
    if robot is None:
        raise HTTPException(status_code=404, detail="Robot not found")
    return robot


def _get_tower_or_404(tower_id: str) -> dict[str, Any]:
    tower = aml_state.get_aml_tower(tower_id)
    if tower is None:
        raise HTTPException(status_code=404, detail="Tower not found")
    return tower


def _get_ie_station_or_404(station_id: str) -> dict[str, Any]:
    station = aml_state.get_aml_ie_station(station_id)
    if station is None:
        raise HTTPException(status_code=404, detail="IE station not found")
    return station


def _get_magazine_or_404(magazine_id: str) -> dict[str, Any]:
    magazine = aml_state.get_aml_magazine(magazine_id)
    if magazine is None:
        raise HTTPException(status_code=404, detail="Magazine not found")
    return magazine


def _barcode_pool(context: AppContext, minimum_count: int) -> list[str]:
    inventory = context.library.inventory()
    barcodes = [str(slot.barcode) for slot in inventory.slots if slot.barcode is not None]
    next_index = len(barcodes) + 1
    while len(barcodes) < minimum_count:
        candidate = f"VOL{next_index:03d}L9"
        if candidate not in barcodes:
            barcodes.append(candidate)
        next_index += 1
    return barcodes


def _barcode_by_slot_address() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for media in aml_state.list_aml_media():
        slot_address = str(media.get("slotAddress", "")).strip()
        barcode = str(media.get("barcode", "")).strip()
        if slot_address and barcode:
            mapping[slot_address] = barcode
    return mapping


def _magazine_slot_addresses(magazine: dict[str, Any]) -> list[str]:
    configured = magazine.get("slotAddresses")
    if isinstance(configured, list):
        addresses = [str(item).strip() for item in configured if str(item).strip()]
        if addresses:
            return addresses

    bay = int(magazine.get("bay", 1))
    slot_count = int(magazine.get("slotCount", 0))
    return [f"1,{bay},{index}" for index in range(1, slot_count + 1)]


def _build_tower_slots(tower: dict[str, Any], context: AppContext) -> list[Slot]:
    total_slots = int(tower.get("slots", 0))
    occupied_slots = min(int(tower.get("occupiedSlots", 0)), total_slots)
    bay = int(tower.get("bay", 1))
    inventory = context.library.inventory()
    start_slot_id = 1 + ((bay - 1) * total_slots)
    end_slot_id = start_slot_id + total_slots - 1
    barcodes = [
        str(slot.barcode)
        for slot in inventory.slots
        if start_slot_id <= slot.slot_id <= end_slot_id and slot.barcode is not None
    ]
    if len(barcodes) < occupied_slots:
        barcodes = _barcode_pool(context, occupied_slots)
    return [
        Slot(
            id=f"{tower['id']}-S{index}",
            address=f"1,{bay},{index}",
            state="occupied" if index <= occupied_slots else "empty",
            barcode=barcodes[index - 1] if index <= occupied_slots else None,
            type="storage",
        )
        for index in range(1, total_slots + 1)
    ]


def _build_tower_drives(tower: dict[str, Any], context: AppContext) -> list[Drive]:
    inventory = context.library.inventory()
    drive_ids = list(tower.get("drives", []))
    drives: list[Drive] = []
    for index, drive_id in enumerate(drive_ids):
        inventory_drive = inventory.drives[index] if index < len(inventory.drives) else None
        state = inventory_drive.drive_state.value if inventory_drive is not None else DriveState.EMPTY.value
        status = "failed" if inventory_drive is not None and inventory_drive.drive_state == DriveState.FAILED else "online"
        barcode = str(inventory_drive.barcode) if inventory_drive is not None and inventory_drive.barcode is not None else None
        drives.append(Drive(id=drive_id, status=status, state=state, barcode=barcode))
    return drives


def _tower_with_counts(tower: dict[str, Any], context: AppContext) -> dict[str, Any]:
    slots = _build_tower_slots(tower, context)
    updated = dict(tower)
    updated["occupiedSlots"] = sum(1 for slot in slots if slot.state == "occupied")
    updated["drives"] = list(updated.get("drives", []))
    return updated


def _ie_station_with_counts(station: dict[str, Any]) -> dict[str, Any]:
    updated = dict(station)
    updated["slots"] = [slot.model_dump() if isinstance(slot, Slot) else dict(slot) for slot in updated.get("slots", [])]
    updated["slotCount"] = len(updated["slots"])
    return updated


def _magazine_with_counts(magazine: dict[str, Any]) -> dict[str, Any]:
    updated = dict(magazine)
    slot_addresses = _magazine_slot_addresses(updated)
    barcode_by_address = _barcode_by_slot_address()
    mapped_tapes = [barcode_by_address[address] for address in slot_addresses if address in barcode_by_address]
    updated["slotAddresses"] = slot_addresses
    updated["tapes"] = mapped_tapes
    updated["slotCount"] = len(slot_addresses) if slot_addresses else int(updated.get("slotCount", 0))
    updated["occupiedSlots"] = len(mapped_tapes)
    return updated


def _robot_status_response(robot: dict[str, Any]) -> RobotStatusResponse:
    return RobotStatusResponse(
        robotStatus=RobotStatus(
            id=str(robot["id"]),
            status=str(robot["status"]),
            state=str(robot["state"]),
            health=_health_from_status(str(robot["status"])),
            location=str(robot["location"]),
        )
    )


def _tower_status_response(tower: dict[str, Any], context: AppContext) -> TowerStatusResponse:
    current = _tower_with_counts(tower, context)
    return TowerStatusResponse(
        towerStatus=TowerStatus(
            id=str(current["id"]),
            status=str(current["status"]),
            health=_health_from_status(str(current["status"])),
            slots=int(current["slots"]),
            occupiedSlots=int(current["occupiedSlots"]),
            drives=len(current.get("drives", [])),
        )
    )


def _ie_station_status_response(station: dict[str, Any]) -> IEStationStatusResponse:
    current = _ie_station_with_counts(station)
    return IEStationStatusResponse(
        ieStationStatus=IEStationStatus(
            id=str(current["id"]),
            status=str(current["status"]),
            state=str(current["state"]),
            health=_health_from_status(str(current["status"])),
            slotCount=int(current["slotCount"]),
        )
    )


def _magazine_slots(magazine: dict[str, Any]) -> list[Slot]:
    current = _magazine_with_counts(magazine)
    slot_addresses = _magazine_slot_addresses(current)
    barcode_by_address = _barcode_by_slot_address()
    slots: list[Slot] = []
    for index, library_coordinate in enumerate(slot_addresses, start=1):
        barcode = barcode_by_address.get(library_coordinate)
        slots.append(
            Slot(
                id=f"{current['id']}-S{index}",
                address=f"{current['id']},{index}",
                state="occupied" if barcode else "empty",
                barcode=barcode,
                type="magazine",
                libraryCoordinate=library_coordinate,
            )
        )
    return slots


@router.get("/robots", response_model=RobotListResponse)
async def list_robots(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> RobotListResponse:
    _ensure_state(context)
    robots = [_serialize_robot(item) for item in aml_state.get_aml_robots().values()]
    return RobotListResponse(robotList=RobotListResource(robot=robots))


@router.get("/robot/{id}", response_model=RobotResponse)
async def get_robot(
    id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> RobotResponse:
    _ensure_state(context)
    return RobotResponse(robot=_serialize_robot(_get_robot_or_404(_validate_identifier(id, field_name="Robot id"))))


@router.put("/robot/{id}", response_model=RobotResponse)
async def put_robot(
    id: str,
    payload: RobotUpdateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> RobotResponse:
    _ensure_state(context)
    _require_admin(current_user)
    robot_id = _validate_identifier(id, field_name="Robot id")
    _get_robot_or_404(robot_id)
    robot = aml_state.update_aml_robot(robot_id, _validate_patch(payload.robot))
    return RobotResponse(robot=_serialize_robot(robot or _get_robot_or_404(robot_id)))


@router.post("/robot/{id}/home", response_model=WSResultCode)
async def home_robot(
    id: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    robot_id = _validate_identifier(id, field_name="Robot id")
    _get_robot_or_404(robot_id)
    aml_state.move_aml_robot_home(robot_id)
    return _ws_result("Operation completed")


@router.post("/robot/{id}/calibrate", response_model=WSResultCode)
async def calibrate_robot(
    id: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    robot_id = _validate_identifier(id, field_name="Robot id")
    _get_robot_or_404(robot_id)
    aml_state.calibrate_aml_robot(robot_id)
    return _ws_result("Operation completed")


@router.get("/robot/{id}/status", response_model=RobotStatusResponse)
async def get_robot_status(
    id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> RobotStatusResponse:
    _ensure_state(context)
    return _robot_status_response(_get_robot_or_404(_validate_identifier(id, field_name="Robot id")))


@router.get("/devices/robot/{name}", response_model=RobotResponse)
async def get_robot_device(
    name: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> RobotResponse:
    _ensure_state(context)
    return RobotResponse(robot=_serialize_robot(_get_robot_or_404(_validate_identifier(name, field_name="Robot name"))))


@router.post("/devices/robot/{name}", response_model=RobotResponse)
async def post_robot_device(
    name: str,
    payload: RobotUpdateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> RobotResponse:
    _ensure_state(context)
    _require_admin(current_user)
    robot_id = _validate_identifier(name, field_name="Robot name")
    _get_robot_or_404(robot_id)
    robot = aml_state.update_aml_robot(robot_id, _validate_patch(payload.robot))
    return RobotResponse(robot=_serialize_robot(robot or _get_robot_or_404(robot_id)))


@router.get("/devices/robot/{name}/state", response_model=RobotStatusResponse)
async def get_robot_device_state(
    name: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> RobotStatusResponse:
    _ensure_state(context)
    return _robot_status_response(_get_robot_or_404(_validate_identifier(name, field_name="Robot name")))


@router.put("/devices/robot/{name}/state", response_model=RobotStatusResponse)
async def put_robot_device_state(
    name: str,
    payload: dict[str, Any],
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> RobotStatusResponse:
    _ensure_state(context)
    _require_admin(current_user)
    robot_id = _validate_identifier(name, field_name="Robot name")
    robot = _get_robot_or_404(robot_id)
    updates = {key: value for key, value in payload.items() if key in {"state", "status", "location"} and value is not None}
    updated = aml_state.update_aml_robot(robot_id, {**robot, **updates}) or _get_robot_or_404(robot_id)
    return _robot_status_response(updated)


@router.post("/devices/robot/{name}/park", status_code=status.HTTP_202_ACCEPTED)
async def park_robot_device(
    name: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, str]:
    _ensure_state(context)
    _require_admin(current_user)
    robot_id = _validate_identifier(name, field_name="Robot name")
    robot = _get_robot_or_404(robot_id)
    aml_state.update_aml_robot(robot_id, {"state": "parked", "location": robot.get("homeSlot", "park")})
    return _job_response("robot-park", f"Robot {robot_id} park queued")


@router.get("/towers", response_model=TowerListResponse)
async def list_towers(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> TowerListResponse:
    _ensure_state(context)
    towers = [_serialize_tower(_tower_with_counts(item, context)) for item in aml_state.get_aml_towers().values()]
    return TowerListResponse(towerList=TowerListResource(tower=towers))


@router.get("/tower/{id}", response_model=TowerResponse)
async def get_tower(
    id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> TowerResponse:
    _ensure_state(context)
    tower_id = _validate_identifier(id, field_name="Tower id")
    return TowerResponse(tower=_serialize_tower(_tower_with_counts(_get_tower_or_404(tower_id), context)))


@router.put("/tower/{id}", response_model=TowerResponse)
async def put_tower(
    id: str,
    payload: TowerUpdateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> TowerResponse:
    _ensure_state(context)
    _require_admin(current_user)
    tower_id = _validate_identifier(id, field_name="Tower id")
    _get_tower_or_404(tower_id)
    tower = aml_state.update_aml_tower(tower_id, _validate_patch(payload.tower))
    return TowerResponse(tower=_serialize_tower(_tower_with_counts(tower or _get_tower_or_404(tower_id), context)))


@router.get("/tower/{id}/status", response_model=TowerStatusResponse)
async def get_tower_status(
    id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> TowerStatusResponse:
    _ensure_state(context)
    return _tower_status_response(_get_tower_or_404(_validate_identifier(id, field_name="Tower id")), context)


@router.get("/tower/{id}/slots", response_model=SlotListResponse)
async def list_tower_slots(
    id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> SlotListResponse:
    _ensure_state(context)
    tower = _get_tower_or_404(_validate_identifier(id, field_name="Tower id"))
    return SlotListResponse(slotList=SlotListResource(slot=_build_tower_slots(tower, context)))


@router.get("/tower/{id}/drives", response_model=DriveListResponse)
async def list_tower_drives(
    id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> DriveListResponse:
    _ensure_state(context)
    tower = _get_tower_or_404(_validate_identifier(id, field_name="Tower id"))
    return DriveListResponse(driveList=DriveListResource(drive=_build_tower_drives(tower, context)))


@router.get("/ieStations", response_model=IEStationListResponse)
async def list_ie_stations(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IEStationListResponse:
    _ensure_state(context)
    stations = [_serialize_ie_station(_ie_station_with_counts(item)) for item in aml_state.get_aml_ie_stations().values()]
    return IEStationListResponse(ieStationList=IEStationListResource(ieStation=stations))


@router.get("/ieStation/{id}", response_model=IEStationResponse)
async def get_ie_station(
    id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IEStationResponse:
    _ensure_state(context)
    station_id = _validate_identifier(id, field_name="IE station id")
    return IEStationResponse(ieStation=_serialize_ie_station(_ie_station_with_counts(_get_ie_station_or_404(station_id))))


@router.put("/ieStation/{id}", response_model=IEStationResponse)
async def put_ie_station(
    id: str,
    payload: IEStationUpdateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IEStationResponse:
    _ensure_state(context)
    _require_admin(current_user)
    station_id = _validate_identifier(id, field_name="IE station id")
    _get_ie_station_or_404(station_id)
    station = aml_state.update_aml_ie_station(station_id, _validate_patch(payload.ieStation))
    return IEStationResponse(ieStation=_serialize_ie_station(_ie_station_with_counts(station or _get_ie_station_or_404(station_id))))


@router.post("/ieStation/{id}/open", response_model=WSResultCode)
async def open_ie_station(
    id: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    station_id = _validate_identifier(id, field_name="IE station id")
    _get_ie_station_or_404(station_id)
    aml_state.open_aml_ie_station(station_id)
    return _ws_result("Operation completed")


@router.post("/ieStation/{id}/close", response_model=WSResultCode)
async def close_ie_station(
    id: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    station_id = _validate_identifier(id, field_name="IE station id")
    _get_ie_station_or_404(station_id)
    aml_state.close_aml_ie_station(station_id)
    return _ws_result("Operation completed")


@router.get("/ieStation/{id}/status", response_model=IEStationStatusResponse)
async def get_ie_station_status(
    id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IEStationStatusResponse:
    _ensure_state(context)
    return _ie_station_status_response(_get_ie_station_or_404(_validate_identifier(id, field_name="IE station id")))


@router.get("/ieStation/{id}/slots", response_model=SlotListResponse)
async def list_ie_station_slots(
    id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> SlotListResponse:
    _ensure_state(context)
    station = _ie_station_with_counts(_get_ie_station_or_404(_validate_identifier(id, field_name="IE station id")))
    return SlotListResponse(slotList=SlotListResource(slot=[_serialize_slot(slot) for slot in station.get("slots", [])]))


@router.get("/magazines", response_model=MagazineListResponse)
async def list_magazines(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> MagazineListResponse:
    _ensure_state(context)
    magazines = [_serialize_magazine(_magazine_with_counts(item)) for item in aml_state.get_aml_magazines().values()]
    return MagazineListResponse(magazineList=MagazineListResource(magazine=magazines))


@router.get("/magazine/{id}", response_model=MagazineResponse)
async def get_magazine(
    id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> MagazineResponse:
    _ensure_state(context)
    magazine_id = _validate_identifier(id, field_name="Magazine id")
    return MagazineResponse(magazine=_serialize_magazine(_magazine_with_counts(_get_magazine_or_404(magazine_id))))


@router.put("/magazine/{id}", response_model=MagazineResponse)
async def put_magazine(
    id: str,
    payload: MagazineUpdateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> MagazineResponse:
    _ensure_state(context)
    _require_admin(current_user)
    magazine_id = _validate_identifier(id, field_name="Magazine id")
    _get_magazine_or_404(magazine_id)
    magazine = aml_state.update_aml_magazine(magazine_id, _validate_patch(payload.magazine))
    return MagazineResponse(magazine=_serialize_magazine(_magazine_with_counts(magazine or _get_magazine_or_404(magazine_id))))


@router.post("/magazine/{id}/eject", response_model=WSResultCode)
async def eject_magazine(
    id: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    magazine_id = _validate_identifier(id, field_name="Magazine id")
    _get_magazine_or_404(magazine_id)
    aml_state.eject_aml_magazine(magazine_id)
    return _ws_result("Operation completed")


@router.post("/magazine/{id}/insert", response_model=WSResultCode)
async def insert_magazine(
    id: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    magazine_id = _validate_identifier(id, field_name="Magazine id")
    _get_magazine_or_404(magazine_id)
    aml_state.insert_aml_magazine(magazine_id)
    return _ws_result("Operation completed")


@router.get("/magazine/{id}/slots", response_model=SlotListResponse)
async def list_magazine_slots(
    id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> SlotListResponse:
    _ensure_state(context)
    magazine = _get_magazine_or_404(_validate_identifier(id, field_name="Magazine id"))
    return SlotListResponse(slotList=SlotListResource(slot=_magazine_slots(magazine)))


@router.get("/physical", response_model=PhysicalSummaryResponse)
async def get_physical_summary(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PhysicalSummaryResponse:
    _ensure_state(context)
    robots = list(aml_state.get_aml_robots().values())
    towers = list(aml_state.get_aml_towers().values())
    ie_stations = list(aml_state.get_aml_ie_stations().values())
    magazines = list(aml_state.get_aml_magazines().values())
    return PhysicalSummaryResponse(
        physicalSummary=PhysicalSummary(
            robots=PhysicalCategorySummary(count=len(robots), status=_collection_health(robots)),
            towers=PhysicalCategorySummary(count=len(towers), status=_collection_health(towers)),
            ieStations=PhysicalCategorySummary(count=len(ie_stations), status=_collection_health(ie_stations)),
            magazines=PhysicalCategorySummary(count=len(magazines), status=_collection_health(magazines)),
        )
    )


@router.get("/physical/status", response_model=PhysicalStatusResponse)
async def get_physical_status(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PhysicalStatusResponse:
    _ensure_state(context)
    robotics = _collection_health(list(aml_state.get_aml_robots().values()))
    towers = _collection_health(list(aml_state.get_aml_towers().values()))
    ie_stations = _collection_health(list(aml_state.get_aml_ie_stations().values()))
    magazines = _collection_health(list(aml_state.get_aml_magazines().values()))
    overall = _collection_health(
        [
            {"status": robotics},
            {"status": towers},
            {"status": ie_stations},
            {"status": magazines},
        ]
    )
    return PhysicalStatusResponse(
        physicalStatus=PhysicalStatus(
            overall=overall,
            robotics=robotics,
            towers=towers,
            ieStations=ie_stations,
            magazines=magazines,
        )
    )


@router.post("/physical/audit", response_model=WSResultCode)
async def audit_physical(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    aml_state.run_physical_audit()
    return _ws_result("Operation completed")


@router.get("/physical/elements", response_model=ElementListResponse)
async def get_physical_elements(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> ElementListResponse:
    _ensure_state(context)
    elements = [
        Element(type="robot", id=item["id"], location=item.get("location", "unknown"), status=item["status"])
        for item in aml_state.get_aml_robots().values()
    ]
    elements.extend(
        Element(type="tower", id=item["id"], location="library", status=item["status"])
        for item in aml_state.get_aml_towers().values()
    )
    elements.extend(
        Element(type="ieStation", id=item["id"], location="front-access", status=item["status"])
        for item in aml_state.get_aml_ie_stations().values()
    )
    elements.extend(
        Element(type="magazine", id=item["id"], location=item.get("location", "unknown"), status=item["status"])
        for item in aml_state.get_aml_magazines().values()
    )
    return ElementListResponse(elementList=ElementListResource(element=elements))
