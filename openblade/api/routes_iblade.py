"""iBlade compatibility routes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, status

from openblade.api import aml_state
from openblade.api.routes_aml_auth import _ensure_state, require_auth
from openblade.bootstrap import AppContext, get_context
from openblade.catalog.models import AmlUser
from openblade.nas.iblade_types import (
    CodeDescription,
    IBladeHost,
    IBladeIoStatus,
    IBladeJobResponse,
    IBladeMessage,
    IBladeNetworkConfig,
    IBladeProductElement,
    IBladeProductInfo,
    IBladeReport,
    IBladeSetting,
    IBladeVolumeGroup,
)

router = APIRouter()

_STATE_CODES = [
    {"code": "READY", "description": "Element is ready for normal library operations."},
    {"code": "IN_USE", "description": "Element is actively participating in an operation."},
    {"code": "LOADED", "description": "Tape is loaded in a drive."},
    {"code": "UNLOADED", "description": "Tape is not mounted in a drive."},
    {"code": "OFFLINE", "description": "Element is administratively offline."},
    {"code": "ERROR", "description": "Element is faulted and requires attention."},
]
_VOLUME_STATES = [
    {"code": "SCRATCH", "description": "Volume is available for assignment."},
    {"code": "ASSIGNED", "description": "Volume is assigned to a volume group."},
    {"code": "EXPORTED", "description": "Volume is staged for export or already exported."},
    {"code": "FULL", "description": "Volume has no remaining usable capacity."},
]
_VG_STATES = [
    {"code": "READY", "description": "Volume group is online and consistent."},
    {"code": "DEGRADED", "description": "Volume group is accessible with warnings."},
    {"code": "REPAIRING", "description": "Volume group is being repaired."},
    {"code": "OFFLINE", "description": "Volume group is unavailable."},
]
_JOB_STATES = [
    {"code": "queued", "description": "Job is queued and waiting to run."},
    {"code": "active", "description": "Job is currently running."},
    {"code": "completed", "description": "Job completed successfully."},
    {"code": "failed", "description": "Job failed and requires review."},
]
_REASON_CODES = [
    {"code": "NONE", "description": "No exceptional reason is currently recorded."},
    {"code": "OPERATOR_REQUEST", "description": "State change was requested by an operator."},
    {"code": "MAINTENANCE", "description": "State change is due to maintenance activity."},
    {"code": "HARDWARE_EVENT", "description": "A hardware condition triggered the state change."},
]
_VG_REASON_CODES = [
    {"code": "NONE", "description": "Volume group is healthy."},
    {"code": "INCOMPLETE_ASSIGNMENT", "description": "Expected media are missing from the group."},
    {"code": "REPLICATION_PENDING", "description": "Replication is scheduled or in progress."},
    {"code": "REPAIR_REQUIRED", "description": "Metadata needs repair before the group is usable."},
]


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")



def _validate_identifier(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail=f"{field_name} is required")
    return normalized



def _queue_job(job_type: str, message: str, metadata: dict[str, Any] | None = None) -> IBladeJobResponse:
    job_id = str(uuid4())
    aml_state.set_aml_job(
        job_id,
        {
            "type": job_type,
            "status": "queued",
            "progress": 0,
            "requestedAt": _timestamp(),
            "result": message,
            "metadata": metadata or {},
        },
    )
    return IBladeJobResponse(job_id=job_id, status="queued", message=message)



def _product_info() -> IBladeProductInfo:
    firmware = aml_state.get_system_firmware_info().get("currentVersion", "6.0.1")
    return IBladeProductInfo(
        product="OpenBlade iBlade",
        model="Scalar i3",
        serial="MOCK-I3-001",
        firmware=str(firmware),
        software="0.1.0",
        vendor="Quantum",
        build="20240115.1",
    )



def _message_or_404(message_id: str) -> dict[str, Any]:
    message = aml_state.get_iblade_message(message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return message



def _volume_group_or_404(index: int) -> dict[str, Any]:
    group = aml_state.get_iblade_volume_group(index)
    if group is None:
        raise HTTPException(status_code=404, detail="Volume group not found")
    return group



def _sorted_open_messages() -> list[IBladeMessage]:
    messages = [
        IBladeMessage.model_validate(item)
        for item in aml_state.list_iblade_messages()
        if not bool(item.get("acknowledged", False))
    ]
    return sorted(messages, key=lambda item: item.created_at, reverse=True)



def _serialize_hosts() -> list[IBladeHost]:
    return [IBladeHost.model_validate(item) for item in aml_state.list_iblade_hosts()]



def _serialize_volume_groups() -> list[IBladeVolumeGroup]:
    return [IBladeVolumeGroup.model_validate(item) for item in aml_state.list_iblade_volume_groups()]



def _configuration_report() -> IBladeReport:
    product = _product_info().model_dump()
    network = aml_state.get_iblade_network_config()
    partitions = aml_state.list_aml_partitions()
    drives = aml_state.list_aml_drives()
    return IBladeReport(
        generated_at=_timestamp(),
        items=[
            {"section": "product", "data": product},
            {"section": "network", "data": network},
            {"section": "partitions", "data": partitions},
            {"section": "drives", "data": drives},
        ],
        summary={
            "partitionCount": len(partitions),
            "driveCount": len(drives),
            "hostname": network.get("hostname"),
        },
    )



def _media_report() -> IBladeReport:
    media = aml_state.list_aml_media()
    return IBladeReport(
        generated_at=_timestamp(),
        items=media,
        summary={
            "total": len(media),
            "data": sum(1 for item in media if str(item.get("type")) == "LTO-9"),
            "cleaning": sum(1 for item in media if "CLN" in str(item.get("barcode", ""))),
        },
    )



def _media_count_report() -> IBladeReport:
    media = aml_state.list_aml_media()
    by_state: dict[str, int] = {}
    for item in media:
        state = str(item.get("state", "unknown"))
        by_state[state] = by_state.get(state, 0) + 1
    return IBladeReport(generated_at=_timestamp(), items=[], summary={"total": len(media), "byState": by_state})



def _volume_group_report() -> IBladeReport:
    groups = [item.model_dump() for item in _serialize_volume_groups()]
    return IBladeReport(
        generated_at=_timestamp(),
        items=groups,
        summary={"total": len(groups), "mediaCount": sum(int(item.get("mediaCount", 0)) for item in groups)},
    )



def _io_status() -> IBladeIoStatus:
    jobs = aml_state.list_aml_jobs()
    drives = aml_state.list_aml_drives()
    active_jobs = [job for job in jobs if str(job.get("status", "")).lower() in {"queued", "active", "running"}]
    active_drives = [str(drive.get("serialNumber")) for drive in drives if drive.get("loadedMedia")]
    return IBladeIoStatus(
        activeTransfers=len(active_drives),
        queueDepth=len(active_jobs),
        throughputMBps=max(len(active_drives) * 400, 0),
        activeDrives=active_drives,
    )


@router.get("/states", response_model=list[CodeDescription])
async def get_states() -> list[CodeDescription]:
    return [CodeDescription.model_validate(item) for item in _STATE_CODES]


@router.get("/volstates", response_model=list[CodeDescription])
async def get_volume_states() -> list[CodeDescription]:
    return [CodeDescription.model_validate(item) for item in _VOLUME_STATES]


@router.get("/vgstates", response_model=list[CodeDescription])
async def get_volume_group_states() -> list[CodeDescription]:
    return [CodeDescription.model_validate(item) for item in _VG_STATES]


@router.get("/jobstates", response_model=list[CodeDescription])
async def get_job_states() -> list[CodeDescription]:
    return [CodeDescription.model_validate(item) for item in _JOB_STATES]


@router.get("/reasons", response_model=list[CodeDescription])
async def get_reasons() -> list[CodeDescription]:
    return [CodeDescription.model_validate(item) for item in _REASON_CODES]


@router.get("/vgreasons", response_model=list[CodeDescription])
async def get_volume_group_reasons() -> list[CodeDescription]:
    return [CodeDescription.model_validate(item) for item in _VG_REASON_CODES]


@router.get("/messages", response_model=list[IBladeMessage])
async def list_messages(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> list[IBladeMessage]:
    _ensure_state(context)
    return _sorted_open_messages()


@router.get("/messages/{message_id}", response_model=IBladeMessage)
async def get_message(
    message_id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeMessage:
    _ensure_state(context)
    return IBladeMessage.model_validate(_message_or_404(_validate_identifier(message_id, field_name="message id")))


@router.delete("/messages/{message_id}", response_model=IBladeMessage)
async def delete_message(
    message_id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeMessage:
    _ensure_state(context)
    message = _message_or_404(_validate_identifier(message_id, field_name="message id"))
    updated = aml_state.update_iblade_message(str(message["id"]), {"acknowledged": True}) or {**message, "acknowledged": True}
    return IBladeMessage.model_validate(updated)


@router.get("/hosts", response_model=list[IBladeHost])
async def list_hosts(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> list[IBladeHost]:
    _ensure_state(context)
    return _serialize_hosts()


@router.put("/hosts", response_model=list[IBladeHost])
async def put_hosts(
    payload: dict[str, Any] = Body(default_factory=dict),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> list[IBladeHost]:
    _ensure_state(context)
    items = payload.get("hosts") if isinstance(payload.get("hosts"), list) else [payload.get("host") or payload]
    for item in items:
        if not isinstance(item, dict):
            continue
        host = IBladeHost.model_validate(item)
        aml_state.upsert_iblade_host(host.model_dump())
    return _serialize_hosts()


@router.get("/network", response_model=IBladeNetworkConfig)
async def get_network(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeNetworkConfig:
    _ensure_state(context)
    return IBladeNetworkConfig.model_validate(aml_state.get_iblade_network_config())


@router.put("/network", response_model=IBladeNetworkConfig)
async def put_network(
    payload: IBladeNetworkConfig,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeNetworkConfig:
    _ensure_state(context)
    return IBladeNetworkConfig.model_validate(aml_state.set_iblade_network_config(payload.model_dump()))


@router.get("/product", response_model=IBladeProductInfo)
async def get_product() -> IBladeProductInfo:
    return _product_info()


@router.get("/product/{element}", response_model=IBladeProductElement)
async def get_product_element(element: str) -> IBladeProductElement:
    product = _product_info().model_dump()
    key = _validate_identifier(element, field_name="element")
    if key not in product:
        raise HTTPException(status_code=404, detail="Product element not found")
    return IBladeProductElement(element=key, value=str(product[key]))


@router.get("/reports/configuration", response_model=IBladeReport)
async def get_configuration_report(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeReport:
    _ensure_state(context)
    return _configuration_report()


@router.get("/reports/media", response_model=IBladeReport)
async def get_media_report(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeReport:
    _ensure_state(context)
    return _media_report()


@router.get("/reports/media-count", response_model=IBladeReport)
async def get_media_count_report(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeReport:
    _ensure_state(context)
    return _media_count_report()


@router.get("/reports/volume-groups", response_model=IBladeReport)
async def get_volume_groups_report(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeReport:
    _ensure_state(context)
    return _volume_group_report()


@router.get("/status/io", response_model=IBladeIoStatus)
async def get_io_status(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeIoStatus:
    _ensure_state(context)
    return _io_status()


@router.get("/status/open-messages", response_model=list[IBladeMessage])
async def get_open_messages(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> list[IBladeMessage]:
    _ensure_state(context)
    return _sorted_open_messages()


@router.get("/system/settings", response_model=dict[str, Any])
async def get_system_settings(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    _ensure_state(context)
    return aml_state.get_iblade_system_settings()


@router.put("/system/settings", response_model=dict[str, Any])
async def put_system_settings(
    payload: dict[str, Any] = Body(default_factory=dict),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    _ensure_state(context)
    return aml_state.set_iblade_system_settings(payload)


@router.get("/system/settings/{settingname}", response_model=IBladeSetting)
async def get_system_setting(
    settingname: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeSetting:
    _ensure_state(context)
    settings = aml_state.get_iblade_system_settings()
    key = _validate_identifier(settingname, field_name="settingname")
    if key not in settings:
        raise HTTPException(status_code=404, detail="Setting not found")
    return IBladeSetting(name=key, value=settings[key])


@router.put("/system/settings/{settingname}", response_model=IBladeSetting)
async def put_system_setting(
    settingname: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeSetting:
    _ensure_state(context)
    key = _validate_identifier(settingname, field_name="settingname")
    value = payload.get("value") if isinstance(payload, dict) and "value" in payload else payload
    settings = aml_state.set_iblade_system_settings({key: value})
    return IBladeSetting(name=key, value=settings[key])


@router.post("/system/snapshot", response_model=IBladeJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_snapshot(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeJobResponse:
    _ensure_state(context)
    return _queue_job("iblade-snapshot", "System snapshot queued")


@router.post("/system/save-configuration", response_model=IBladeJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def save_configuration(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeJobResponse:
    _ensure_state(context)
    return _queue_job("iblade-save-config", "Configuration save queued")


@router.post("/system/restore-configuration", response_model=IBladeJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def restore_configuration(
    payload: dict[str, Any] | None = Body(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeJobResponse:
    _ensure_state(context)
    return _queue_job("iblade-restore-config", "Configuration restore queued", payload or {})


@router.post("/system/fwupgrade", response_model=IBladeJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def start_firmware_upgrade(
    payload: dict[str, Any] | None = Body(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeJobResponse:
    _ensure_state(context)
    return _queue_job("iblade-fwupgrade", "Firmware upgrade queued", payload or {})


@router.post("/system/reboot", response_model=IBladeJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def reboot_system(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeJobResponse:
    _ensure_state(context)
    return _queue_job("iblade-reboot", "System reboot queued")


@router.post("/operations/assignment", response_model=IBladeJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def assignment_operation(
    payload: dict[str, Any] | None = Body(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeJobResponse:
    _ensure_state(context)
    data = payload or {}
    index = int(data.get("index", 1))
    tapes = [str(item) for item in data.get("tapes", data.get("barcodes", []))]
    if tapes:
        group = _volume_group_or_404(index)
        merged = list(dict.fromkeys([*list(group.get("tapes", [])), *tapes]))
        aml_state.update_iblade_volume_group(index, {"tapes": merged})
    return _queue_job("iblade-assignment", f"Tape assignment queued for volume group {index}", data)


@router.post("/operations/merge", response_model=IBladeJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def merge_operation(
    payload: dict[str, Any] | None = Body(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeJobResponse:
    _ensure_state(context)
    data = payload or {}
    source_index = int(data.get("source", 1))
    destination_index = int(data.get("destination", 2))
    if source_index != destination_index:
        source = _volume_group_or_404(source_index)
        destination = _volume_group_or_404(destination_index)
        merged = list(dict.fromkeys([*destination.get("tapes", []), *source.get("tapes", [])]))
        groups = [item.model_dump() for item in _serialize_volume_groups() if item.index != source_index]
        for item in groups:
            if int(item["index"]) == destination_index:
                item["tapes"] = merged
        aml_state.replace_iblade_volume_groups(groups)
    return _queue_job("iblade-merge", "Volume group merge queued", data)


@router.post("/operations/prepare-export", response_model=IBladeJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def prepare_export_operation(
    payload: dict[str, Any] | None = Body(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeJobResponse:
    _ensure_state(context)
    return _queue_job("iblade-prepare-export", "Prepare export job queued", payload or {})


@router.post("/operations/volume-groups/repair", response_model=IBladeJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def repair_volume_group_operation(
    payload: dict[str, Any] | None = Body(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeJobResponse:
    _ensure_state(context)
    data = payload or {}
    index = int(data.get("index", 1))
    aml_state.update_iblade_volume_group(index, {"state": "READY", "reason": "NONE"})
    return _queue_job("iblade-vg-repair", f"Repair queued for volume group {index}", data)


@router.post("/operations/replicate", response_model=IBladeJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def replicate_operation(
    payload: dict[str, Any] | None = Body(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeJobResponse:
    _ensure_state(context)
    return _queue_job("iblade-replicate", "Replication queued", payload or {})


@router.post("/operations/safe-repair", response_model=IBladeJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def safe_repair_operation(
    payload: dict[str, Any] | None = Body(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeJobResponse:
    _ensure_state(context)
    data = payload or {}
    index = int(data.get("index", 1))
    aml_state.update_iblade_volume_group(index, {"state": "READY", "reason": "NONE"})
    return _queue_job("iblade-safe-repair", f"Safe repair queued for volume group {index}", data)


@router.get("/volume-groups/{index}", response_model=IBladeVolumeGroup)
async def get_volume_group(
    index: int,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeVolumeGroup:
    _ensure_state(context)
    return IBladeVolumeGroup.model_validate(_volume_group_or_404(index))


@router.put("/volume-groups/{index}", response_model=IBladeVolumeGroup)
async def put_volume_group(
    index: int,
    payload: dict[str, Any] = Body(default_factory=dict),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeVolumeGroup:
    _ensure_state(context)
    _volume_group_or_404(index)
    updated = aml_state.update_iblade_volume_group(index, payload)
    return IBladeVolumeGroup.model_validate(updated or _volume_group_or_404(index))
