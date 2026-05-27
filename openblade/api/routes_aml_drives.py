"""AML drive management routes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from openblade.api import aml_state
from openblade.api.library_context import get_active_library, get_library_profile
from openblade.api.routes_aml_auth import WSResultCode, _ensure_state, _require_admin, require_auth
from openblade.api.service_auth import require_service_token
from openblade.bootstrap import AppContext, get_context
from openblade.catalog.models import AmlUser

router = APIRouter()

_SUPPORTED_DRIVE_TYPES: dict[str, dict[str, Any]] = {
    "LTO-9": {
        "name": "LTO-9",
        "description": "IBM LTO-9 half-height tape drive",
        "speeds": ["300MB/s", "400MB/s"],
        "generations": ["LTO-8", "LTO-9"],
    }
}


class Drive(BaseModel):
    model_config = ConfigDict(extra="allow")

    serialNumber: str
    model: str
    type: str
    status: str
    state: str
    partition: str
    location: str
    firmware: str
    loadCount: int
    errorCount: int
    cleaningCount: int
    lastCleaned: str | None = None


class DriveListResource(BaseModel):
    drive: list[Drive]


class DriveListResponse(BaseModel):
    driveList: DriveListResource


class DriveResponse(BaseModel):
    drive: Drive


class DrivePatch(BaseModel):
    model_config = ConfigDict(extra="allow")

    alias: str | None = None
    description: str | None = None


class DriveUpdateRequest(BaseModel):
    drive: DrivePatch


class DriveStatus(BaseModel):
    overall: str
    read: str
    write: str
    cleaning: str
    connectivity: str


class DriveStatusResponse(BaseModel):
    driveStatus: DriveStatus


class MediaResource(BaseModel):
    model_config = ConfigDict(extra="allow")

    barcode: str
    type: str
    state: str


class MediaResponse(BaseModel):
    media: MediaResource | None = None


class DriveStats(BaseModel):
    loadCount: int
    unloadCount: int
    errorCount: int
    readErrors: int
    writeErrors: int
    cleaningCount: int
    totalHours: float
    lastLoaded: str | None = None


class DriveStatsResponse(BaseModel):
    driveStats: DriveStats


class HistoryEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    timestamp: str
    type: str
    media: str | None = None
    result: str
    errorCode: str | None = None


class HistoryListResource(BaseModel):
    event: list[HistoryEvent]


class HistoryListResponse(BaseModel):
    historyList: HistoryListResource


class DriveError(BaseModel):
    model_config = ConfigDict(extra="allow")

    timestamp: str
    type: str
    code: str
    description: str
    media: str | None = None


class ErrorListResource(BaseModel):
    error: list[DriveError]


class ErrorListResponse(BaseModel):
    errorList: ErrorListResource


class FirmwareInfo(BaseModel):
    current: str
    available: str
    updateRequired: bool


class FirmwareResponse(BaseModel):
    firmware: FirmwareInfo


class DiagnosticTest(BaseModel):
    name: str
    result: str
    details: str | None = None


class DiagnosticResult(BaseModel):
    timestamp: str | None = None
    status: str
    tests: list[DiagnosticTest] = Field(default_factory=list)


class DiagnosticResponse(BaseModel):
    diagnosticResult: DiagnosticResult


class DriveConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    compression: bool
    encryption: bool
    speed: str
    bufferSize: str


class DriveConfigResponse(BaseModel):
    driveConfig: DriveConfig


class DriveConfigPatch(BaseModel):
    model_config = ConfigDict(extra="allow")

    compression: bool | None = None
    encryption: bool | None = None
    speed: str | None = None
    bufferSize: str | None = None


class DriveConfigUpdateRequest(BaseModel):
    driveConfig: DriveConfigPatch


class FleetStatus(BaseModel):
    total: int
    online: int
    offline: int
    error: int
    cleaning: int


class FleetStatusResponse(BaseModel):
    fleetStatus: FleetStatus


class DriveType(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    description: str
    speeds: list[str] = Field(default_factory=list)
    generations: list[str] = Field(default_factory=list)


class TypeListResource(BaseModel):
    type: list[DriveType]


class TypeListResponse(BaseModel):
    typeList: TypeListResource


def _ws_result(summary: str = "Operation completed") -> WSResultCode:
    return WSResultCode(summary=summary)



def _job_response(job_type: str, message: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    job_id = str(uuid4())
    aml_state.set_aml_job(job_id, {"type": job_type, "status": "queued", "result": message, "metadata": metadata or {}})
    return {"job_id": job_id, "status": "queued", "message": message}


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_identifier(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail=f"{field_name} is required")
    return normalized


def _validate_drive_patch(payload: DrivePatch) -> dict[str, Any]:
    updates = payload.model_dump(exclude_none=True)
    for field_name in ("alias", "description"):
        if field_name in updates:
            updates[field_name] = _validate_identifier(str(updates[field_name]), field_name=field_name)
    return updates


def _validate_drive_config(payload: DriveConfigPatch) -> dict[str, Any]:
    updates = payload.model_dump(exclude_none=True)
    for field_name in ("speed", "bufferSize"):
        if field_name in updates:
            updates[field_name] = _validate_identifier(str(updates[field_name]), field_name=field_name)
    return updates


def _scoped_drives(context: AppContext) -> list[dict[str, Any]]:
    active_library = get_active_library(context.catalog)
    drives = aml_state.list_aml_drives()
    if active_library is None:
        return drives

    profile = get_library_profile(active_library)
    drive_count = max(profile["drive_count"], 0)
    scoped = drives[:drive_count]
    for index, drive in enumerate(scoped, start=1):
        drive["location"] = f"{active_library.name} / Bay {index}"
    return scoped


def _drive_aliases(drives: list[dict[str, Any]]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for index, drive in enumerate(drives, start=1):
        serial_number = str(drive.get("serialNumber", ""))
        if serial_number:
            aliases[f"DRV-{index:03d}"] = serial_number
    return aliases


def _get_drive_or_404(serial_number: str, context: AppContext) -> dict[str, Any]:
    drives = _scoped_drives(context)
    resolved_serial_number = _drive_aliases(drives).get(serial_number, serial_number)
    drive = next(
        (candidate for candidate in drives if str(candidate.get("serialNumber")) == resolved_serial_number),
        None,
    )
    if drive is None:
        raise HTTPException(status_code=404, detail="Drive not found")
    return drive


def _serialize_drive(drive: dict[str, Any]) -> Drive:
    return Drive.model_validate(drive)


def _drive_needs_cleaning(drive: dict[str, Any]) -> bool:
    if bool(drive.get("cleaningRequired", False)):
        return True
    if str(drive.get("state", "")).lower() in {"cleaning_required", "cleaning-required", "needs_cleaning"}:
        return True
    threshold = int(drive.get("cleaningThreshold", 100))
    load_count = int(drive.get("loadCount", 0))
    cleaning_count = int(drive.get("cleaningCount", 0))
    return load_count > 0 and (load_count - cleaning_count * 50) >= threshold


def _drive_status(drive: dict[str, Any]) -> DriveStatus:
    status = str(drive.get("status", "online")).lower()
    state = str(drive.get("state", "idle")).lower()
    error_count = int(drive.get("errorCount", 0))

    connectivity = "failed" if status == "offline" else "good"
    if error_count >= 5 or state == "error":
        read = "failed"
        write = "failed"
    elif error_count > 0:
        read = "warning"
        write = "warning"
    else:
        read = "good"
        write = "good"

    cleaning = "warning" if _drive_needs_cleaning(drive) else "good"
    overall = "failed" if "failed" in {read, write, connectivity} else "warning" if "warning" in {read, write, cleaning, connectivity} else "good"
    return DriveStatus(overall=overall, read=read, write=write, cleaning=cleaning, connectivity=connectivity)


def _loaded_media(drive: dict[str, Any]) -> MediaResource | None:
    loaded = drive.get("loadedMedia")
    if loaded is None:
        return None
    return MediaResource.model_validate(loaded)


def _drive_history(drive: dict[str, Any]) -> list[HistoryEvent]:
    history = drive.get("history")
    if isinstance(history, list):
        return [HistoryEvent.model_validate(item) for item in history]
    events: list[HistoryEvent] = []
    if drive.get("lastCleaned"):
        events.append(HistoryEvent(timestamp=str(drive["lastCleaned"]), type="clean", media=None, result="success", errorCode=None))
    return events


def _drive_errors(drive: dict[str, Any]) -> list[DriveError]:
    errors = drive.get("errors")
    if not isinstance(errors, list):
        return []
    return [DriveError.model_validate(item) for item in errors]


def _drive_stats(drive: dict[str, Any]) -> DriveStats:
    statistics = drive.get("statistics") if isinstance(drive.get("statistics"), dict) else {}
    load_count = int(drive.get("loadCount", 0))
    loaded_media = drive.get("loadedMedia") if isinstance(drive.get("loadedMedia"), dict) else None
    last_loaded = statistics.get("lastLoaded") or (loaded_media or {}).get("lastLoaded")
    return DriveStats(
        loadCount=load_count,
        unloadCount=int(statistics.get("unloadCount", load_count - (1 if loaded_media else 0))),
        errorCount=int(drive.get("errorCount", 0)),
        readErrors=int(statistics.get("readErrors", drive.get("errorCount", 0))),
        writeErrors=int(statistics.get("writeErrors", drive.get("errorCount", 0))),
        cleaningCount=int(drive.get("cleaningCount", 0)),
        totalHours=float(statistics.get("totalHours", round(load_count * 12.5, 1))),
        lastLoaded=last_loaded,
    )


def _firmware_info(drive: dict[str, Any]) -> FirmwareInfo:
    current = str(drive.get("firmware", "unknown"))
    firmware_info = drive.get("firmwareInfo") if isinstance(drive.get("firmwareInfo"), dict) else {}
    available = str(firmware_info.get("available") or ("H3J5" if current == "H3J4" else current))
    return FirmwareInfo(current=current, available=available, updateRequired=current != available)


def _diagnostic_result(drive: dict[str, Any]) -> DiagnosticResult:
    result = drive.get("diagnosticResult")
    if not isinstance(result, dict):
        return DiagnosticResult(timestamp=None, status="notRun", tests=[])
    return DiagnosticResult.model_validate(result)


def _drive_config(drive: dict[str, Any]) -> DriveConfig:
    config = drive.get("config") if isinstance(drive.get("config"), dict) else {}
    return DriveConfig.model_validate(
        {
            "compression": bool(config.get("compression", True)),
            "encryption": bool(config.get("encryption", False)),
            "speed": str(config.get("speed", "400MB/s")),
            "bufferSize": str(config.get("bufferSize", "256MB")),
        }
    )


def _fleet_status(drives: list[dict[str, Any]]) -> FleetStatus:
    return FleetStatus(
        total=len(drives),
        online=sum(1 for drive in drives if str(drive.get("status", "")).lower() == "online"),
        offline=sum(1 for drive in drives if str(drive.get("status", "")).lower() == "offline"),
        error=sum(1 for drive in drives if _drive_status(drive).overall == "failed"),
        cleaning=sum(1 for drive in drives if _drive_needs_cleaning(drive)),
    )


def _update_drive(serial_number: str, updates: dict[str, Any]) -> dict[str, Any]:
    updated = aml_state.update_aml_drive(serial_number, updates)
    if updated is None:
        raise HTTPException(status_code=404, detail="Drive not found")
    return updated


def _append_history(drive: dict[str, Any], *, event_type: str, media: str | None = None, result: str = "success", error_code: str | None = None) -> list[dict[str, Any]]:
    history = [item.model_dump() for item in _drive_history(drive)]
    history.insert(0, HistoryEvent(timestamp=_timestamp(), type=event_type, media=media, result=result, errorCode=error_code).model_dump())
    return history[:50]


@router.get("/drives/status", response_model=FleetStatusResponse)
async def get_fleet_status(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> FleetStatusResponse:
    _ensure_state(context)
    drives = _scoped_drives(context)
    return FleetStatusResponse(fleetStatus=_fleet_status(drives))


@router.post("/drives/clean/all", response_model=WSResultCode)
async def clean_all_drives(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    cleaned = 0
    for drive in _scoped_drives(context):
        if not _drive_needs_cleaning(drive):
            continue
        cleaned += 1
        history = _append_history(drive, event_type="clean", media=(_loaded_media(drive).barcode if _loaded_media(drive) else None))
        _update_drive(
            str(drive["serialNumber"]),
            {
                "cleaningCount": int(drive.get("cleaningCount", 0)) + 1,
                "lastCleaned": _timestamp(),
                "cleaningRequired": False,
                "state": "idle" if str(drive.get("status", "online")).lower() == "online" else str(drive.get("state", "idle")),
                "history": history,
            },
        )
    return _ws_result(f"Cleaned {cleaned} drive(s)")


@router.get("/drives/types", response_model=TypeListResponse)
async def list_drive_types(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> TypeListResponse:
    _ensure_state(context)
    types = dict(_SUPPORTED_DRIVE_TYPES)
    for drive in _scoped_drives(context):
        drive_type = str(drive.get("type", "")).strip()
        if drive_type and drive_type not in types:
            types[drive_type] = {
                "name": drive_type,
                "description": f"{drive_type} tape drive",
                "speeds": [str(_drive_config(drive).speed)],
                "generations": [drive_type],
            }
    return TypeListResponse(typeList=TypeListResource(type=[DriveType.model_validate(item) for _, item in sorted(types.items())]))


@router.get("/drives/cleaning", response_model=DriveListResponse)
async def list_drives_needing_cleaning(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> DriveListResponse:
    _ensure_state(context)
    drives = [_serialize_drive(drive) for drive in _scoped_drives(context) if _drive_needs_cleaning(drive)]
    return DriveListResponse(driveList=DriveListResource(drive=drives))


@router.get("/drives", response_model=DriveListResponse)
async def list_drives(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> DriveListResponse:
    _ensure_state(context)
    return DriveListResponse(driveList=DriveListResource(drive=[_serialize_drive(drive) for drive in _scoped_drives(context)]))


@router.get("/drive/{serialNumber}/status", response_model=DriveStatusResponse)
async def get_drive_status(
    serialNumber: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> DriveStatusResponse:
    _ensure_state(context)
    serial_number = _validate_identifier(serialNumber, field_name="serialNumber")
    return DriveStatusResponse(driveStatus=_drive_status(_get_drive_or_404(serial_number, context)))


@router.post("/drive/{serialNumber}/online", response_model=WSResultCode)
async def bring_drive_online(
    serialNumber: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    serial_number = _validate_identifier(serialNumber, field_name="serialNumber")
    drive = _get_drive_or_404(serial_number, context)
    _update_drive(serial_number, {"status": "online", "state": "idle", "history": _append_history(drive, event_type="online")})
    return _ws_result(f"Drive {serial_number} is online")


@router.post("/drive/{serialNumber}/offline", response_model=WSResultCode)
async def take_drive_offline(
    serialNumber: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    serial_number = _validate_identifier(serialNumber, field_name="serialNumber")
    drive = _get_drive_or_404(serial_number, context)
    _update_drive(serial_number, {"status": "offline", "state": "offline", "history": _append_history(drive, event_type="offline")})
    return _ws_result(f"Drive {serial_number} is offline")


@router.post("/drive/{serialNumber}/reset", response_model=WSResultCode)
async def reset_drive(
    serialNumber: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    serial_number = _validate_identifier(serialNumber, field_name="serialNumber")
    drive = _get_drive_or_404(serial_number, context)
    _update_drive(serial_number, {"status": "online", "state": "idle", "history": _append_history(drive, event_type="reset")})
    return _ws_result(f"Drive {serial_number} reset completed")


@router.post("/drive/{serialNumber}/clean", response_model=WSResultCode)
async def clean_drive(
    serialNumber: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    serial_number = _validate_identifier(serialNumber, field_name="serialNumber")
    drive = _get_drive_or_404(serial_number, context)
    loaded_media = _loaded_media(drive)
    _update_drive(
        serial_number,
        {
            "cleaningCount": int(drive.get("cleaningCount", 0)) + 1,
            "lastCleaned": _timestamp(),
            "cleaningRequired": False,
            "state": "idle",
            "history": _append_history(drive, event_type="clean", media=loaded_media.barcode if loaded_media else None),
        },
    )
    return _ws_result(f"Drive {serial_number} cleaning started")


@router.get("/drive/{serialNumber}/media", response_model=MediaResponse)
async def get_drive_media(
    serialNumber: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> MediaResponse:
    _ensure_state(context)
    serial_number = _validate_identifier(serialNumber, field_name="serialNumber")
    return MediaResponse(media=_loaded_media(_get_drive_or_404(serial_number, context)))


@router.post("/drive/{serialNumber}/unload", response_model=WSResultCode, dependencies=[Depends(require_service_token)])
async def unload_drive_media(
    serialNumber: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    serial_number = _validate_identifier(serialNumber, field_name="serialNumber")
    drive = _get_drive_or_404(serial_number, context)
    loaded_media = _loaded_media(drive)
    statistics = dict(drive.get("statistics") or {})
    statistics["unloadCount"] = int(statistics.get("unloadCount", int(drive.get("loadCount", 0)) - (1 if loaded_media else 0))) + (1 if loaded_media else 0)
    _update_drive(
        serial_number,
        {
            "loadedMedia": None,
            "state": "idle",
            "statistics": statistics,
            "history": _append_history(drive, event_type="unload", media=loaded_media.barcode if loaded_media else None),
        },
    )
    # Sync media record: move it back to its home slot as stored
    if loaded_media:
        barcode = loaded_media.barcode
        media_obj = aml_state.get_aml_media(barcode) or {}
        home_slot = media_obj.get("homeSlot") or media_obj.get("previousSlot") or f"slot-{barcode}"
        aml_state.update_aml_media(barcode, {"state": "stored", "slotAddress": home_slot})
    return _ws_result(f"Drive {serial_number} media unloaded")


@router.get("/drive/{serialNumber}/statistics", response_model=DriveStatsResponse)
async def get_drive_statistics(
    serialNumber: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> DriveStatsResponse:
    _ensure_state(context)
    serial_number = _validate_identifier(serialNumber, field_name="serialNumber")
    return DriveStatsResponse(driveStats=_drive_stats(_get_drive_or_404(serial_number, context)))


@router.get("/drive/{serialNumber}/history", response_model=HistoryListResponse)
async def get_drive_history(
    serialNumber: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> HistoryListResponse:
    _ensure_state(context)
    serial_number = _validate_identifier(serialNumber, field_name="serialNumber")
    return HistoryListResponse(historyList=HistoryListResource(event=_drive_history(_get_drive_or_404(serial_number, context))))


@router.get("/drive/{serialNumber}/errors", response_model=ErrorListResponse)
async def get_drive_errors(
    serialNumber: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> ErrorListResponse:
    _ensure_state(context)
    serial_number = _validate_identifier(serialNumber, field_name="serialNumber")
    return ErrorListResponse(errorList=ErrorListResource(error=_drive_errors(_get_drive_or_404(serial_number, context))))


@router.delete("/drive/{serialNumber}/errors", response_model=WSResultCode)
async def clear_drive_errors(
    serialNumber: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    serial_number = _validate_identifier(serialNumber, field_name="serialNumber")
    drive = _get_drive_or_404(serial_number, context)
    _update_drive(serial_number, {"errors": [], "errorCount": 0, "history": _append_history(drive, event_type="clearErrors")})
    return _ws_result(f"Cleared drive errors for {serial_number}")


@router.post("/drive/{serialNumber}/diagnostic", response_model=WSResultCode)
async def run_drive_diagnostic(
    serialNumber: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    serial_number = _validate_identifier(serialNumber, field_name="serialNumber")
    drive = _get_drive_or_404(serial_number, context)
    diagnostic = DiagnosticResult(
        timestamp=_timestamp(),
        status="completed",
        tests=[
            DiagnosticTest(name="readPath", result="passed", details="Read path healthy"),
            DiagnosticTest(name="writePath", result="passed", details="Write path healthy"),
            DiagnosticTest(name="connectivity", result="passed", details="SAS/FC link stable"),
        ],
    )
    _update_drive(serial_number, {"diagnosticResult": diagnostic.model_dump(), "history": _append_history(drive, event_type="diagnostic")})
    return _ws_result(f"Drive {serial_number} diagnostic completed")


@router.get("/drive/{serialNumber}/diagnostic/results", response_model=DiagnosticResponse)
async def get_drive_diagnostic_results(
    serialNumber: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> DiagnosticResponse:
    _ensure_state(context)
    serial_number = _validate_identifier(serialNumber, field_name="serialNumber")
    return DiagnosticResponse(diagnosticResult=_diagnostic_result(_get_drive_or_404(serial_number, context)))


@router.get("/drive/{serialNumber}/config", response_model=DriveConfigResponse)
async def get_drive_config(
    serialNumber: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> DriveConfigResponse:
    _ensure_state(context)
    serial_number = _validate_identifier(serialNumber, field_name="serialNumber")
    return DriveConfigResponse(driveConfig=_drive_config(_get_drive_or_404(serial_number, context)))


@router.put("/drive/{serialNumber}/config", response_model=DriveConfigResponse)
async def update_drive_config(
    serialNumber: str,
    payload: DriveConfigUpdateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> DriveConfigResponse:
    _ensure_state(context)
    _require_admin(current_user)
    serial_number = _validate_identifier(serialNumber, field_name="serialNumber")
    drive = _get_drive_or_404(serial_number, context)
    config = dict(drive.get("config") or {})
    config.update(_validate_drive_config(payload.driveConfig))
    updated = _update_drive(serial_number, {"config": config})
    return DriveConfigResponse(driveConfig=_drive_config(updated))


@router.get("/drives/logs", response_model=dict[str, list[dict[str, Any]]])
async def get_drive_logs(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, list[dict[str, Any]]]:
    _ensure_state(context)
    logs: list[dict[str, Any]] = []
    for drive in _scoped_drives(context):
        serial = str(drive.get("serialNumber"))
        logs.extend({"serialNumber": serial, **item.model_dump()} for item in _drive_history(drive))
        logs.extend(
            {
                "serialNumber": serial,
                "timestamp": item.timestamp,
                "type": item.type,
                "result": "error",
                "errorCode": item.code,
                "description": item.description,
            }
            for item in _drive_errors(drive)
        )
    return {"logs": logs[:100]}


@router.get("/drives/ports", response_model=dict[str, list[dict[str, Any]]])
async def get_drive_ports(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, list[dict[str, Any]]]:
    _ensure_state(context)
    ports = [
        {
            "serialNumber": str(drive.get("serialNumber")),
            "hardwareSerialNumber": str(drive.get("hardwareSerialNumber", drive.get("serialNumber"))),
            "location": str(drive.get("location", "unknown")),
            "portType": "fibre-channel",
            "speed": "16G",
            "status": str(drive.get("status", "online")),
        }
        for drive in _scoped_drives(context)
    ]
    return {"ports": ports}


@router.post("/drives/powerCycle", status_code=status.HTTP_202_ACCEPTED)
async def power_cycle_drive(
    payload: dict[str, Any],
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    _ensure_state(context)
    _require_admin(current_user)
    serial_number = _validate_identifier(str(payload.get("serialNumber", "")), field_name="serialNumber")
    drive = _get_drive_or_404(serial_number, context)
    _update_drive(serial_number, {"state": "resetting", "history": _append_history(drive, event_type="powerCycle")})
    return _job_response("drive-power-cycle", f"Power cycle queued for drive {serial_number}", {"serialNumber": serial_number})


@router.get("/drives/reports/activity", response_model=dict[str, Any])
async def get_drive_activity_report(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    _ensure_state(context)
    items = [
        {
            "serialNumber": str(drive.get("serialNumber")),
            "loadCount": int(drive.get("loadCount", 0)),
            "errorCount": int(drive.get("errorCount", 0)),
            "lastCleaned": drive.get("lastCleaned"),
        }
        for drive in _scoped_drives(context)
    ]
    return {"generatedAt": _timestamp(), "drives": items}


@router.get("/drives/reports/utilization", response_model=dict[str, Any])
async def get_drive_utilization_report(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    _ensure_state(context)
    items = []
    for drive in _scoped_drives(context):
        load_count = int(drive.get("loadCount", 0))
        items.append(
            {
                "serialNumber": str(drive.get("serialNumber")),
                "utilizationPercent": min(load_count, 100),
                "cleaningRequired": _drive_needs_cleaning(drive),
                "active": drive.get("loadedMedia") is not None,
            }
        )
    return {"generatedAt": _timestamp(), "drives": items}


@router.get("/drive/{serialNumber}/operations/state", response_model=dict[str, Any])
async def get_drive_operation_state(
    serialNumber: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    _ensure_state(context)
    serial_number = _validate_identifier(serialNumber, field_name="serialNumber")
    drive = _get_drive_or_404(serial_number, context)
    return {"serialNumber": serial_number, "state": str(drive.get("state", "idle")), "status": str(drive.get("status", "online"))}


@router.put("/drive/{serialNumber}/operations/state", response_model=dict[str, Any])
async def put_drive_operation_state(
    serialNumber: str,
    payload: dict[str, Any],
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    _ensure_state(context)
    _require_admin(current_user)
    serial_number = _validate_identifier(serialNumber, field_name="serialNumber")
    drive = _get_drive_or_404(serial_number, context)
    updates = {key: value for key, value in payload.items() if key in {"state", "status"} and value is not None}
    updated = _update_drive(serial_number, {**{k: drive.get(k) for k in ()}, **updates})
    return {"serialNumber": serial_number, "state": str(updated.get("state", "idle")), "status": str(updated.get("status", "online"))}


@router.post("/drives/firmware/operations/update", status_code=status.HTTP_202_ACCEPTED)
async def update_drive_firmware_operation(
    payload: dict[str, Any] | None = None,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    _ensure_state(context)
    _require_admin(current_user)
    return _job_response("drive-firmware-update", "Drive firmware update queued", payload or {})


@router.get("/drive/{serialNumber}", response_model=DriveResponse)
async def get_drive(
    serialNumber: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> DriveResponse:
    _ensure_state(context)
    serial_number = _validate_identifier(serialNumber, field_name="serialNumber")
    return DriveResponse(drive=_serialize_drive(_get_drive_or_404(serial_number, context)))


@router.put("/drive/{serialNumber}", response_model=DriveResponse)
async def update_drive(
    serialNumber: str,
    payload: DriveUpdateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> DriveResponse:
    _ensure_state(context)
    _require_admin(current_user)
    serial_number = _validate_identifier(serialNumber, field_name="serialNumber")
    updated = _update_drive(serial_number, _validate_drive_patch(payload.drive))
    return DriveResponse(drive=_serialize_drive(updated))
