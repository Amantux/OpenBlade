"""AML firmware management routes."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from openblade.api import aml_state
from openblade.api.routes_aml_auth import WSResultCode, _ensure_state, _require_admin, require_auth
from openblade.bootstrap import AppContext, get_context
from openblade.catalog.models import AmlUser

router = APIRouter()

_VALID_DRIVE_FIRMWARE_EXTENSIONS = {".drv", ".fmr", ".fmrz", ".img", ".ro", ".e", ".frm"}


class BladeFirmwareItem(BaseModel):
    name: str
    target: str
    version: str
    status: str
    uploadedAt: str
    size: int
    checksum: str | None = None


class BladeFirmwareListResource(BaseModel):
    firmware: list[BladeFirmwareItem]


class BladeFirmwareListResponse(BaseModel):
    bladeFirmwareList: BladeFirmwareListResource


class DriveFirmwareImage(BaseModel):
    name: str
    version: str
    driveType: str
    extension: str
    size: int
    uploadedAt: str
    checksum: str | None = None
    active: bool = False


class DriveFirmwareImageListResource(BaseModel):
    image: list[DriveFirmwareImage]


class DriveFirmwareImageListResponse(BaseModel):
    firmwareImageList: DriveFirmwareImageListResource


class DriveFirmwareDetails(BaseModel):
    current: str
    available: str
    activeImage: str | None = None
    updateRequired: bool
    lastUpdated: str | None = None


class DriveFirmwareResponse(BaseModel):
    firmware: DriveFirmwareDetails


class DriveFirmwareUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image: str | None = None


class DriveFirmwareUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    firmware: DriveFirmwareUpdatePayload = Field(default_factory=DriveFirmwareUpdatePayload)


class SystemFirmwarePackage(BaseModel):
    name: str
    version: str
    size: int
    uploadedAt: str
    checksum: str | None = None
    active: bool = False


class SystemFirmwareInfo(BaseModel):
    currentVersion: str
    stagedVersion: str | None = None
    stagedPackage: str | None = None
    status: str
    lastActivated: str | None = None
    package: list[SystemFirmwarePackage] = Field(default_factory=list)


class SystemFirmwareResponse(BaseModel):
    systemFirmware: SystemFirmwareInfo


class SystemFirmwareStatus(BaseModel):
    state: str
    progress: int
    message: str
    currentVersion: str
    stagedVersion: str | None = None
    lastUpdated: str
    lastActivated: str | None = None


class SystemFirmwareStatusResponse(BaseModel):
    firmwareStatus: SystemFirmwareStatus


class SystemFirmwareActivatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    commit: bool = True


class SystemFirmwareActivateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    firmware: SystemFirmwareActivatePayload = Field(default_factory=SystemFirmwareActivatePayload)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _ws_result(summary: str = "Operation completed") -> WSResultCode:
    return WSResultCode(summary=summary)


def _validate_identifier(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail=f"{field_name} is required")
    return normalized


def _guess_version(filename: str) -> str:
    """Extract a human-readable version string from a firmware filename.

    Handles formats like:
      - lto9_fw_D9.1.0.img  → D9.1.0
      - lto9_d9_1_0.img     → D9.1.0
      - drive_fw_9_0_5.drv  → 9.0.5
      - firmware-1.2.3.fmr  → 1.2.3
    """
    stem = Path(filename).stem
    # Prefer an explicit dotted version string (e.g. D9.1.0, 9.0.5)
    dotted = re.search(r"([A-Za-z]*\d+(?:\.\d+)+)", stem)
    if dotted:
        v = dotted.group(1)
        return v.upper() if v[0].isalpha() else v
    # Try underscore/dash-separated segments: collect a letter+digit prefix followed
    # by consecutive pure-numeric tokens, OR a run of consecutive pure-numeric tokens ≥ 2
    tokens = re.split(r"[_\-]", stem)
    best: list[str] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if re.fullmatch(r"[A-Za-z]\d+", t):
            # letter-prefixed version seed (e.g. d9)
            run = [t.upper()]
            j = i + 1
            while j < len(tokens) and re.fullmatch(r"\d+", tokens[j]):
                run.append(tokens[j])
                j += 1
            if len(run) > len(best):
                best = run
            i = j
        elif re.fullmatch(r"\d+", t):
            # pure-numeric run
            run = [t]
            j = i + 1
            while j < len(tokens) and re.fullmatch(r"\d+", tokens[j]):
                run.append(tokens[j])
                j += 1
            if len(run) >= 2 and len(run) > len(best):
                best = run
            i = j
        else:
            i += 1
    if best:
        return ".".join(best)
    # Last resort: first token that contains a digit
    for token in tokens:
        if any(c.isdigit() for c in token):
            return token.upper() if token[0].isalpha() else token
    return stem or "unknown"


def _blade_target(filename: str) -> str:
    normalized = filename.lower()
    if "mgmt" in normalized or "management" in normalized or "iblade" in normalized:
        return "management"
    if "eth" in normalized:
        return "ethernet"
    if "fc" in normalized or "fibre" in normalized or "fiber" in normalized:
        return "fibre-channel"
    return "mixed"


def _metadata(filename: str, content: bytes, **extras: Any) -> dict[str, Any]:
    item = {
        "name": filename,
        "size": len(content),
        "uploadedAt": _timestamp(),
        "checksum": hashlib.sha256(content).hexdigest()[:16],
    }
    item.update(extras)
    return item


def _get_drive_image_or_404(name: str) -> dict[str, Any]:
    image = aml_state.get_drive_firmware_image(name)
    if image is None:
        raise HTTPException(status_code=404, detail="Drive firmware image not found")
    return image


def _active_drive_image() -> dict[str, Any] | None:
    for image in aml_state.list_drive_firmware_images():
        if bool(image.get("active", False)):
            return image
    return None


def _get_drive_or_404(serial_number: str) -> dict[str, Any]:
    drive = aml_state.get_aml_drive(serial_number)
    if drive is None:
        raise HTTPException(status_code=404, detail="Drive not found")
    return drive


def _append_drive_history(drive: dict[str, Any], *, event_type: str) -> list[dict[str, Any]]:
    history = list(drive.get("history") or [])
    history.insert(
        0,
        {
            "timestamp": _timestamp(),
            "type": event_type,
            "media": None,
            "result": "success",
            "errorCode": None,
        },
    )
    return history[:50]


def _drive_firmware_response(drive: dict[str, Any]) -> DriveFirmwareResponse:
    active_image = _active_drive_image()
    current = str(drive.get("firmware", "unknown"))
    available = str((active_image or {}).get("version") or drive.get("firmware", "unknown"))
    return DriveFirmwareResponse(
        firmware=DriveFirmwareDetails(
            current=current,
            available=available,
            activeImage=(str((active_image or {}).get("name")) if active_image else None),
            updateRequired=current != available,
            lastUpdated=drive.get("firmwareUpdatedAt"),
        )
    )


def _system_firmware_response() -> SystemFirmwareResponse:
    info = aml_state.get_system_firmware_info()
    staged_package = info.get("stagedPackage") if isinstance(info.get("stagedPackage"), dict) else None
    packages = [SystemFirmwarePackage.model_validate(item) for item in info.get("uploadedPackages", [])]
    return SystemFirmwareResponse(
        systemFirmware=SystemFirmwareInfo(
            currentVersion=str(info.get("currentVersion", "unknown")),
            stagedVersion=(str(staged_package.get("version")) if staged_package else None),
            stagedPackage=(str(staged_package.get("name")) if staged_package else None),
            status=str((info.get("status") or {}).get("state", "idle")),
            lastActivated=info.get("lastActivated"),
            package=packages,
        )
    )


def _system_firmware_status_response() -> SystemFirmwareStatusResponse:
    info = aml_state.get_system_firmware_info()
    status = dict(info.get("status") or {})
    return SystemFirmwareStatusResponse(
        firmwareStatus=SystemFirmwareStatus.model_validate(
            {
                "state": status.get("state", "idle"),
                "progress": int(status.get("progress", 0)),
                "message": status.get("message", "No firmware activation pending"),
                "currentVersion": status.get("currentVersion") or info.get("currentVersion", "unknown"),
                "stagedVersion": status.get("stagedVersion"),
                "lastUpdated": status.get("lastUpdated") or _timestamp(),
                "lastActivated": status.get("lastActivated") or info.get("lastActivated"),
            }
        )
    )


@router.get("/devices/blades/firmware", response_model=BladeFirmwareListResponse)
async def list_blade_firmware(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> BladeFirmwareListResponse:
    _ensure_state(context)
    firmware = [BladeFirmwareItem.model_validate(item) for item in aml_state.list_blade_firmware()]
    return BladeFirmwareListResponse(bladeFirmwareList=BladeFirmwareListResource(firmware=firmware))


@router.post("/devices/blades/firmware", response_model=WSResultCode)
async def upload_blade_firmware(
    file: UploadFile = File(...),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    try:
        filename = file.filename or "blade-firmware.bundle"
        content = await file.read()
        aml_state.upsert_blade_firmware(
            _metadata(
                filename,
                content,
                target=_blade_target(filename),
                version=_guess_version(filename),
                status="available",
            )
        )
    finally:
        await file.close()
    return _ws_result(f"Blade firmware bundle {filename} uploaded")


@router.get("/drives/firmware/images", response_model=DriveFirmwareImageListResponse)
async def list_drive_firmware_images(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> DriveFirmwareImageListResponse:
    _ensure_state(context)
    images = [DriveFirmwareImage.model_validate(item) for item in aml_state.list_drive_firmware_images()]
    return DriveFirmwareImageListResponse(firmwareImageList=DriveFirmwareImageListResource(image=images))


@router.post("/drives/firmware/images", response_model=WSResultCode)
async def upload_drive_firmware_image(
    file: UploadFile = File(...),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    try:
        filename = file.filename or "drive-firmware.img"
        extension = Path(filename).suffix.lower()
        if extension not in _VALID_DRIVE_FIRMWARE_EXTENSIONS:
            raise HTTPException(status_code=400, detail="Unsupported drive firmware file extension")
        content = await file.read()
        aml_state.upsert_drive_firmware_image(
            _metadata(
                filename,
                content,
                version=_guess_version(filename),
                driveType="LTO-9",
                extension=extension,
                active=False,
            )
        )
    finally:
        await file.close()
    return _ws_result(f"Drive firmware image {filename} uploaded")


@router.put("/drives/firmware/images/{name}/activate", response_model=WSResultCode)
async def activate_drive_firmware_image(
    name: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    image_name = _validate_identifier(name, field_name="name")
    image = _get_drive_image_or_404(image_name)
    aml_state.activate_drive_firmware_image(image_name)
    for drive in aml_state.list_aml_drives():
        aml_state.update_aml_drive(
            str(drive.get("serialNumber")),
            {
                "firmware": image["version"],
                "firmwareInfo": {"available": image["version"]},
                "firmwareImage": image_name,
                "firmwareUpdatedAt": _timestamp(),
                "history": _append_drive_history(drive, event_type="firmwareActivate"),
            },
        )
    return _ws_result(f"Activated drive firmware image {image_name}")


@router.delete("/drives/firmware/image/{name}", response_model=WSResultCode)
async def delete_drive_firmware_image(
    name: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    image_name = _validate_identifier(name, field_name="name")
    if not aml_state.delete_drive_firmware_image(image_name):
        raise HTTPException(status_code=404, detail="Drive firmware image not found")
    return _ws_result(f"Deleted drive firmware image {image_name}")


@router.get("/system/firmware/status", response_model=SystemFirmwareStatusResponse)
async def get_system_firmware_status(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> SystemFirmwareStatusResponse:
    _ensure_state(context)
    return _system_firmware_status_response()


@router.get("/system/firmware", response_model=SystemFirmwareResponse)
async def get_system_firmware(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> SystemFirmwareResponse:
    _ensure_state(context)
    return _system_firmware_response()


@router.post("/system/firmware", response_model=WSResultCode)
async def upload_system_firmware(
    file: UploadFile = File(...),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    try:
        filename = file.filename or "system-firmware.pkg"
        content = await file.read()
        package = _metadata(filename, content, version=_guess_version(filename), active=False)
    finally:
        await file.close()
    info = aml_state.get_system_firmware_info()
    packages = [item for item in info.get("uploadedPackages", []) if str(item.get("name")) != filename]
    packages.append(package)
    info["uploadedPackages"] = packages
    info["stagedPackage"] = package
    info["status"] = {
        "state": "uploaded",
        "progress": 100,
        "message": f"Firmware package {filename} uploaded",
        "currentVersion": info.get("currentVersion", "unknown"),
        "stagedVersion": package["version"],
        "lastUpdated": _timestamp(),
        "lastActivated": info.get("lastActivated"),
    }
    aml_state.set_system_firmware_info(info)
    return _ws_result(f"System firmware package {filename} uploaded")


@router.put("/system/firmware/activate", response_model=WSResultCode)
async def activate_system_firmware(
    payload: SystemFirmwareActivateRequest | None = Body(default=None),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    if payload is not None and not payload.firmware.commit:
        return _ws_result("System firmware activation skipped")
    info = aml_state.get_system_firmware_info()
    staged_package = info.get("stagedPackage") if isinstance(info.get("stagedPackage"), dict) else None
    if staged_package is None:
        raise HTTPException(status_code=409, detail="No staged system firmware available")
    activated_at = _timestamp()
    updated_packages: list[dict[str, Any]] = []
    for package in info.get("uploadedPackages", []):
        current = dict(package)
        current["active"] = str(current.get("name")) == str(staged_package.get("name"))
        updated_packages.append(current)
    info["currentVersion"] = staged_package["version"]
    info["uploadedPackages"] = updated_packages
    info["stagedPackage"] = None
    info["lastActivated"] = activated_at
    info["status"] = {
        "state": "completed",
        "progress": 100,
        "message": f"Activated system firmware {staged_package['name']}",
        "currentVersion": staged_package["version"],
        "stagedVersion": None,
        "lastUpdated": activated_at,
        "lastActivated": activated_at,
    }
    aml_state.set_system_firmware_info(info)
    return _ws_result(f"Activated system firmware {staged_package['name']}")


@router.get("/drive/{serialNumber}/firmware", response_model=DriveFirmwareResponse)
async def get_drive_firmware(
    serialNumber: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> DriveFirmwareResponse:
    _ensure_state(context)
    serial_number = _validate_identifier(serialNumber, field_name="serialNumber")
    return _drive_firmware_response(_get_drive_or_404(serial_number))


@router.put("/drive/{serialNumber}/firmware", response_model=WSResultCode)
async def update_drive_firmware(
    serialNumber: str,
    payload: DriveFirmwareUpdateRequest | None = Body(default=None),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    serial_number = _validate_identifier(serialNumber, field_name="serialNumber")
    drive = _get_drive_or_404(serial_number)
    image_name = payload.firmware.image if payload is not None else None
    if image_name is None:
        active_image = _active_drive_image()
        image_name = str((active_image or {}).get("name") or drive.get("firmwareImage") or "").strip() or None
    if image_name is None:
        raise HTTPException(status_code=409, detail="No drive firmware image available")
    image = _get_drive_image_or_404(_validate_identifier(image_name, field_name="image"))
    aml_state.update_aml_drive(
        serial_number,
        {
            "firmware": image["version"],
            "firmwareInfo": {"available": image["version"]},
            "firmwareImage": image["name"],
            "firmwareUpdatedAt": _timestamp(),
            "history": _append_drive_history(drive, event_type="firmwareUpdate"),
        },
    )
    return _ws_result(f"Drive {serial_number} firmware updated to {image['version']}")
