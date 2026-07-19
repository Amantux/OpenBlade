"""Pydantic models for iBlade compatibility routes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class IBladeModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class StrictIBladeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CodeDescription(IBladeModel):
    code: str
    description: str


class IBladeMessage(IBladeModel):
    id: str
    code: str
    severity: str
    summary: str
    description: str
    action: str
    created_at: str
    acknowledged: bool


class IBladeNasDrive(IBladeModel):
    serialNumber: str
    model: str
    status: str
    state: str


class IBladeHost(IBladeModel):
    id: str
    hostname: str
    ip: str
    wwn: str
    connection_type: str
    state: str
    # iBlade WS Rev A models host adds/updates as requiring a reboot ("The system
    # must be rebooted before the change will take effect"). Signalled per-response
    # on mutating operations; default False on reads.
    reboot_required: bool = False


class IBladeHostUpdate(IBladeModel):
    id: str | None = None
    hostname: str | None = None
    wwn: str | None = None
    connection_type: str | None = None
    state: str | None = None


class IBladeNetworkConfig(IBladeModel):
    hostname: str
    management_ip: str
    subnet_mask: str
    gateway: str
    dns: list[str] = Field(default_factory=list)
    mtu: int
    vlan: int | None = None
    bondMode: str | None = None


class IBladeProductInfo(IBladeModel):
    product: str
    model: str
    serial: str
    firmware: str
    software: str
    vendor: str
    build: str


class IBladeProductElement(IBladeModel):
    element: str
    value: str


class IBladeReport(IBladeModel):
    generated_at: str
    items: list[dict[str, Any]] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


class IBladeIoStatus(IBladeModel):
    activeTransfers: int
    queueDepth: int
    throughputMBps: int
    activeDrives: list[str] = Field(default_factory=list)


class IBladeJobResponse(IBladeModel):
    job_id: str
    status: str
    message: str


class IBladeJob(IBladeModel):
    id: str
    type: str
    status: str
    opened: str
    closed: str | None = None
    description: str
    progress: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class IBladeJobUpdate(IBladeModel):
    id: str
    job_state: str | int


class IBladeJobsUpdateRequest(IBladeModel):
    jobs: list[IBladeJobUpdate] = Field(default_factory=list)


class IBladeJobStateUpdate(IBladeModel):
    job_state: str | int


class IBladeVolumeGroup(IBladeModel):
    index: int
    name: str
    state: str
    reason: str
    mediaCount: int
    policy: str
    tapes: list[str] = Field(default_factory=list)


class IBladeSetting(IBladeModel):
    name: str
    value: Any


class IBladeNetworkUpdate(StrictIBladeModel):
    hostname: str | None = None
    management_ip: str | None = None
    subnet_mask: str | None = None
    gateway: str | None = None
    dns: list[str] | None = None
    mtu: int | None = Field(default=None, ge=576, le=9000, strict=True)
    vlan: int | None = Field(default=None, ge=1, le=4094, strict=True)
    bondMode: str | None = None


class IBladeMessageCloseRequest(StrictIBladeModel):
    closed_by: str = Field(min_length=1)


class IBladeMessagesCloseRequest(StrictIBladeModel):
    closed_by: str = Field(min_length=1)
    ids: list[str] = Field(default_factory=list)
    close_all: bool = False


class IBladeAssignmentOperationRequest(StrictIBladeModel):
    index: int = Field(default=1, ge=1, strict=True)
    tapes: list[str] = Field(default_factory=list)
    barcodes: list[str] = Field(default_factory=list)


class IBladeMergeOperationRequest(StrictIBladeModel):
    source: int = Field(default=1, ge=1, strict=True)
    destination: int = Field(default=2, ge=1, strict=True)


class IBladePrepareExportOperationRequest(StrictIBladeModel):
    index: int = Field(default=1, ge=1, strict=True)


class IBladeRepairOperationRequest(StrictIBladeModel):
    index: int = Field(default=1, ge=1, strict=True)


class IBladeReplicateOperationRequest(StrictIBladeModel):
    source: int = Field(default=1, ge=1, strict=True)
    destination: int = Field(default=2, ge=1, strict=True)


class IBladeSafeRepairOperationRequest(StrictIBladeModel):
    index: int = Field(default=1, ge=1, strict=True)
