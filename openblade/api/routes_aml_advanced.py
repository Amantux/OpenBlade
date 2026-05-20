"""Advanced AML FC, LTFS, HA, EKM, and sharing routes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from openblade.api import aml_state
from openblade.api.routes_aml_auth import WSResultCode, _ensure_state, _require_admin, require_auth
from openblade.bootstrap import AppContext, get_context
from openblade.catalog.models import AmlUser

router = APIRouter()


class ResponseModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HpfConfig(ResponseModel):
    serialNumber: str
    enabled: bool
    mode: str
    preferredPort: int
    partnerBladeSerial: str | None = None
    interventionRequired: bool
    autoRestore: bool
    state: str


class HpfListResource(ResponseModel):
    hpf: list[HpfConfig]


class HpfListResponse(ResponseModel):
    hpfList: HpfListResource


class HpfResponse(ResponseModel):
    hpf: HpfConfig


class HpfPatch(RequestModel):
    enabled: bool | None = None
    mode: str | None = None
    preferredPort: int | None = None
    partnerBladeSerial: str | None = None
    interventionRequired: bool | None = None
    autoRestore: bool | None = None
    state: str | None = None


class HpfUpdateRequest(RequestModel):
    hpf: HpfPatch


class ZoningConfig(ResponseModel):
    serialNumber: str
    enabled: bool
    mode: str
    defaultZoneSet: str
    activeZoneCount: int
    pendingChanges: bool


class ZoningResponse(ResponseModel):
    zoning: ZoningConfig


class ZoningPatch(RequestModel):
    enabled: bool | None = None
    mode: str | None = None
    defaultZoneSet: str | None = None
    activeZoneCount: int | None = None
    pendingChanges: bool | None = None


class ZoningUpdateRequest(RequestModel):
    zoning: ZoningPatch


class LtfsDrive(ResponseModel):
    serialNumber: str
    state: str
    role: str


class LtfsDriveListResource(ResponseModel):
    drive: list[LtfsDrive]


class LtfsDriveListResponse(ResponseModel):
    driveList: LtfsDriveListResource


class LtfsMedia(ResponseModel):
    barcode: str
    state: str
    type: str


class LtfsMediaListResource(ResponseModel):
    media: list[LtfsMedia]


class LtfsMediaListResponse(ResponseModel):
    mediaList: LtfsMediaListResource


class LtfsSection(ResponseModel):
    sectionNumber: int
    name: str
    status: str
    mounted: bool
    mountPoint: str
    fileSystem: str
    partitionName: str
    readOnly: bool
    lastMounted: str | None = None


class LtfsSectionListResource(ResponseModel):
    section: list[LtfsSection]


class LtfsSectionListResponse(ResponseModel):
    sectionList: LtfsSectionListResource


class LtfsSectionResponse(ResponseModel):
    section: LtfsSection


class LtfsSectionPatch(RequestModel):
    name: str | None = None
    status: str | None = None
    mounted: bool | None = None
    mountPoint: str | None = None
    fileSystem: str | None = None
    partitionName: str | None = None
    readOnly: bool | None = None
    lastMounted: str | None = None


class LtfsSectionUpdateRequest(RequestModel):
    section: LtfsSectionPatch


class LtfsStatus(ResponseModel):
    sectionNumber: int
    state: str
    mounted: bool
    health: str
    activeMounts: int


class LtfsStatusResponse(ResponseModel):
    status: LtfsStatus


class FcPort(ResponseModel):
    serialNumber: str
    portNumber: int
    id: str
    wwpn: str
    speed: str
    status: str
    mode: str
    topology: str
    fabricLoginState: str
    alias: str | None = None


class FcPortListResource(ResponseModel):
    port: list[FcPort]


class FcPortListResponse(ResponseModel):
    portList: FcPortListResource


class FcPortResponse(ResponseModel):
    port: FcPort


class FcPortPatch(RequestModel):
    wwpn: str | None = None
    speed: str | None = None
    status: str | None = None
    mode: str | None = None
    topology: str | None = None
    fabricLoginState: str | None = None
    alias: str | None = None


class FcPortUpdateRequest(RequestModel):
    port: FcPortPatch


class FcPortStatistics(ResponseModel):
    serialNumber: str
    portNumber: int
    framesTx: int
    framesRx: int
    linkResets: int
    lossOfSignal: int
    crcErrors: int
    secondsSinceReset: int


class FcPortStatisticsResponse(ResponseModel):
    statistics: FcPortStatistics


class WwnInfo(ResponseModel):
    serialNumber: str
    nodeWwn: str
    virtualWwnEnabled: bool
    virtualNodeWwn: str | None = None
    portWwns: list[str] = Field(default_factory=list)


class WwnResponse(ResponseModel):
    wwn: WwnInfo


class WwnPatch(RequestModel):
    nodeWwn: str | None = None
    virtualWwnEnabled: bool | None = None
    virtualNodeWwn: str | None = None
    portWwns: list[str] | None = None


class WwnUpdateRequest(RequestModel):
    wwn: WwnPatch


class IscsiConfig(ResponseModel):
    serialNumber: str
    enabled: bool
    iqn: str
    ipAddress: str
    subnetMask: str
    gateway: str
    authMode: str
    mtu: int


class IscsiConfigResponse(ResponseModel):
    config: IscsiConfig


class IscsiConfigPatch(RequestModel):
    enabled: bool | None = None
    iqn: str | None = None
    ipAddress: str | None = None
    subnetMask: str | None = None
    gateway: str | None = None
    authMode: str | None = None
    mtu: int | None = None


class IscsiConfigUpdateRequest(RequestModel):
    config: IscsiConfigPatch


class IscsiSession(ResponseModel):
    id: str
    initiator: str
    target: str
    state: str
    connectedAt: str | None = None


class IscsiSessionListResource(ResponseModel):
    session: list[IscsiSession]


class IscsiSessionListResponse(ResponseModel):
    sessionList: IscsiSessionListResource


class IscsiTarget(ResponseModel):
    name: str
    status: str
    luns: list[str] = Field(default_factory=list)


class IscsiTargetListResource(ResponseModel):
    target: list[IscsiTarget]


class IscsiTargetListResponse(ResponseModel):
    targetList: IscsiTargetListResource


class IscsiInitiator(ResponseModel):
    name: str
    address: str
    state: str


class IscsiInitiatorListResource(ResponseModel):
    initiator: list[IscsiInitiator]


class IscsiInitiatorListResponse(ResponseModel):
    initiatorList: IscsiInitiatorListResource


class HaConfig(ResponseModel):
    enabled: bool
    mode: str
    clusterName: str
    heartbeatInterval: int
    autoFailback: bool


class HaConfigResponse(ResponseModel):
    config: HaConfig


class HaConfigPatch(RequestModel):
    enabled: bool | None = None
    mode: str | None = None
    clusterName: str | None = None
    heartbeatInterval: int | None = None
    autoFailback: bool | None = None


class HaConfigUpdateRequest(RequestModel):
    config: HaConfigPatch


class HaStatus(ResponseModel):
    state: str
    role: str
    peerReachable: bool
    lastFailover: str | None = None
    syncStatus: str


class HaStatusResponse(ResponseModel):
    status: HaStatus


class HaNode(ResponseModel):
    id: str
    name: str
    role: str
    state: str
    ipAddress: str
    lastHeartbeat: str | None = None
    healthy: bool


class HaNodeListResource(ResponseModel):
    node: list[HaNode]


class HaNodeListResponse(ResponseModel):
    nodeList: HaNodeListResource


class HaNodeResponse(ResponseModel):
    node: HaNode


class EkmConfig(ResponseModel):
    enabled: bool
    primaryServer: str
    secondaryServer: str | None = None
    port: int
    protocol: str
    timeoutSeconds: int
    clientCertificate: str | None = None


class EkmConfigResponse(ResponseModel):
    config: EkmConfig


class EkmConfigPatch(RequestModel):
    enabled: bool | None = None
    primaryServer: str | None = None
    secondaryServer: str | None = None
    port: int | None = None
    protocol: str | None = None
    timeoutSeconds: int | None = None
    clientCertificate: str | None = None


class EkmConfigUpdateRequest(RequestModel):
    config: EkmConfigPatch


class EkmStatus(ResponseModel):
    connected: bool
    lastTest: str | None = None
    error: str | None = None
    cacheAgeSeconds: int


class EkmStatusResponse(ResponseModel):
    status: EkmStatus


class EkmKey(ResponseModel):
    keyId: str
    alias: str
    state: str
    algorithm: str
    updatedAt: str


class EkmKeyListResource(ResponseModel):
    key: list[EkmKey]


class EkmKeyListResponse(ResponseModel):
    keyList: EkmKeyListResource


class DriveEncryption(ResponseModel):
    serialNumber: str
    enabled: bool
    mode: str
    keyManager: str | None = None
    keyAlias: str | None = None
    status: str


class DriveEncryptionResponse(ResponseModel):
    encryption: DriveEncryption


class DriveEncryptionPatch(RequestModel):
    enabled: bool | None = None
    mode: str | None = None
    keyManager: str | None = None
    keyAlias: str | None = None
    status: str | None = None


class DriveEncryptionUpdateRequest(RequestModel):
    encryption: DriveEncryptionPatch


class PartitionEncryption(ResponseModel):
    name: str
    enabled: bool
    mode: str
    keyManager: str | None = None
    keyAlias: str | None = None
    status: str


class PartitionEncryptionResponse(ResponseModel):
    encryption: PartitionEncryption


class PartitionEncryptionPatch(RequestModel):
    enabled: bool | None = None
    mode: str | None = None
    keyManager: str | None = None
    keyAlias: str | None = None
    status: str | None = None


class PartitionEncryptionUpdateRequest(RequestModel):
    encryption: PartitionEncryptionPatch


class DataPathStatus(ResponseModel):
    serialNumber: str
    status: str
    activePaths: int
    preferredPath: str
    lastTest: str | None = None
    lastResult: str


class DataPathStatusResponse(ResponseModel):
    dataPath: DataPathStatus


class SharingConfig(ResponseModel):
    enabled: bool
    mode: str
    serverId: str
    exportedPartitions: list[str] = Field(default_factory=list)


class SharingConfigResponse(ResponseModel):
    config: SharingConfig


class SharingConfigPatch(RequestModel):
    enabled: bool | None = None
    mode: str | None = None
    serverId: str | None = None
    exportedPartitions: list[str] | None = None


class SharingConfigUpdateRequest(RequestModel):
    config: SharingConfigPatch


class SharingStatus(ResponseModel):
    state: str
    connectedClients: int
    lastSync: str | None = None
    health: str


class SharingStatusResponse(ResponseModel):
    status: SharingStatus


class SharingClient(ResponseModel):
    id: str
    name: str
    type: str
    status: str
    lastSeen: str | None = None
    partitions: list[str] = Field(default_factory=list)


class SharingClientListResource(ResponseModel):
    client: list[SharingClient]


class SharingClientListResponse(ResponseModel):
    clientList: SharingClientListResource


class RemoteLibrary(ResponseModel):
    id: str
    name: str
    host: str
    model: str
    status: str
    protocol: str
    sharedSlots: int


class RemoteLibraryListResource(ResponseModel):
    remoteLibrary: list[RemoteLibrary]


class RemoteLibraryListResponse(ResponseModel):
    remoteLibraryList: RemoteLibraryListResource


class RemoteLibraryResponse(ResponseModel):
    remoteLibrary: RemoteLibrary


class RemoteLibraryCreate(RequestModel):
    name: str
    host: str
    model: str
    status: str = "connected"
    protocol: str = "FC"
    sharedSlots: int = 24


class RemoteLibraryUpdate(RequestModel):
    name: str | None = None
    host: str | None = None
    model: str | None = None
    status: str | None = None
    protocol: str | None = None
    sharedSlots: int | None = None


class RemoteLibraryCreateRequest(RequestModel):
    remoteLibrary: RemoteLibraryCreate


class RemoteLibraryUpdateRequest(RequestModel):
    remoteLibrary: RemoteLibraryUpdate


class CapacitySummary(ResponseModel):
    totalSlots: int
    usedSlots: int
    freeSlots: int
    totalDrives: int
    onlineDrives: int
    mediaCount: int
    partitions: int


class CapacitySummaryResponse(ResponseModel):
    capacity: CapacitySummary


class PerformanceMetrics(ResponseModel):
    cpuUsage: int
    memoryUsage: int
    roboticsOpsPerHour: int
    mountsPerHour: int
    bandwidthMBps: int
    latencyMs: int


class PerformanceMetricsResponse(ResponseModel):
    performance: PerformanceMetrics


class MediaHistoryEvent(ResponseModel):
    timestamp: str
    action: str
    drive: str | None = None
    source: str | None = None
    destination: str | None = None
    result: str


class MediaHistoryListResource(ResponseModel):
    event: list[MediaHistoryEvent]


class MediaHistoryListResponse(ResponseModel):
    historyList: MediaHistoryListResource


class SupportedMedia(ResponseModel):
    name: str
    description: str
    nativeCapacity: str
    compressedCapacity: str
    generations: list[str] = Field(default_factory=list)
    cleaning: bool


class SupportedMediaListResource(ResponseModel):
    mediaType: list[SupportedMedia]


class SupportedMediaListResponse(ResponseModel):
    supportedMediaList: SupportedMediaListResource


def _ws_result(summary: str) -> WSResultCode:
    return WSResultCode(summary=summary)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_identifier(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail=f"{field_name} is required")
    return normalized


def _validate_positive_int(value: int, *, field_name: str) -> int:
    if value < 1:
        raise HTTPException(status_code=400, detail=f"{field_name} must be positive")
    return value


def _get_fc_blade_or_404(serial_number: str) -> dict[str, Any]:
    blade = aml_state.get_fc_blade_by_serial(serial_number)
    if blade is None:
        raise HTTPException(status_code=404, detail="FC blade not found")
    return blade


def _get_ltfs_section_or_404(section_number: int) -> dict[str, Any]:
    section = aml_state.get_aml_ltfs_section(section_number)
    if section is None:
        raise HTTPException(status_code=404, detail="LTFS section not found")
    return section


def _get_fc_port_or_404(serial_number: str, port_number: int) -> dict[str, Any]:
    port = aml_state.get_fc_port_by_number(serial_number, port_number)
    if port is None:
        raise HTTPException(status_code=404, detail="FC port not found")
    return port


def _get_iscsi_blade_or_404(serial_number: str) -> dict[str, Any]:
    blade = aml_state.get_aml_iscsi_blade(serial_number)
    if blade is None:
        raise HTTPException(status_code=404, detail="iSCSI blade not found")
    return blade


def _get_ha_node_or_404(node_id: str) -> dict[str, Any]:
    node = aml_state.get_aml_advanced_ha_node(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="HA node not found")
    return node


def _get_drive_or_404(serial_number: str) -> dict[str, Any]:
    drive = aml_state.get_aml_drive(serial_number)
    if drive is None:
        raise HTTPException(status_code=404, detail="Drive not found")
    return drive


def _get_partition_or_404(name: str) -> dict[str, Any]:
    partition = aml_state.get_aml_partition(name)
    if partition is None:
        raise HTTPException(status_code=404, detail="Partition not found")
    return partition


def _get_remote_library_or_404(library_id: str) -> dict[str, Any]:
    library = aml_state.get_aml_remote_library(library_id)
    if library is None:
        raise HTTPException(status_code=404, detail="Remote library not found")
    return library


def _serialize_hpf(blade: dict[str, Any]) -> HpfConfig:
    payload = {"serialNumber": str(blade["serialNumber"]), **dict(blade.get("hpf", {}))}
    return HpfConfig.model_validate(payload)


def _serialize_zoning(blade: dict[str, Any]) -> ZoningConfig:
    payload = {"serialNumber": str(blade["serialNumber"]), **dict(blade.get("zoning", {}))}
    return ZoningConfig.model_validate(payload)


def _serialize_ltfs_section(section: dict[str, Any]) -> LtfsSection:
    return LtfsSection.model_validate(section)


def _serialize_fc_port(serial_number: str, port: dict[str, Any]) -> FcPort:
    payload = {"serialNumber": serial_number, **port}
    return FcPort.model_validate(payload)


def _serialize_fc_port_stats(serial_number: str, port: dict[str, Any]) -> FcPortStatistics:
    payload = {
        "serialNumber": serial_number,
        "portNumber": int(port.get("portNumber", 0)),
        **dict(port.get("statistics", {})),
    }
    return FcPortStatistics.model_validate(payload)


def _serialize_wwn(blade: dict[str, Any]) -> WwnInfo:
    return WwnInfo.model_validate(blade.get("wwn", {}))


def _serialize_iscsi_config(blade: dict[str, Any]) -> IscsiConfig:
    payload = {key: value for key, value in blade.items() if key not in {"sessions", "targets", "initiators"}}
    return IscsiConfig.model_validate(payload)


def _ha_status_payload() -> dict[str, Any]:
    config = aml_state.get_aml_advanced_ha_config()
    nodes = aml_state.list_aml_advanced_ha_nodes()
    active = next((item for item in nodes if item.get("role") == "active"), None)
    peer = next((item for item in nodes if item.get("role") != "active"), None)
    return {
        "state": "standalone" if not config.get("enabled") else str(active.get("state", "active") if active else "active"),
        "role": str(active.get("role", "active") if active else "active"),
        "peerReachable": bool(peer and peer.get("healthy") and config.get("enabled")),
        "lastFailover": config.get("lastFailover"),
        "syncStatus": "not-configured" if not config.get("enabled") else "in-sync",
    }


def _serialize_ekm_config(config: dict[str, Any]) -> EkmConfig:
    return EkmConfig.model_validate(config)


def _serialize_drive_encryption(drive: dict[str, Any]) -> DriveEncryption:
    payload = {"serialNumber": str(drive["serialNumber"]), **dict(drive.get("encryptionState", {}))}
    return DriveEncryption.model_validate(payload)


def _serialize_partition_encryption(name: str, partition: dict[str, Any]) -> PartitionEncryption:
    encryption = dict(partition.get("encryption", {}))
    payload = {
        "name": name,
        "enabled": bool(encryption.get("enabled", False)),
        "mode": str(encryption.get("mode", encryption.get("type", "none"))),
        "keyManager": encryption.get("keyManager"),
        "keyAlias": encryption.get("keyAlias"),
        "status": str(encryption.get("status", "enabled" if encryption.get("enabled") else "disabled")),
    }
    return PartitionEncryption.model_validate(payload)


def _serialize_data_path(blade: dict[str, Any]) -> DataPathStatus:
    return DataPathStatus.model_validate(blade.get("dataPath", {}))


def _capacity_payload() -> dict[str, Any]:
    partitions = aml_state.list_aml_partitions()
    drives = aml_state.list_aml_drives()
    media = aml_state.list_aml_media()
    total_slots = sum(int(partition.get("slotCount", 0)) for partition in partitions)
    used_slots = len(media)
    return {
        "totalSlots": total_slots,
        "usedSlots": used_slots,
        "freeSlots": max(total_slots - used_slots, 0),
        "totalDrives": len(drives),
        "onlineDrives": sum(1 for drive in drives if drive.get("status") == "online"),
        "mediaCount": len(media),
        "partitions": len(partitions),
    }


def _performance_payload() -> dict[str, Any]:
    mounts = aml_state.list_aml_mounts()
    drives = aml_state.list_aml_drives()
    return {
        "cpuUsage": 18,
        "memoryUsage": 42,
        "roboticsOpsPerHour": 24,
        "mountsPerHour": len(mounts) * 6,
        "bandwidthMBps": 640 if drives else 0,
        "latencyMs": 4,
    }


def _media_history_events(media: dict[str, Any]) -> list[MediaHistoryEvent]:
    items = []
    for event in media.get("history", []):
        payload = {
            "timestamp": event.get("timestamp"),
            "action": event.get("action", event.get("type", "inventory")),
            "drive": event.get("drive"),
            "source": event.get("source"),
            "destination": event.get("destination"),
            "result": event.get("result", "success"),
        }
        items.append(MediaHistoryEvent.model_validate(payload))
    return items


@router.get("/devices/blades/fibreChannel/hpf", response_model=HpfListResponse)
async def list_fc_hpf(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> HpfListResponse:
    _ensure_state(context)
    blades = [_serialize_hpf(item) for item in aml_state.get_fc_blades().values()]
    return HpfListResponse(hpfList=HpfListResource(hpf=blades))


@router.get("/devices/blades/fibreChannel/ports", response_model=FcPortListResponse)
async def list_all_fc_ports(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> FcPortListResponse:
    _ensure_state(context)
    ports = [
        _serialize_fc_port(str(blade.get("serialNumber")), port)
        for blade in aml_state.get_fc_blades().values()
        for port in blade.get("ports", [])
    ]
    return FcPortListResponse(portList=FcPortListResource(port=ports))


@router.get("/devices/blades/ltfs", response_model=LtfsSectionListResponse)
async def list_ltfs_sections(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> LtfsSectionListResponse:
    _ensure_state(context)
    sections = [_serialize_ltfs_section(item) for item in aml_state.list_aml_ltfs_sections()]
    return LtfsSectionListResponse(sectionList=LtfsSectionListResource(section=sections))


@router.get("/devices/blade/fibreChannel/{serialNumber}/hpf", response_model=HpfResponse)
async def get_fc_hpf(
    serialNumber: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> HpfResponse:
    _ensure_state(context)
    blade = _get_fc_blade_or_404(_validate_identifier(serialNumber, field_name="serialNumber"))
    return HpfResponse(hpf=_serialize_hpf(blade))


@router.put("/devices/blade/fibreChannel/{serialNumber}/hpf", response_model=WSResultCode)
async def put_fc_hpf(
    serialNumber: str,
    payload: HpfUpdateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    serial = _validate_identifier(serialNumber, field_name="serialNumber")
    blade = _get_fc_blade_or_404(serial)
    updates = payload.hpf.model_dump(exclude_none=True)
    aml_state.update_fc_blade_by_serial(serial, {"hpf": {**dict(blade.get("hpf", {})), **updates}})
    return _ws_result(f"Updated HPF configuration for FC blade {serial}")


@router.put("/devices/blade/fibreChannel/{serialNumber}/hpf/intervention", response_model=WSResultCode)
async def clear_fc_hpf_intervention(
    serialNumber: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    serial = _validate_identifier(serialNumber, field_name="serialNumber")
    blade = _get_fc_blade_or_404(serial)
    hpf = {**dict(blade.get("hpf", {})), "interventionRequired": False, "state": "protected"}
    aml_state.update_fc_blade_by_serial(serial, {"hpf": hpf})
    return _ws_result(f"Cleared HPF intervention flag for FC blade {serial}")


@router.get("/devices/blade/fibreChannel/{serialNumber}/zoning", response_model=ZoningResponse)
async def get_fc_zoning(
    serialNumber: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> ZoningResponse:
    _ensure_state(context)
    blade = _get_fc_blade_or_404(_validate_identifier(serialNumber, field_name="serialNumber"))
    return ZoningResponse(zoning=_serialize_zoning(blade))


@router.put("/devices/blade/fibreChannel/{serialNumber}/zoning", response_model=WSResultCode)
async def put_fc_zoning(
    serialNumber: str,
    payload: ZoningUpdateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    serial = _validate_identifier(serialNumber, field_name="serialNumber")
    blade = _get_fc_blade_or_404(serial)
    updates = payload.zoning.model_dump(exclude_none=True)
    aml_state.update_fc_blade_by_serial(serial, {"zoning": {**dict(blade.get("zoning", {})), **updates}})
    return _ws_result(f"Updated zoning configuration for FC blade {serial}")


@router.get("/devices/blade/ltfs/{sectionNumber}", response_model=LtfsSectionResponse)
async def get_ltfs_section(
    sectionNumber: int,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> LtfsSectionResponse:
    _ensure_state(context)
    section = _get_ltfs_section_or_404(_validate_positive_int(sectionNumber, field_name="sectionNumber"))
    return LtfsSectionResponse(section=_serialize_ltfs_section(section))


@router.put("/devices/blade/ltfs/{sectionNumber}", response_model=WSResultCode)
async def put_ltfs_section(
    sectionNumber: int,
    payload: LtfsSectionUpdateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    section_no = _validate_positive_int(sectionNumber, field_name="sectionNumber")
    _get_ltfs_section_or_404(section_no)
    aml_state.update_aml_ltfs_section(section_no, payload.section.model_dump(exclude_none=True))
    return _ws_result(f"Updated LTFS section {section_no}")


@router.get("/devices/blade/ltfs/{sectionNumber}/status", response_model=LtfsStatusResponse)
async def get_ltfs_section_status(
    sectionNumber: int,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> LtfsStatusResponse:
    _ensure_state(context)
    section = _get_ltfs_section_or_404(_validate_positive_int(sectionNumber, field_name="sectionNumber"))
    state = "mounted" if section.get("mounted") else section.get("status", "ready")
    health = "ok" if section.get("status") in {"ready", "mounted"} else "warning"
    status_payload = {
        "sectionNumber": int(section["sectionNumber"]),
        "state": str(state),
        "mounted": bool(section.get("mounted", False)),
        "health": health,
        "activeMounts": 1 if section.get("mounted") else 0,
    }
    return LtfsStatusResponse(status=LtfsStatus.model_validate(status_payload))


@router.post("/devices/blade/ltfs/{sectionNumber}/mount", response_model=WSResultCode)
async def mount_ltfs_section(
    sectionNumber: int,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    section_no = _validate_positive_int(sectionNumber, field_name="sectionNumber")
    section = _get_ltfs_section_or_404(section_no)
    aml_state.update_aml_ltfs_section(
        section_no,
        {"mounted": True, "status": "mounted", "lastMounted": _timestamp(), "mountPoint": section.get("mountPoint", "/ltfs/partition1")},
    )
    return _ws_result(f"Mounted LTFS section {section_no}")


@router.post("/devices/blade/ltfs/{sectionNumber}/unmount", response_model=WSResultCode)
async def unmount_ltfs_section(
    sectionNumber: int,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    section_no = _validate_positive_int(sectionNumber, field_name="sectionNumber")
    _get_ltfs_section_or_404(section_no)
    aml_state.update_aml_ltfs_section(section_no, {"mounted": False, "status": "ready"})
    return _ws_result(f"Unmounted LTFS section {section_no}")


@router.get("/devices/blade/ltfs/{sectionNumber}/drives", response_model=LtfsDriveListResponse)
async def list_ltfs_section_drives(
    sectionNumber: int,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> LtfsDriveListResponse:
    _ensure_state(context)
    section = _get_ltfs_section_or_404(_validate_positive_int(sectionNumber, field_name="sectionNumber"))
    drives = [LtfsDrive.model_validate(item) for item in section.get("drives", [])]
    return LtfsDriveListResponse(driveList=LtfsDriveListResource(drive=drives))


@router.get("/devices/blade/ltfs/{sectionNumber}/media", response_model=LtfsMediaListResponse)
async def list_ltfs_section_media(
    sectionNumber: int,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> LtfsMediaListResponse:
    _ensure_state(context)
    section = _get_ltfs_section_or_404(_validate_positive_int(sectionNumber, field_name="sectionNumber"))
    media = [LtfsMedia.model_validate(item) for item in section.get("media", [])]
    return LtfsMediaListResponse(mediaList=LtfsMediaListResource(media=media))


@router.get("/devices/blade/fibreChannel/{serialNumber}/ports", response_model=FcPortListResponse)
async def list_fc_ports(
    serialNumber: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> FcPortListResponse:
    _ensure_state(context)
    blade = _get_fc_blade_or_404(_validate_identifier(serialNumber, field_name="serialNumber"))
    ports = [_serialize_fc_port(str(blade["serialNumber"]), port) for port in blade.get("ports", [])]
    return FcPortListResponse(portList=FcPortListResource(port=ports))


@router.get("/devices/blade/fibreChannel/{serialNumber}/port/{portNumber}", response_model=FcPortResponse)
async def get_fc_port(
    serialNumber: str,
    portNumber: int,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> FcPortResponse:
    _ensure_state(context)
    serial = _validate_identifier(serialNumber, field_name="serialNumber")
    port = _get_fc_port_or_404(serial, _validate_positive_int(portNumber, field_name="portNumber"))
    return FcPortResponse(port=_serialize_fc_port(serial, port))


@router.put("/devices/blade/fibreChannel/{serialNumber}/port/{portNumber}", response_model=WSResultCode)
async def put_fc_port(
    serialNumber: str,
    portNumber: int,
    payload: FcPortUpdateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    serial = _validate_identifier(serialNumber, field_name="serialNumber")
    port_no = _validate_positive_int(portNumber, field_name="portNumber")
    _get_fc_blade_or_404(serial)
    if aml_state.update_fc_port_by_number(serial, port_no, payload.port.model_dump(exclude_none=True)) is None:
        raise HTTPException(status_code=404, detail="FC port not found")
    return _ws_result(f"Updated FC port {port_no} on blade {serial}")


@router.get("/devices/blade/fibreChannel/{serialNumber}/port/{portNumber}/statistics", response_model=FcPortStatisticsResponse)
async def get_fc_port_statistics(
    serialNumber: str,
    portNumber: int,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> FcPortStatisticsResponse:
    _ensure_state(context)
    serial = _validate_identifier(serialNumber, field_name="serialNumber")
    port = _get_fc_port_or_404(serial, _validate_positive_int(portNumber, field_name="portNumber"))
    return FcPortStatisticsResponse(statistics=_serialize_fc_port_stats(serial, port))


@router.get("/devices/blade/fibreChannel/{serialNumber}/wwn", response_model=WwnResponse)
async def get_fc_wwn(
    serialNumber: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WwnResponse:
    _ensure_state(context)
    blade = _get_fc_blade_or_404(_validate_identifier(serialNumber, field_name="serialNumber"))
    return WwnResponse(wwn=_serialize_wwn(blade))


@router.put("/devices/blade/fibreChannel/{serialNumber}/wwn", response_model=WSResultCode)
async def put_fc_wwn(
    serialNumber: str,
    payload: WwnUpdateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    serial = _validate_identifier(serialNumber, field_name="serialNumber")
    blade = _get_fc_blade_or_404(serial)
    current = dict(blade.get("wwn", {}))
    updates = payload.wwn.model_dump(exclude_none=True)
    aml_state.update_fc_blade_by_serial(serial, {"wwn": {**current, **updates, "serialNumber": serial}})
    return _ws_result(f"Updated WWN settings for FC blade {serial}")


@router.get("/devices/blade/iSCSI/{serialNumber}/config", response_model=IscsiConfigResponse)
async def get_iscsi_config(
    serialNumber: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IscsiConfigResponse:
    _ensure_state(context)
    blade = _get_iscsi_blade_or_404(_validate_identifier(serialNumber, field_name="serialNumber"))
    return IscsiConfigResponse(config=_serialize_iscsi_config(blade))


@router.put("/devices/blade/iSCSI/{serialNumber}/config", response_model=WSResultCode)
async def put_iscsi_config(
    serialNumber: str,
    payload: IscsiConfigUpdateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    serial = _validate_identifier(serialNumber, field_name="serialNumber")
    _get_iscsi_blade_or_404(serial)
    aml_state.update_aml_iscsi_blade(serial, payload.config.model_dump(exclude_none=True))
    return _ws_result(f"Updated iSCSI configuration for blade {serial}")


@router.get("/devices/blade/iSCSI/{serialNumber}/sessions", response_model=IscsiSessionListResponse)
async def list_iscsi_sessions(
    serialNumber: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IscsiSessionListResponse:
    _ensure_state(context)
    blade = _get_iscsi_blade_or_404(_validate_identifier(serialNumber, field_name="serialNumber"))
    sessions = [IscsiSession.model_validate(item) for item in blade.get("sessions", [])]
    return IscsiSessionListResponse(sessionList=IscsiSessionListResource(session=sessions))


@router.get("/devices/blade/iSCSI/{serialNumber}/targets", response_model=IscsiTargetListResponse)
async def list_iscsi_targets(
    serialNumber: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IscsiTargetListResponse:
    _ensure_state(context)
    blade = _get_iscsi_blade_or_404(_validate_identifier(serialNumber, field_name="serialNumber"))
    targets = [IscsiTarget.model_validate(item) for item in blade.get("targets", [])]
    return IscsiTargetListResponse(targetList=IscsiTargetListResource(target=targets))


@router.get("/devices/blade/iSCSI/{serialNumber}/initiators", response_model=IscsiInitiatorListResponse)
async def list_iscsi_initiators(
    serialNumber: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IscsiInitiatorListResponse:
    _ensure_state(context)
    blade = _get_iscsi_blade_or_404(_validate_identifier(serialNumber, field_name="serialNumber"))
    initiators = [IscsiInitiator.model_validate(item) for item in blade.get("initiators", [])]
    return IscsiInitiatorListResponse(initiatorList=IscsiInitiatorListResource(initiator=initiators))


@router.get("/system/ha/config", response_model=HaConfigResponse)
async def get_ha_config(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> HaConfigResponse:
    _ensure_state(context)
    return HaConfigResponse(config=HaConfig.model_validate(aml_state.get_aml_advanced_ha_config()))


@router.put("/system/ha/config", response_model=WSResultCode)
async def put_ha_config(
    payload: HaConfigUpdateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    aml_state.set_aml_advanced_ha_config(payload.config.model_dump(exclude_none=True))
    return _ws_result("Updated HA configuration")


@router.get("/system/ha/status", response_model=HaStatusResponse)
async def get_ha_status(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> HaStatusResponse:
    _ensure_state(context)
    return HaStatusResponse(status=HaStatus.model_validate(_ha_status_payload()))


@router.get("/system/ha/nodes", response_model=HaNodeListResponse)
async def list_ha_nodes(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> HaNodeListResponse:
    _ensure_state(context)
    nodes = [HaNode.model_validate(item) for item in aml_state.list_aml_advanced_ha_nodes()]
    return HaNodeListResponse(nodeList=HaNodeListResource(node=nodes))


@router.get("/system/ha/node/{id}", response_model=HaNodeResponse)
async def get_ha_node(
    id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> HaNodeResponse:
    _ensure_state(context)
    node = _get_ha_node_or_404(_validate_identifier(id, field_name="id"))
    return HaNodeResponse(node=HaNode.model_validate(node))


@router.get("/system/ekm/config", response_model=EkmConfigResponse)
async def get_ekm_config(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> EkmConfigResponse:
    _ensure_state(context)
    return EkmConfigResponse(config=_serialize_ekm_config(aml_state.get_aml_ekm_config()))


@router.put("/system/ekm/config", response_model=WSResultCode)
async def put_ekm_config(
    payload: EkmConfigUpdateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    aml_state.set_aml_ekm_config(payload.config.model_dump(exclude_none=True))
    return _ws_result("Updated EKM configuration")


@router.get("/system/ekm/status", response_model=EkmStatusResponse)
async def get_ekm_status(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> EkmStatusResponse:
    _ensure_state(context)
    return EkmStatusResponse(status=EkmStatus.model_validate(aml_state.get_aml_ekm_status()))


@router.post("/system/ekm/test", response_model=WSResultCode)
async def test_ekm_connection(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    config = aml_state.get_aml_ekm_config()
    connected = bool(config.get("enabled"))
    aml_state.set_aml_ekm_status(
        {
            "connected": connected,
            "lastTest": _timestamp(),
            "error": None if connected else "EKM disabled",
            "cacheAgeSeconds": 0,
        }
    )
    return _ws_result("EKM connectivity test completed")


@router.post("/system/ekm/keys/refresh", response_model=WSResultCode)
async def refresh_ekm_keys(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    keys = aml_state.list_aml_ekm_keys()
    refreshed = []
    now = _timestamp()
    for item in keys:
        refreshed.append({**item, "updatedAt": now, "state": "cached"})
    aml_state.set_aml_ekm_keys(refreshed)
    status_payload = aml_state.get_aml_ekm_status()
    aml_state.set_aml_ekm_status({**status_payload, "cacheAgeSeconds": 0, "lastTest": now})
    return _ws_result("Refreshed EKM key cache")


@router.get("/system/ekm/keys", response_model=EkmKeyListResponse)
async def list_ekm_keys(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> EkmKeyListResponse:
    _ensure_state(context)
    keys = [EkmKey.model_validate(item) for item in aml_state.list_aml_ekm_keys()]
    return EkmKeyListResponse(keyList=EkmKeyListResource(key=keys))


@router.get("/drive/{serialNumber}/encryption", response_model=DriveEncryptionResponse)
async def get_drive_encryption(
    serialNumber: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> DriveEncryptionResponse:
    _ensure_state(context)
    drive = _get_drive_or_404(_validate_identifier(serialNumber, field_name="serialNumber"))
    return DriveEncryptionResponse(encryption=_serialize_drive_encryption(drive))


@router.put("/drive/{serialNumber}/encryption", response_model=WSResultCode)
async def put_drive_encryption(
    serialNumber: str,
    payload: DriveEncryptionUpdateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    serial = _validate_identifier(serialNumber, field_name="serialNumber")
    drive = _get_drive_or_404(serial)
    updates = payload.encryption.model_dump(exclude_none=True)
    aml_state.update_aml_drive(serial, {"encryptionState": {**dict(drive.get("encryptionState", {})), **updates}})
    return _ws_result(f"Updated encryption settings for drive {serial}")


@router.get("/devices/blade/fibreChannel/{serialNumber}/dataPath", response_model=DataPathStatusResponse)
async def get_fc_data_path(
    serialNumber: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> DataPathStatusResponse:
    _ensure_state(context)
    blade = _get_fc_blade_or_404(_validate_identifier(serialNumber, field_name="serialNumber"))
    return DataPathStatusResponse(dataPath=_serialize_data_path(blade))


@router.post("/devices/blade/fibreChannel/{serialNumber}/dataPath/test", response_model=WSResultCode)
async def test_fc_data_path(
    serialNumber: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    serial = _validate_identifier(serialNumber, field_name="serialNumber")
    blade = _get_fc_blade_or_404(serial)
    current = dict(blade.get("dataPath", {}))
    aml_state.update_fc_blade_by_serial(
        serial,
        {"dataPath": {**current, "status": "healthy", "lastTest": _timestamp(), "lastResult": "pass"}},
    )
    return _ws_result(f"Completed data path test for FC blade {serial}")


@router.get("/system/sharing/config", response_model=SharingConfigResponse)
async def get_sharing_config(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> SharingConfigResponse:
    _ensure_state(context)
    return SharingConfigResponse(config=SharingConfig.model_validate(aml_state.get_aml_sharing_config()))


@router.put("/system/sharing/config", response_model=WSResultCode)
async def put_sharing_config(
    payload: SharingConfigUpdateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    config = aml_state.set_aml_sharing_config(payload.config.model_dump(exclude_none=True))
    status_payload = aml_state.get_aml_sharing_status()
    aml_state.set_aml_sharing_status(
        {
            **status_payload,
            "state": "enabled" if config.get("enabled") else "disabled",
            "connectedClients": len(aml_state.list_aml_sharing_clients()),
        }
    )
    return _ws_result("Updated library sharing configuration")


@router.get("/system/sharing/status", response_model=SharingStatusResponse)
async def get_sharing_status(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> SharingStatusResponse:
    _ensure_state(context)
    return SharingStatusResponse(status=SharingStatus.model_validate(aml_state.get_aml_sharing_status()))


@router.get("/system/sharing/clients", response_model=SharingClientListResponse)
async def list_sharing_clients(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> SharingClientListResponse:
    _ensure_state(context)
    clients = [SharingClient.model_validate(item) for item in aml_state.list_aml_sharing_clients()]
    return SharingClientListResponse(clientList=SharingClientListResource(client=clients))


@router.get("/system/remoteLibraries", response_model=RemoteLibraryListResponse)
async def list_remote_libraries(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> RemoteLibraryListResponse:
    _ensure_state(context)
    libraries = [RemoteLibrary.model_validate(item) for item in aml_state.list_aml_remote_libraries()]
    return RemoteLibraryListResponse(remoteLibraryList=RemoteLibraryListResource(remoteLibrary=libraries))


@router.post("/system/remoteLibraries", response_model=WSResultCode)
async def create_remote_library(
    payload: RemoteLibraryCreateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    library = aml_state.create_aml_remote_library(payload.remoteLibrary.model_dump())
    return _ws_result(f"Added remote library {library['id']}")


@router.get("/system/remoteLibrary/{id}", response_model=RemoteLibraryResponse)
async def get_remote_library(
    id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> RemoteLibraryResponse:
    _ensure_state(context)
    library = _get_remote_library_or_404(_validate_identifier(id, field_name="id"))
    return RemoteLibraryResponse(remoteLibrary=RemoteLibrary.model_validate(library))


@router.put("/system/remoteLibrary/{id}", response_model=WSResultCode)
async def put_remote_library(
    id: str,
    payload: RemoteLibraryUpdateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    library_id = _validate_identifier(id, field_name="id")
    _get_remote_library_or_404(library_id)
    aml_state.update_aml_remote_library(library_id, payload.remoteLibrary.model_dump(exclude_none=True))
    return _ws_result(f"Updated remote library {library_id}")


@router.delete("/system/remoteLibrary/{id}", response_model=WSResultCode)
async def delete_remote_library(
    id: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    library_id = _validate_identifier(id, field_name="id")
    if not aml_state.delete_aml_remote_library(library_id):
        raise HTTPException(status_code=404, detail="Remote library not found")
    return _ws_result(f"Deleted remote library {library_id}")


@router.get("/system/capacity", response_model=CapacitySummaryResponse)
async def get_capacity_summary(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> CapacitySummaryResponse:
    _ensure_state(context)
    return CapacitySummaryResponse(capacity=CapacitySummary.model_validate(_capacity_payload()))


@router.get("/system/supportedMedia", response_model=SupportedMediaListResponse)
async def list_supported_media(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> SupportedMediaListResponse:
    _ensure_state(context)
    media_types = [SupportedMedia.model_validate(item) for item in aml_state.list_aml_supported_media()]
    return SupportedMediaListResponse(supportedMediaList=SupportedMediaListResource(mediaType=media_types))
