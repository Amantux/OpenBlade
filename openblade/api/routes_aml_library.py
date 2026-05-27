"""AML library overview routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from openblade.api import aml_state
from openblade.api.routes_aml_auth import require_auth
from openblade.bootstrap import AppContext, get_context
from openblade.catalog.models import AmlUser
from openblade.domain.models import CartridgeState, ChangerState, DriveState

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
    return PhysicalLibraryResource(
        name=aml_state.get_library_name(),
        serialNumber=_serial_number(context),
        firmware=_FIRMWARE_VERSION,
        model=_LIBRARY_MODEL,
        type=_LIBRARY_TYPE,
        status=_mode_status(),
        roboticsState=inventory.changer_state.value,
        modules=1,
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


@router.get("/physicalLibrary/i3-i6/modules", response_model=ModuleListResponse)
async def get_library_modules(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> ModuleListResponse:
    _ensure_state(context)
    inventory = context.library.inventory()
    module = ModuleResource(
        id=1,
        serialNumber=f"{_serial_number(context)}-M1",
        model=_LIBRARY_MODEL,
        status="good" if inventory.changer_state != ChangerState.ERROR else "failed",
        slots=len(inventory.slots),
        drives=len(inventory.drives),
    )
    return ModuleListResponse(moduleList=ModuleListResource(module=[module]))


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
