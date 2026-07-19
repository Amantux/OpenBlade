"""AML blade and device inventory routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from openblade.api import aml_state
from openblade.api.routes_aml_auth import WSResultCode, _ensure_state, _require_admin, require_auth
from openblade.bootstrap import AppContext, get_context
from openblade.catalog.models import AmlUser

router = APIRouter()


class EthPort(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    mac: str
    ip: str
    status: str
    speed: str | None = None
    duplex: str | None = None


class EthPortListResource(BaseModel):
    port: list[EthPort]


class EthPortListResponse(BaseModel):
    ethPortList: EthPortListResource


class EthPortResponse(BaseModel):
    ethPort: EthPort


class EthBlade(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    serialNumber: str
    model: str
    status: str
    firmware: str
    portCount: int
    ports: list[EthPort] = Field(default_factory=list)


class EthBladeListResource(BaseModel):
    ethBlade: list[EthBlade]


class EthBladeListResponse(BaseModel):
    ethBladeList: EthBladeListResource


class EthBladeResponse(BaseModel):
    ethBlade: EthBlade


class EthPortPatch(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    mac: str | None = None
    ip: str | None = None
    status: str | None = None
    speed: str | None = None
    duplex: str | None = None


class EthPortUpdateRequest(BaseModel):
    ethPort: EthPortPatch


class EthBladePatch(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    serialNumber: str | None = None
    model: str | None = None
    status: str | None = None
    firmware: str | None = None
    portCount: int | None = None
    ports: list[EthPortPatch] | None = None


class EthBladeUpdateRequest(BaseModel):
    ethBlade: EthBladePatch


class FcPort(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    wwpn: str
    speed: str
    status: str
    mode: str
    topology: str | None = None


class FcPortListResource(BaseModel):
    port: list[FcPort]


class FcPortListResponse(BaseModel):
    fcPortList: FcPortListResource


class FcPortResponse(BaseModel):
    fcPort: FcPort


class FcBlade(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    serialNumber: str
    model: str
    status: str
    firmware: str
    portCount: int
    ports: list[FcPort] = Field(default_factory=list)


class FcBladeListResource(BaseModel):
    fcBlade: list[FcBlade]


class FcBladeListResponse(BaseModel):
    fcBladeList: FcBladeListResource


class FcBladeResponse(BaseModel):
    fcBlade: FcBlade


class FcPortPatch(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    wwpn: str | None = None
    speed: str | None = None
    status: str | None = None
    mode: str | None = None
    topology: str | None = None


class FcPortUpdateRequest(BaseModel):
    fcPort: FcPortPatch


class FcBladePatch(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    serialNumber: str | None = None
    model: str | None = None
    status: str | None = None
    firmware: str | None = None
    portCount: int | None = None
    ports: list[FcPortPatch] | None = None


class FcBladeUpdateRequest(BaseModel):
    fcBlade: FcBladePatch


class MgmtBlade(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    serialNumber: str
    model: str
    status: str
    firmware: str
    role: str


class MgmtBladeListResource(BaseModel):
    mgmtBlade: list[MgmtBlade]


class MgmtBladeListResponse(BaseModel):
    mgmtBladeList: MgmtBladeListResource


class MgmtBladeResponse(BaseModel):
    mgmtBlade: MgmtBlade


class MgmtBladePatch(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    serialNumber: str | None = None
    model: str | None = None
    status: str | None = None
    firmware: str | None = None
    role: str | None = None


class MgmtBladeUpdateRequest(BaseModel):
    mgmtBlade: MgmtBladePatch


class DriveSled(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    serialNumber: str
    model: str
    status: str
    drives: list[str] = Field(default_factory=list)


class DriveSledListResource(BaseModel):
    driveSled: list[DriveSled]


class DriveSledListResponse(BaseModel):
    driveSledList: DriveSledListResource


class DriveSledResponse(BaseModel):
    driveSled: DriveSled


class DriveSledPatch(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    serialNumber: str | None = None
    model: str | None = None
    status: str | None = None
    drives: list[str] | None = None


class DriveSledUpdateRequest(BaseModel):
    driveSled: DriveSledPatch


class PowerSupply(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    location: str
    status: str
    voltage: float
    wattage: int


class PowerSupplyListResource(BaseModel):
    ps: list[PowerSupply]


class PowerSupplyListResponse(BaseModel):
    psList: PowerSupplyListResource


class PowerSupplyResponse(BaseModel):
    ps: PowerSupply


class Fan(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    location: str
    status: str
    rpm: int
    speed: str


class FanListResource(BaseModel):
    fan: list[Fan]


class FanListResponse(BaseModel):
    fanList: FanListResource


class FanResponse(BaseModel):
    fan: Fan


class WindowsBladeSection(BaseModel):
    model_config = ConfigDict(extra="allow")

    sectionNumber: int
    id: str
    hostname: str
    status: str
    role: str
    enabled: bool = True
    lastChanged: str | None = None


class WindowsBladeSectionResponse(BaseModel):
    windowsBlade: WindowsBladeSection


class BladeStatus(BaseModel):
    id: str
    status: str
    health: str


class BladeStatusResponse(BaseModel):
    bladeStatus: BladeStatus


class DeviceSummary(BaseModel):
    ethBlades: int
    fcBlades: int
    mgmtBlades: int
    driveSleds: int
    powerSupplies: int
    fans: int


class DeviceSummaryResponse(BaseModel):
    deviceSummary: DeviceSummary


class DeviceStatus(BaseModel):
    overall: str
    ethBlades: str
    fcBlades: str
    mgmtBlades: str
    driveSleds: str
    power: str
    cooling: str


class DeviceStatusResponse(BaseModel):
    deviceStatus: DeviceStatus


class DeviceType(BaseModel):
    name: str
    count: int
    description: str


class DeviceTypeListResource(BaseModel):
    type: list[DeviceType]


class DeviceTypeListResponse(BaseModel):
    typeList: DeviceTypeListResource


def _ws_result(summary: str) -> WSResultCode:
    return WSResultCode(summary=summary)


def _validate_identifier(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail=f"{field_name} is required")
    return normalized


def _serialize_eth_blade(blade: dict[str, Any]) -> EthBlade:
    return EthBlade.model_validate(blade)


def _serialize_eth_port(port: dict[str, Any]) -> EthPort:
    return EthPort.model_validate(port)


def _serialize_fc_blade(blade: dict[str, Any]) -> FcBlade:
    return FcBlade.model_validate(blade)


def _serialize_fc_port(port: dict[str, Any]) -> FcPort:
    return FcPort.model_validate(port)


def _serialize_mgmt_blade(blade: dict[str, Any]) -> MgmtBlade:
    return MgmtBlade.model_validate(blade)


def _serialize_drive_sled(sled: dict[str, Any]) -> DriveSled:
    return DriveSled.model_validate(sled)


def _serialize_power_supply(item: dict[str, Any]) -> PowerSupply:
    return PowerSupply.model_validate(item)


def _serialize_fan(item: dict[str, Any]) -> Fan:
    return Fan.model_validate(item)


def _serialize_windows_section(item: dict[str, Any]) -> WindowsBladeSection:
    return WindowsBladeSection.model_validate(item)


def _health_from_status(status_value: str) -> str:
    normalized = status_value.strip().lower()
    if normalized in {"failed", "fault", "offline", "critical"}:
        return "failed"
    if normalized in {"warning", "degraded", "standby", "resetting"}:
        return "warning"
    return "good"


def _collection_health(items: list[dict[str, Any]]) -> str:
    levels = [_health_from_status(str(item.get("status", "good"))) for item in items]
    if "failed" in levels:
        return "failed"
    if "warning" in levels:
        return "warning"
    return "good"


def _get_eth_blade_or_404(blade_id: str) -> dict[str, Any]:
    blade = aml_state.get_eth_blade(blade_id)
    if blade is None:
        raise HTTPException(status_code=404, detail="Ethernet blade not found")
    return blade


def _get_fc_blade_or_404(blade_id: str) -> dict[str, Any]:
    blade = aml_state.get_fc_blade(blade_id)
    if blade is None:
        raise HTTPException(status_code=404, detail="FC blade not found")
    return blade


def _get_mgmt_blade_or_404(blade_id: str) -> dict[str, Any]:
    blade = aml_state.get_mgmt_blade(blade_id)
    if blade is None:
        raise HTTPException(status_code=404, detail="Management blade not found")
    return blade


def _get_mgmt_blade_by_serial_or_404(serial_number: str) -> dict[str, Any]:
    for blade in aml_state.get_mgmt_blades().values():
        if str(blade.get("serialNumber")) == serial_number:
            return blade
    raise HTTPException(status_code=404, detail="Management blade not found")


def _get_windows_section_or_404(section_number: int) -> dict[str, Any]:
    section = aml_state.get_aml_windows_section(section_number)
    if section is None:
        raise HTTPException(status_code=404, detail="Windows blade section not found")
    return section


def _get_drive_sled_or_404(sled_id: str) -> dict[str, Any]:
    sled = aml_state.get_drive_sled(sled_id)
    if sled is None:
        raise HTTPException(status_code=404, detail="Drive sled not found")
    return sled


def _get_power_supply_or_404(ps_id: str) -> dict[str, Any]:
    item = aml_state.get_power_supply(ps_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Power supply not found")
    return item


def _get_fan_or_404(fan_id: str) -> dict[str, Any]:
    item = aml_state.get_aml_fan(fan_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Fan not found")
    return item


def _get_port_or_404(ports: list[dict[str, Any]], port_id: str, *, detail: str) -> dict[str, Any]:
    for port in ports:
        if str(port.get("id")) == port_id:
            return port
    raise HTTPException(status_code=404, detail=detail)


def _validate_patch(payload: BaseModel, *, identifier_field: str = "id") -> dict[str, Any]:
    updates = payload.model_dump(exclude_none=True)
    updates.pop(identifier_field, None)
    return updates


def _eth_status_response(blade: dict[str, Any]) -> BladeStatusResponse:
    return BladeStatusResponse(
        bladeStatus=BladeStatus(
            id=str(blade["id"]),
            status=str(blade["status"]),
            health=_health_from_status(str(blade["status"])),
        )
    )


def _fc_status_response(blade: dict[str, Any]) -> BladeStatusResponse:
    return BladeStatusResponse(
        bladeStatus=BladeStatus(
            id=str(blade["id"]),
            status=str(blade["status"]),
            health=_health_from_status(str(blade["status"])),
        )
    )


def _mgmt_status_response(blade: dict[str, Any]) -> BladeStatusResponse:
    return BladeStatusResponse(
        bladeStatus=BladeStatus(
            id=str(blade["id"]),
            status=str(blade["status"]),
            health=_health_from_status(str(blade["status"])),
        )
    )


def _drive_sled_status_response(sled: dict[str, Any]) -> BladeStatusResponse:
    return BladeStatusResponse(
        bladeStatus=BladeStatus(
            id=str(sled["id"]),
            status=str(sled["status"]),
            health=_health_from_status(str(sled["status"])),
        )
    )


@router.get("/devices/ethBlades", response_model=EthBladeListResponse)
async def list_eth_blades(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> EthBladeListResponse:
    _ensure_state(context)
    blades = [_serialize_eth_blade(item) for item in aml_state.get_eth_blades().values()]
    return EthBladeListResponse(ethBladeList=EthBladeListResource(ethBlade=blades))


@router.get("/devices/ethBlade/{id}", response_model=EthBladeResponse)
async def get_eth_blade(
    id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> EthBladeResponse:
    _ensure_state(context)
    blade_id = _validate_identifier(id, field_name="Ethernet blade id")
    return EthBladeResponse(ethBlade=_serialize_eth_blade(_get_eth_blade_or_404(blade_id)))


@router.put("/devices/ethBlade/{id}", response_model=EthBladeResponse)
async def put_eth_blade(
    id: str,
    payload: EthBladeUpdateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> EthBladeResponse:
    _ensure_state(context)
    _require_admin(current_user)
    blade_id = _validate_identifier(id, field_name="Ethernet blade id")
    _get_eth_blade_or_404(blade_id)
    blade = aml_state.update_eth_blade(blade_id, _validate_patch(payload.ethBlade))
    return EthBladeResponse(ethBlade=_serialize_eth_blade(blade or _get_eth_blade_or_404(blade_id)))


@router.get("/devices/ethBlade/{id}/ports", response_model=EthPortListResponse)
async def list_eth_ports(
    id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> EthPortListResponse:
    _ensure_state(context)
    blade_id = _validate_identifier(id, field_name="Ethernet blade id")
    blade = _get_eth_blade_or_404(blade_id)
    return EthPortListResponse(
        ethPortList=EthPortListResource(
            port=[_serialize_eth_port(item) for item in blade.get("ports", [])]
        )
    )


@router.get("/devices/ethBlade/{id}/port/{portId}", response_model=EthPortResponse)
async def get_eth_port(
    id: str,
    portId: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> EthPortResponse:
    _ensure_state(context)
    blade = _get_eth_blade_or_404(_validate_identifier(id, field_name="Ethernet blade id"))
    port = _get_port_or_404(
        blade.get("ports", []),
        _validate_identifier(portId, field_name="Ethernet port id"),
        detail="Ethernet port not found",
    )
    return EthPortResponse(ethPort=_serialize_eth_port(port))


@router.put("/devices/ethBlade/{id}/port/{portId}", response_model=EthPortResponse)
async def put_eth_port(
    id: str,
    portId: str,
    payload: EthPortUpdateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> EthPortResponse:
    _ensure_state(context)
    _require_admin(current_user)
    blade_id = _validate_identifier(id, field_name="Ethernet blade id")
    _get_eth_blade_or_404(blade_id)
    normalized_port_id = _validate_identifier(portId, field_name="Ethernet port id")
    port = aml_state.update_eth_port(blade_id, normalized_port_id, _validate_patch(payload.ethPort))
    if port is None:
        raise HTTPException(status_code=404, detail="Ethernet port not found")
    return EthPortResponse(ethPort=_serialize_eth_port(port))


@router.post("/devices/ethBlade/{id}/reset", response_model=WSResultCode)
async def reset_eth_blade(
    id: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    blade_id = _validate_identifier(id, field_name="Ethernet blade id")
    _get_eth_blade_or_404(blade_id)
    aml_state.reset_eth_blade(blade_id)
    return _ws_result(f"Ethernet blade {blade_id} reset completed")


@router.get("/devices/ethBlade/{id}/status", response_model=BladeStatusResponse)
async def get_eth_blade_status(
    id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> BladeStatusResponse:
    _ensure_state(context)
    return _eth_status_response(
        _get_eth_blade_or_404(_validate_identifier(id, field_name="Ethernet blade id"))
    )


@router.get("/devices/fcBlades", response_model=FcBladeListResponse)
async def list_fc_blades(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> FcBladeListResponse:
    _ensure_state(context)
    blades = [_serialize_fc_blade(item) for item in aml_state.get_fc_blades().values()]
    return FcBladeListResponse(fcBladeList=FcBladeListResource(fcBlade=blades))


@router.get("/devices/fcBlade/{id}", response_model=FcBladeResponse)
async def get_fc_blade(
    id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> FcBladeResponse:
    _ensure_state(context)
    blade_id = _validate_identifier(id, field_name="FC blade id")
    return FcBladeResponse(fcBlade=_serialize_fc_blade(_get_fc_blade_or_404(blade_id)))


@router.put("/devices/fcBlade/{id}", response_model=FcBladeResponse)
async def put_fc_blade(
    id: str,
    payload: FcBladeUpdateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> FcBladeResponse:
    _ensure_state(context)
    _require_admin(current_user)
    blade_id = _validate_identifier(id, field_name="FC blade id")
    _get_fc_blade_or_404(blade_id)
    blade = aml_state.update_fc_blade(blade_id, _validate_patch(payload.fcBlade))
    return FcBladeResponse(fcBlade=_serialize_fc_blade(blade or _get_fc_blade_or_404(blade_id)))


@router.get("/devices/fcBlade/{id}/ports", response_model=FcPortListResponse)
async def list_fc_ports(
    id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> FcPortListResponse:
    _ensure_state(context)
    blade_id = _validate_identifier(id, field_name="FC blade id")
    blade = _get_fc_blade_or_404(blade_id)
    return FcPortListResponse(
        fcPortList=FcPortListResource(
            port=[_serialize_fc_port(item) for item in blade.get("ports", [])]
        )
    )


@router.get("/devices/fcBlade/{id}/port/{portId}", response_model=FcPortResponse)
async def get_fc_port(
    id: str,
    portId: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> FcPortResponse:
    _ensure_state(context)
    blade = _get_fc_blade_or_404(_validate_identifier(id, field_name="FC blade id"))
    port = _get_port_or_404(
        blade.get("ports", []),
        _validate_identifier(portId, field_name="FC port id"),
        detail="FC port not found",
    )
    return FcPortResponse(fcPort=_serialize_fc_port(port))


@router.put("/devices/fcBlade/{id}/port/{portId}", response_model=FcPortResponse)
async def put_fc_port(
    id: str,
    portId: str,
    payload: FcPortUpdateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> FcPortResponse:
    _ensure_state(context)
    _require_admin(current_user)
    blade_id = _validate_identifier(id, field_name="FC blade id")
    _get_fc_blade_or_404(blade_id)
    normalized_port_id = _validate_identifier(portId, field_name="FC port id")
    port = aml_state.update_fc_port(blade_id, normalized_port_id, _validate_patch(payload.fcPort))
    if port is None:
        raise HTTPException(status_code=404, detail="FC port not found")
    return FcPortResponse(fcPort=_serialize_fc_port(port))


@router.post("/devices/fcBlade/{id}/reset", response_model=WSResultCode)
async def reset_fc_blade(
    id: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    blade_id = _validate_identifier(id, field_name="FC blade id")
    _get_fc_blade_or_404(blade_id)
    aml_state.reset_fc_blade(blade_id)
    return _ws_result(f"FC blade {blade_id} reset completed")


@router.get("/devices/fcBlade/{id}/status", response_model=BladeStatusResponse)
async def get_fc_blade_status(
    id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> BladeStatusResponse:
    _ensure_state(context)
    return _fc_status_response(
        _get_fc_blade_or_404(_validate_identifier(id, field_name="FC blade id"))
    )


@router.get("/devices/mgmtBlades", response_model=MgmtBladeListResponse)
async def list_mgmt_blades(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> MgmtBladeListResponse:
    _ensure_state(context)
    blades = [_serialize_mgmt_blade(item) for item in aml_state.get_mgmt_blades().values()]
    return MgmtBladeListResponse(mgmtBladeList=MgmtBladeListResource(mgmtBlade=blades))


@router.get("/devices/mgmtBlade/{id}", response_model=MgmtBladeResponse)
async def get_mgmt_blade(
    id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> MgmtBladeResponse:
    _ensure_state(context)
    blade_id = _validate_identifier(id, field_name="Management blade id")
    return MgmtBladeResponse(mgmtBlade=_serialize_mgmt_blade(_get_mgmt_blade_or_404(blade_id)))


@router.put("/devices/mgmtBlade/{id}", response_model=MgmtBladeResponse)
async def put_mgmt_blade(
    id: str,
    payload: MgmtBladeUpdateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> MgmtBladeResponse:
    _ensure_state(context)
    _require_admin(current_user)
    blade_id = _validate_identifier(id, field_name="Management blade id")
    _get_mgmt_blade_or_404(blade_id)
    blade = aml_state.update_mgmt_blade(blade_id, _validate_patch(payload.mgmtBlade))
    return MgmtBladeResponse(
        mgmtBlade=_serialize_mgmt_blade(blade or _get_mgmt_blade_or_404(blade_id))
    )


@router.post("/devices/mgmtBlade/{id}/failover", response_model=WSResultCode)
async def failover_mgmt_blade(
    id: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    blade_id = _validate_identifier(id, field_name="Management blade id")
    _get_mgmt_blade_or_404(blade_id)
    aml_state.failover_mgmt_blade(blade_id)
    return _ws_result(f"Management blade failover completed for {blade_id}")


@router.get("/devices/mgmtBlade/{id}/status", response_model=BladeStatusResponse)
async def get_mgmt_blade_status(
    id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> BladeStatusResponse:
    _ensure_state(context)
    return _mgmt_status_response(
        _get_mgmt_blade_or_404(_validate_identifier(id, field_name="Management blade id"))
    )


@router.get("/devices/driveSleds", response_model=DriveSledListResponse)
async def list_drive_sleds(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> DriveSledListResponse:
    _ensure_state(context)
    sleds = [_serialize_drive_sled(item) for item in aml_state.get_drive_sleds().values()]
    return DriveSledListResponse(driveSledList=DriveSledListResource(driveSled=sleds))


@router.get("/devices/driveSled/{id}", response_model=DriveSledResponse)
async def get_drive_sled(
    id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> DriveSledResponse:
    _ensure_state(context)
    sled_id = _validate_identifier(id, field_name="Drive sled id")
    return DriveSledResponse(driveSled=_serialize_drive_sled(_get_drive_sled_or_404(sled_id)))


@router.put("/devices/driveSled/{id}", response_model=DriveSledResponse)
async def put_drive_sled(
    id: str,
    payload: DriveSledUpdateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> DriveSledResponse:
    _ensure_state(context)
    _require_admin(current_user)
    sled_id = _validate_identifier(id, field_name="Drive sled id")
    _get_drive_sled_or_404(sled_id)
    sled = aml_state.update_drive_sled(sled_id, _validate_patch(payload.driveSled))
    return DriveSledResponse(
        driveSled=_serialize_drive_sled(sled or _get_drive_sled_or_404(sled_id))
    )


@router.get("/devices/driveSled/{id}/status", response_model=BladeStatusResponse)
async def get_drive_sled_status(
    id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> BladeStatusResponse:
    _ensure_state(context)
    return _drive_sled_status_response(
        _get_drive_sled_or_404(_validate_identifier(id, field_name="Drive sled id"))
    )


@router.get("/devices/powerSupplies", response_model=PowerSupplyListResponse)
async def list_power_supplies(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PowerSupplyListResponse:
    _ensure_state(context)
    items = [_serialize_power_supply(item) for item in aml_state.get_power_supplies().values()]
    return PowerSupplyListResponse(psList=PowerSupplyListResource(ps=items))


@router.get("/devices/powerSupply/{id}", response_model=PowerSupplyResponse)
async def get_power_supply(
    id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PowerSupplyResponse:
    _ensure_state(context)
    ps_id = _validate_identifier(id, field_name="Power supply id")
    return PowerSupplyResponse(ps=_serialize_power_supply(_get_power_supply_or_404(ps_id)))


@router.get("/devices/fans", response_model=FanListResponse)
async def list_fans(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> FanListResponse:
    _ensure_state(context)
    items = [_serialize_fan(item) for item in aml_state.get_aml_fans().values()]
    return FanListResponse(fanList=FanListResource(fan=items))


@router.get("/devices/fan/{id}", response_model=FanResponse)
async def get_fan(
    id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> FanResponse:
    _ensure_state(context)
    fan_id = _validate_identifier(id, field_name="Fan id")
    return FanResponse(fan=_serialize_fan(_get_fan_or_404(fan_id)))


@router.get("/devices/blades/ethernet", response_model=dict[str, list[dict[str, Any]]])
async def get_ethernet_blade_info(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, list[dict[str, Any]]]:
    _ensure_state(context)
    return {
        "ethernet": [
            _serialize_eth_blade(item).model_dump() for item in aml_state.get_eth_blades().values()
        ]
    }


@router.get("/devices/blades/fibreChannel", response_model=dict[str, list[dict[str, Any]]])
async def get_fibre_channel_blade_info(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, list[dict[str, Any]]]:
    _ensure_state(context)
    return {
        "fibreChannel": [
            _serialize_fc_blade(item).model_dump() for item in aml_state.get_fc_blades().values()
        ]
    }


@router.get("/devices/blades/library", response_model=dict[str, list[dict[str, Any]]])
async def get_library_blade_info(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, list[dict[str, Any]]]:
    _ensure_state(context)
    return {
        "library": [
            _serialize_mgmt_blade(item).model_dump()
            for item in aml_state.get_mgmt_blades().values()
        ]
    }


@router.get("/devices/blade/library/{serialNumber}", response_model=MgmtBladeResponse)
async def get_library_blade_by_serial_number(
    serialNumber: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> MgmtBladeResponse:
    _ensure_state(context)
    serial_number = _validate_identifier(serialNumber, field_name="Management blade serialNumber")
    return MgmtBladeResponse(
        mgmtBlade=_serialize_mgmt_blade(_get_mgmt_blade_by_serial_or_404(serial_number))
    )


@router.get("/devices/blades/windows", response_model=dict[str, list[dict[str, Any]]])
async def get_windows_blade_info(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, list[dict[str, Any]]]:
    _ensure_state(context)
    windows = [
        _serialize_windows_section(item).model_dump()
        for item in aml_state.list_aml_windows_sections()
    ]
    return {"windows": windows}


@router.get("/devices/blade/windows/{sectionNumber}", response_model=WindowsBladeSectionResponse)
async def get_windows_blade_section(
    sectionNumber: int,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WindowsBladeSectionResponse:
    _ensure_state(context)
    if sectionNumber <= 0:
        raise HTTPException(status_code=400, detail="sectionNumber must be a positive integer")
    section = _get_windows_section_or_404(sectionNumber)
    return WindowsBladeSectionResponse(windowsBlade=_serialize_windows_section(section))


@router.delete("/devices/blade/windows/{sectionNumber}", response_model=WSResultCode)
async def disable_windows_blade_section(
    sectionNumber: int,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    if sectionNumber <= 0:
        raise HTTPException(status_code=400, detail="sectionNumber must be a positive integer")
    _get_windows_section_or_404(sectionNumber)
    aml_state.update_aml_windows_section(
        sectionNumber,
        {"enabled": False, "status": "offline", "lastChanged": "2024-01-15T10:30:00Z"},
    )
    return _ws_result(f"Disabled windows blade section {sectionNumber}")


@router.get("/devices", response_model=DeviceSummaryResponse)
async def get_devices_summary(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> DeviceSummaryResponse:
    _ensure_state(context)
    return DeviceSummaryResponse(
        deviceSummary=DeviceSummary(
            ethBlades=len(aml_state.get_eth_blades()),
            fcBlades=len(aml_state.get_fc_blades()),
            mgmtBlades=len(aml_state.get_mgmt_blades()),
            driveSleds=len(aml_state.get_drive_sleds()),
            powerSupplies=len(aml_state.get_power_supplies()),
            fans=len(aml_state.get_aml_fans()),
        )
    )


@router.get("/devices/status", response_model=DeviceStatusResponse)
async def get_devices_status(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> DeviceStatusResponse:
    _ensure_state(context)
    eth_status = _collection_health(list(aml_state.get_eth_blades().values()))
    fc_status = _collection_health(list(aml_state.get_fc_blades().values()))
    mgmt_status = _collection_health(list(aml_state.get_mgmt_blades().values()))
    drive_sled_status = _collection_health(list(aml_state.get_drive_sleds().values()))
    power_status = _collection_health(list(aml_state.get_power_supplies().values()))
    cooling_status = _collection_health(list(aml_state.get_aml_fans().values()))
    overall = _collection_health(
        [
            {"status": eth_status},
            {"status": fc_status},
            {"status": mgmt_status},
            {"status": drive_sled_status},
            {"status": power_status},
            {"status": cooling_status},
        ]
    )
    return DeviceStatusResponse(
        deviceStatus=DeviceStatus(
            overall=overall,
            ethBlades=eth_status,
            fcBlades=fc_status,
            mgmtBlades=mgmt_status,
            driveSleds=drive_sled_status,
            power=power_status,
            cooling=cooling_status,
        )
    )


@router.post("/devices/refresh", response_model=WSResultCode)
async def refresh_devices(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    aml_state.refresh_devices()
    return _ws_result("Device discovery refresh completed")


@router.get("/devices/types", response_model=DeviceTypeListResponse)
async def list_device_types(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> DeviceTypeListResponse:
    _ensure_state(context)
    device_types = [
        DeviceType(
            name="ethernet-blade",
            count=len(aml_state.get_eth_blades()),
            description="Ethernet switch blades and their ports",
        ),
        DeviceType(
            name="fc-blade",
            count=len(aml_state.get_fc_blades()),
            description="Fibre Channel blades and their ports",
        ),
        DeviceType(
            name="management-blade",
            count=len(aml_state.get_mgmt_blades()),
            description="iBlade management controllers",
        ),
        DeviceType(
            name="drive-sled",
            count=len(aml_state.get_drive_sleds()),
            description="Drive sled and blade controller assemblies",
        ),
        DeviceType(
            name="power-supply",
            count=len(aml_state.get_power_supplies()),
            description="Blade-level power supply units",
        ),
        DeviceType(
            name="fan",
            count=len(aml_state.get_aml_fans()),
            description="Blade-level cooling fan modules",
        ),
    ]
    return DeviceTypeListResponse(typeList=DeviceTypeListResource(type=device_types))
