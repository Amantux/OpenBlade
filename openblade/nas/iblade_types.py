"""Pydantic models for iBlade compatibility routes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class IBladeModel(BaseModel):
    model_config = ConfigDict(extra="allow")


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


class IBladeHost(IBladeModel):
    id: str
    hostname: str
    ip: str
    wwn: str
    connection_type: str
    state: str


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
