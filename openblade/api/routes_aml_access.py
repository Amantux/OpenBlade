"""AML access groups, hosts, and license routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from openblade.api import aml_state
from openblade.api.routes_aml_auth import WSResultCode, _ensure_state, _require_admin, require_auth
from openblade.bootstrap import AppContext, get_context
from openblade.catalog.models import AmlUser

router = APIRouter()


class Device(BaseModel):
    model_config = ConfigDict(extra="allow")

    serialNumber: str
    type: str | None = None
    location: str | None = None
    partition: str | None = None


class DeviceList(BaseModel):
    device: list[Device]


class DeviceReference(BaseModel):
    serialNumber: str


class AccessGroup(BaseModel):
    name: str
    devices: list[str]
    hosts: list[str]


class AccessGroupList(BaseModel):
    accessGroup: list[AccessGroup]


class AccessGroupEnvelope(BaseModel):
    accessGroup: AccessGroup


class Host(BaseModel):
    WWPN: str
    alias: str | None = None
    groups: list[str] = Field(default_factory=list)


class HostCreateRequest(BaseModel):
    WWPN: str
    alias: str | None = None


class HostUpdateRequest(BaseModel):
    alias: str | None = None


class HostReference(BaseModel):
    WWPN: str


class HostList(BaseModel):
    host: list[Host]


class HostEnvelope(BaseModel):
    host: Host


class License(BaseModel):
    serialNumber: str
    type: str | None = None
    description: str | None = None
    status: str | None = None
    feature: str | None = None
    expiry: str | None = None


class LicenseList(BaseModel):
    license: list[License]


class LicenseEnvelope(BaseModel):
    license: License


def _ws_result(summary: str) -> WSResultCode:
    return WSResultCode(summary=summary)


def _validate_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="Group name is required")
    return normalized


def _validate_identifier(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail=f"{field_name} is required")
    return normalized


def _available_devices(context: AppContext) -> list[dict[str, str | None]]:
    inventory = context.library.inventory()
    return [
        {
            "serialNumber": f"DRV-{drive.drive_id:03d}",
            "type": "drive",
            "location": f"drive-{drive.drive_id}",
            "partition": None,
        }
        for drive in inventory.drives
    ]


def _available_device_map(context: AppContext) -> dict[str, dict[str, str | None]]:
    devices = _available_devices(context)
    return {device["serialNumber"]: device for device in devices}


def _serialize_access_group(group: dict[str, object]) -> AccessGroup:
    return AccessGroup.model_validate(group)


def _serialize_host(host: dict[str, object]) -> Host:
    return Host.model_validate(host)


def _serialize_license(license_item: dict[str, object]) -> License:
    return License.model_validate(license_item)


def _get_group_or_404(name: str) -> dict[str, object]:
    group = aml_state.get_access_group(name)
    if group is None:
        raise HTTPException(status_code=404, detail="Access group not found")
    return group


def _get_host_or_404(wwpn: str) -> dict[str, object]:
    host = aml_state.get_aml_host(wwpn)
    if host is None:
        raise HTTPException(status_code=404, detail="Host not found")
    return host


def _get_license_or_404(serial_number: str) -> dict[str, object]:
    license_item = aml_state.get_aml_license(serial_number)
    if license_item is None:
        raise HTTPException(status_code=404, detail="License not found")
    return license_item


def _require_group(name: str) -> str:
    return _validate_name(name)


def _require_device(context: AppContext, serial_number: str) -> str:
    normalized = _validate_identifier(serial_number, field_name="Serial number")
    if normalized not in _available_device_map(context):
        raise HTTPException(status_code=404, detail="Device not found")
    return normalized


def _require_admin_access(current_user: AmlUser) -> None:
    _require_admin(current_user)


@router.get("/access/devices", response_model=DeviceList)
async def list_devices(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> DeviceList:
    _ensure_state(context)
    _require_admin_access(current_user)
    return DeviceList(device=[Device.model_validate(item) for item in _available_devices(context)])


@router.get("/access/groups", response_model=AccessGroupList)
async def list_access_groups(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> AccessGroupList:
    _ensure_state(context)
    _require_admin_access(current_user)
    return AccessGroupList(accessGroup=[_serialize_access_group(item) for item in aml_state.list_access_groups()])


@router.get("/access/group/{name}", response_model=AccessGroupEnvelope)
async def get_access_group(
    name: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> AccessGroupEnvelope:
    _ensure_state(context)
    _require_admin_access(current_user)
    group_name = _require_group(name)
    return AccessGroupEnvelope(accessGroup=_serialize_access_group(_get_group_or_404(group_name)))


@router.post("/access/group/{name}", response_model=AccessGroupEnvelope, status_code=status.HTTP_201_CREATED)
async def create_access_group(
    name: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> AccessGroupEnvelope:
    _ensure_state(context)
    _require_admin_access(current_user)
    group_name = _require_group(name)
    group = aml_state.create_access_group(group_name)
    if group is None:
        raise HTTPException(status_code=409, detail="Access group already exists")
    return AccessGroupEnvelope(accessGroup=_serialize_access_group(group))


@router.delete("/access/group/{name}", response_model=WSResultCode)
async def delete_access_group(
    name: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin_access(current_user)
    group_name = _require_group(name)
    if not aml_state.delete_access_group(group_name):
        raise HTTPException(status_code=404, detail="Access group not found")
    return _ws_result(f"Deleted access group {group_name}")


@router.post("/access/group/{name}/device", response_model=WSResultCode)
async def add_device_to_group(
    name: str,
    payload: DeviceReference,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin_access(current_user)
    group_name = _require_group(name)
    _get_group_or_404(group_name)
    serial_number = _require_device(context, payload.serialNumber)
    aml_state.add_access_group_devices(group_name, [serial_number])
    return _ws_result(f"Added device {serial_number} to access group {group_name}")


@router.put("/access/group/{name}/device", response_model=WSResultCode)
async def update_group_device(
    name: str,
    payload: Device,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin_access(current_user)
    group_name = _require_group(name)
    _get_group_or_404(group_name)
    serial_number = _require_device(context, payload.serialNumber)
    aml_state.add_access_group_devices(group_name, [serial_number])
    return _ws_result(f"Updated device {serial_number} in access group {group_name}")


@router.delete("/access/group/{name}/device/{serialNumber}", response_model=WSResultCode)
async def remove_device_from_group(
    name: str,
    serialNumber: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin_access(current_user)
    group_name = _require_group(name)
    _get_group_or_404(group_name)
    serial_number = _validate_identifier(serialNumber, field_name="Serial number")
    if not aml_state.remove_access_group_device(group_name, serial_number):
        raise HTTPException(status_code=404, detail="Device not assigned to access group")
    return _ws_result(f"Removed device {serial_number} from access group {group_name}")


@router.get("/access/group/{name}/devices", response_model=DeviceList)
async def list_group_devices(
    name: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> DeviceList:
    _ensure_state(context)
    _require_admin_access(current_user)
    group_name = _require_group(name)
    group = _get_group_or_404(group_name)
    return DeviceList(device=[Device(serialNumber=serial_number) for serial_number in group.get("devices", [])])


@router.post("/access/group/{name}/devices", response_model=WSResultCode)
async def bulk_add_group_devices(
    name: str,
    payload: DeviceList,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin_access(current_user)
    group_name = _require_group(name)
    _get_group_or_404(group_name)
    serial_numbers = [_require_device(context, item.serialNumber) for item in payload.device]
    aml_state.add_access_group_devices(group_name, serial_numbers)
    return _ws_result(f"Added {len(serial_numbers)} devices to access group {group_name}")


@router.post("/access/group/{name}/hosts", response_model=WSResultCode)
async def add_host_to_group(
    name: str,
    payload: HostReference,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin_access(current_user)
    group_name = _require_group(name)
    _get_group_or_404(group_name)
    wwpn = _validate_identifier(payload.WWPN, field_name="WWPN")
    aml_state.add_host_to_access_group(group_name, wwpn)
    return _ws_result(f"Added host {wwpn} to access group {group_name}")


@router.delete("/access/group/{name}/hosts/{WWPN}", response_model=WSResultCode)
async def remove_host_from_group(
    name: str,
    WWPN: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin_access(current_user)
    group_name = _require_group(name)
    _get_group_or_404(group_name)
    wwpn = _validate_identifier(WWPN, field_name="WWPN")
    if not aml_state.remove_host_from_access_group(group_name, wwpn):
        raise HTTPException(status_code=404, detail="Host not assigned to access group")
    return _ws_result(f"Removed host {wwpn} from access group {group_name}")


@router.get("/access/host/{WWPN}", response_model=HostEnvelope)
async def get_host(
    WWPN: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> HostEnvelope:
    _ensure_state(context)
    _require_admin_access(current_user)
    wwpn = _validate_identifier(WWPN, field_name="WWPN")
    return HostEnvelope(host=_serialize_host(_get_host_or_404(wwpn)))


@router.put("/access/host/{WWPN}", response_model=HostEnvelope)
async def update_host(
    WWPN: str,
    payload: HostUpdateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> HostEnvelope:
    _ensure_state(context)
    _require_admin_access(current_user)
    wwpn = _validate_identifier(WWPN, field_name="WWPN")
    updated = aml_state.update_aml_host(wwpn, alias=payload.alias)
    if updated is None:
        raise HTTPException(status_code=404, detail="Host not found")
    return HostEnvelope(host=_serialize_host(updated))


@router.delete("/access/host/{WWPN}", response_model=WSResultCode)
async def delete_host(
    WWPN: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin_access(current_user)
    wwpn = _validate_identifier(WWPN, field_name="WWPN")
    if not aml_state.delete_aml_host(wwpn):
        raise HTTPException(status_code=404, detail="Host not found")
    return _ws_result(f"Deleted host {wwpn}")


@router.get("/access/hosts", response_model=HostList)
async def list_hosts(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> HostList:
    _ensure_state(context)
    _require_admin_access(current_user)
    return HostList(host=[_serialize_host(item) for item in aml_state.list_aml_hosts()])


@router.post("/access/hosts", response_model=HostEnvelope, status_code=status.HTTP_201_CREATED)
async def create_host(
    payload: HostCreateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> HostEnvelope:
    _ensure_state(context)
    _require_admin_access(current_user)
    wwpn = _validate_identifier(payload.WWPN, field_name="WWPN")
    host = aml_state.create_aml_host(wwpn, alias=payload.alias)
    if host is None:
        raise HTTPException(status_code=409, detail="Host already exists")
    return HostEnvelope(host=_serialize_host(host))


@router.get("/access/licenses", response_model=LicenseList)
async def list_licenses(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> LicenseList:
    _ensure_state(context)
    _require_admin_access(current_user)
    return LicenseList(license=[_serialize_license(item) for item in aml_state.list_aml_licenses()])


@router.put("/access/licenses", response_model=WSResultCode)
async def update_licenses(
    payload: LicenseList,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin_access(current_user)
    aml_state.set_aml_licenses([item.model_dump() for item in payload.license])
    return _ws_result(f"Updated {len(payload.license)} licenses")


@router.get("/access/license/{serialNumber}", response_model=LicenseEnvelope)
async def get_license(
    serialNumber: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> LicenseEnvelope:
    _ensure_state(context)
    _require_admin_access(current_user)
    serial_number = _validate_identifier(serialNumber, field_name="Serial number")
    return LicenseEnvelope(license=_serialize_license(_get_license_or_404(serial_number)))


@router.post("/access/license/{serialNumber}", response_model=WSResultCode)
async def activate_license(
    serialNumber: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin_access(current_user)
    serial_number = _validate_identifier(serialNumber, field_name="Serial number")
    if not aml_state.activate_aml_license(serial_number):
        raise HTTPException(status_code=404, detail="License not found")
    return _ws_result(f"Activated license {serial_number}")


@router.delete("/access/license/{serialNumber}", response_model=WSResultCode)
async def delete_license(
    serialNumber: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin_access(current_user)
    serial_number = _validate_identifier(serialNumber, field_name="Serial number")
    if not aml_state.delete_aml_license(serial_number):
        raise HTTPException(status_code=404, detail="License not found")
    return _ws_result(f"Deleted license {serial_number}")
