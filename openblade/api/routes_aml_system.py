"""AML system and network management routes."""

from __future__ import annotations

import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from openblade.api import aml_state
from openblade.api.aml_state import (
    get_aml_audit_log,
    get_aml_callhome_config,
    get_aml_debug_config,
    get_aml_email_config,
    get_aml_emulator_latency_config,
    get_aml_ha_config,
    get_aml_network_config,
    get_aml_proxy_config,
    get_aml_remote_config,
    get_aml_services,
    get_aml_snmp_config,
    get_aml_syslog_config,
    get_aml_system_backup_status,
    get_aml_system_config,
    get_aml_system_preferences,
    get_aml_system_security,
    get_aml_system_started_at,
    set_aml_emulator_latency_config,
    set_aml_system_preferences,
)
from openblade.api.routes_aml_auth import WSResultCode, _ensure_state, _require_admin, require_auth
from openblade.bootstrap import AppContext, get_context
from openblade.catalog.models import AmlUser

router = APIRouter()

_SYSTEM_MODEL = "Scalar i3"
_FIRMWARE_VERSION = "1.0.0-mock"
_SOFTWARE_VERSION = "0.1.0"
_API_VERSION = "v1"
_BUILD_DATE = "2024-01-15T06:00:00Z"
_BUILD_NUMBER = "20240115.1"
_INSTALLED_DATE = "2024-01-01T00:00:00Z"
_DEFAULT_CERTIFICATE_IDS = {"cert-001"}
_DEFAULT_CERTIFICATE_NAMES = {"default", "system-default"}


def _volume_response_payload(volume: dict[str, Any]) -> dict[str, Any]:
    name = str(volume.get("name", ""))
    total = int(volume.get("total", 0))
    used = int(volume.get("used", 0))
    free = int(volume.get("free", max(total - used, 0)))
    percent = volume.get("percent")
    if percent is None:
        percent = int((used / total) * 100) if total else 0
    return {
        "name": name,
        "mountPoint": str(volume.get("mountPoint", "/" if name == "system" else f"/{name}")),
        "total": total,
        "used": used,
        "free": free,
        "percent": int(percent),
        "fsType": str(volume.get("fsType", "ext4" if name == "system" else "xfs")),
    }



def _backup_status_payload(backup_status: dict[str, Any]) -> dict[str, Any]:
    return {
        "lastBackup": backup_status.get("lastBackup"),
        "location": backup_status.get("location"),
        "size": int(backup_status.get("size", 0)),
        "status": str(backup_status.get("status", backup_status.get("state", "idle"))),
    }



def _update_status_payload(update_status: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": str(update_status.get("status", update_status.get("state", "idle"))),
        "currentUpdate": update_status.get("currentUpdate"),
        "progress": int(update_status.get("progress", 0)),
        "lastChecked": update_status.get("lastChecked"),
        "lastInstalled": update_status.get("lastInstalled"),
    }



def _diagnostics_payload(diag: dict[str, Any]) -> dict[str, Any]:
    timestamp = diag.get("timestamp") or diag.get("lastRun") or _iso(_now())
    return {"timestamp": str(timestamp), "tests": list(diag.get("tests", []))}



def _support_bundle_payload(bundle: dict[str, Any]) -> dict[str, Any]:
    filename = bundle.get("filename")
    location = bundle.get("location")
    if location is None and filename:
        location = f"/var/support/{filename}"
    return {
        "lastGenerated": bundle.get("lastGenerated", bundle.get("createdAt")),
        "location": location,
        "size": int(bundle.get("size", 0)),
        "status": str(bundle.get("status", bundle.get("state", "idle"))),
    }



def _certificate_summary(cert: dict[str, Any]) -> dict[str, Any]:
    expiry = cert.get("expiry")
    if expiry is None and cert.get("notAfter") is not None:
        expiry = str(cert["notAfter"])[:10]
    return {
        "name": str(cert.get("name") or cert.get("id") or "certificate"),
        "subject": str(cert.get("subject", "")),
        "expiry": expiry,
        "status": str(cert.get("status", "unknown")),
    }



def _default_certificate_index(certificates: list[dict[str, Any]]) -> int:
    return next(
        (
            idx
            for idx, cert in enumerate(certificates)
            if cert.get("id") in _DEFAULT_CERTIFICATE_IDS or cert.get("name") in _DEFAULT_CERTIFICATE_NAMES
        ),
        0 if certificates else -1,
    )



def _sync_default_certificate(cert_info: dict[str, Any]) -> None:
    certificates = aml_state.get_aml_system_certificates()
    cert_payload = {
        "id": "cert-001",
        "name": "default",
        "subject": str(cert_info.get("subject", "CN=OpenBlade")),
        "issuer": str(cert_info.get("issuer", "CN=OpenBlade CA")),
        "notBefore": str(cert_info.get("notBefore", "2024-01-01T00:00:00Z")),
        "notAfter": str(cert_info.get("notAfter", "2025-01-01T00:00:00Z")),
        "fingerprint": str(cert_info.get("fingerprint", "AA:BB:CC:DD")),
        "status": str(cert_info.get("status", "active")),
        "type": "self-signed",
    }
    index = _default_certificate_index(certificates)
    if index >= 0:
        existing = certificates[index]
        cert_payload["id"] = str(existing.get("id", cert_payload["id"]))
        cert_payload["name"] = str(existing.get("name", cert_payload["name"]))
        cert_payload["type"] = str(existing.get("type", cert_payload["type"]))
        certificates[index] = {**existing, **cert_payload}
    else:
        certificates.append(cert_payload)
    aml_state.set_aml_system_certificates(certificates)


class Interface(BaseModel):
    name: str
    type: str
    ip: str
    mask: str
    gateway: str
    mac: str
    status: str
    speed: str
    duplex: str


class NetworkConfig(BaseModel):
    interfaces: list[Interface]
    dns: dict[str, Any]
    ntp: dict[str, Any]
    hostname: str
    domain: str


class NetworkConfigResponse(BaseModel):
    networkConfig: NetworkConfig


class InterfaceListResource(BaseModel):
    interface: list[Interface]


class InterfaceListResponse(BaseModel):
    interfaceList: InterfaceListResource


class InterfaceResponse(BaseModel):
    interface: Interface


class DNSConfig(BaseModel):
    primary: str
    secondary: str
    search: list[str]
    domain: str


class DNSConfigResponse(BaseModel):
    dnsConfig: DNSConfig


class Route(BaseModel):
    destination: str
    mask: str
    gateway: str
    interface: str
    metric: int


class RouteListResource(BaseModel):
    route: list[Route]


class RouteListResponse(BaseModel):
    routeList: RouteListResource


class NTPConfig(BaseModel):
    enabled: bool
    servers: list[str]
    status: str
    lastSync: str | None = None


class NTPConfigResponse(BaseModel):
    ntpConfig: NTPConfig


class SystemInfo(BaseModel):
    hostname: str
    model: str
    serialNumber: str
    firmware: str
    uptime: int
    cpuUsage: int
    memUsage: int
    diskUsage: int


class SystemInfoResponse(BaseModel):
    systemInfo: SystemInfo


class SystemDetail(BaseModel):
    os: str
    kernel: str
    arch: str
    cpuModel: str
    cpuCount: int
    totalMem: int
    totalDisk: int
    installedDate: str


class SystemDetailResponse(BaseModel):
    systemDetail: SystemDetail


class VersionInfo(BaseModel):
    firmware: str
    software: str
    api: str
    buildDate: str
    buildNumber: str


class VersionInfoResponse(BaseModel):
    versionInfo: VersionInfo


class SystemStatus(BaseModel):
    overall: str
    cpu: str
    memory: str
    disk: str
    network: str
    services: str


class SystemStatusResponse(BaseModel):
    systemStatus: SystemStatus


class UptimeInfo(BaseModel):
    seconds: int
    formatted: str
    bootTime: str


class UptimeInfoResponse(BaseModel):
    uptimeInfo: UptimeInfo


class SystemConfig(BaseModel):
    hostname: str
    timezone: str
    locale: str
    dateFormat: str
    temperatureUnit: str


class SystemConfigResponse(BaseModel):
    systemConfig: SystemConfig


class HostnameValue(BaseModel):
    value: str


class HostnameResponse(BaseModel):
    hostname: HostnameValue


class TimezoneValue(BaseModel):
    value: str
    offset: str


class TimezoneResponse(BaseModel):
    timezone: TimezoneValue


class SystemTime(BaseModel):
    utc: str
    local: str
    timezone: str
    ntp: bool


class SystemTimeResponse(BaseModel):
    systemTime: SystemTime


class LocaleConfig(BaseModel):
    language: str
    dateFormat: str
    timeFormat: str
    temperatureUnit: str


class LocaleResponse(BaseModel):
    locale: LocaleConfig


class SecurityConfig(BaseModel):
    tlsEnabled: bool
    tlsVersion: str
    cipherSuites: list[str]
    certExpiry: str | None = None
    sshEnabled: bool
    loginBanner: str


class SecurityConfigResponse(BaseModel):
    securityConfig: SecurityConfig


class CertInfo(BaseModel):
    subject: str
    issuer: str
    notBefore: str
    notAfter: str
    fingerprint: str


class CertInfoResponse(BaseModel):
    certInfo: CertInfo


class SNMPConfig(BaseModel):
    enabled: bool
    version: str
    community: str
    trapHosts: list[str]
    contact: str
    location: str


class SNMPConfigResponse(BaseModel):
    snmpConfig: SNMPConfig


class Trap(BaseModel):
    timestamp: str
    oid: str
    value: str
    host: str


class TrapListResource(BaseModel):
    trap: list[Trap]


class TrapListResponse(BaseModel):
    trapList: TrapListResource


class EmailConfig(BaseModel):
    enabled: bool
    smtpHost: str
    smtpPort: int
    smtpUser: str
    from_: str = Field(alias="from")
    tls: bool
    recipients: list[str]

    model_config = ConfigDict(populate_by_name=True)


class EmailConfigResponse(BaseModel):
    emailConfig: EmailConfig


class SyslogConfig(BaseModel):
    enabled: bool
    host: str
    port: int
    protocol: str
    facility: str
    severity: str


class SyslogConfigResponse(BaseModel):
    syslogConfig: SyslogConfig


class Volume(BaseModel):
    name: str
    mountPoint: str
    total: int
    used: int
    free: int
    percent: int
    fsType: str


class StorageInfo(BaseModel):
    volumes: list[Volume]


class StorageInfoResponse(BaseModel):
    storageInfo: StorageInfo


class VolumeResponse(BaseModel):
    volume: Volume


class Service(BaseModel):
    name: str
    status: str
    pid: int | None = None
    uptime: int
    description: str


class ServiceListResource(BaseModel):
    service: list[Service]


class ServiceListResponse(BaseModel):
    serviceList: ServiceListResource


class ServiceResponse(BaseModel):
    service: Service


class BackupStatus(BaseModel):
    lastBackup: str | None = None
    location: str | None = None
    size: int
    status: str


class BackupStatusResponse(BaseModel):
    backupStatus: BackupStatus


class BackupItem(BaseModel):
    timestamp: str
    location: str
    size: int
    status: str


class BackupListResource(BaseModel):
    backup: list[BackupItem]


class BackupListResponse(BaseModel):
    backupList: BackupListResource


class Update(BaseModel):
    name: str
    version: str
    description: str
    type: str
    size: int


class UpdateListResource(BaseModel):
    update: list[Update]


class UpdateListResponse(BaseModel):
    updateList: UpdateListResource


class UpdateStatusInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    currentUpdate: str | None = None
    progress: int
    lastChecked: str | None = None
    lastInstalled: str | None = None


class UpdateStatusResponse(BaseModel):
    updateStatus: UpdateStatusInfo


class SystemLicense(BaseModel):
    serialNumber: str
    model: str
    tier: str
    features: list[str]
    expiry: str | None = None


class SystemLicenseResponse(BaseModel):
    systemLicense: SystemLicense


class Certificate(BaseModel):
    name: str
    subject: str
    expiry: str | None = None
    status: str


class CertListResource(BaseModel):
    cert: list[Certificate]


class CertListResponse(BaseModel):
    certList: CertListResource


class DiagnosticTest(BaseModel):
    name: str
    status: str
    details: str


class DiagResult(BaseModel):
    timestamp: str
    tests: list[DiagnosticTest]


class DiagResultResponse(BaseModel):
    diagResult: DiagResult


class SupportInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lastGenerated: str | None = None
    location: str | None = None
    size: int
    status: str


class SupportInfoResponse(BaseModel):
    support: SupportInfo


class DebugInfo(BaseModel):
    logLevel: str
    debugMode: bool
    traceEnabled: bool


class DebugInfoResponse(BaseModel):
    debugInfo: DebugInfo


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sessionTimeout: int
    idleTimeout: int
    passwordPolicy: dict[str, Any]
    auditLog: bool


class PreferencesResponse(BaseModel):
    preferences: Preferences


class AuditItem(BaseModel):
    timestamp: str
    user: str
    action: str
    resource: str
    result: str
    ip: str | None = None


class AuditListResource(BaseModel):
    audit: list[AuditItem]


class AuditListResponse(BaseModel):
    auditList: AuditListResource


class PerfMetrics(BaseModel):
    cpu: int
    memory: int
    disk: int
    network: int
    libraryOps: int


class PerfMetricsResponse(BaseModel):
    perfMetrics: PerfMetrics


class PerfSample(BaseModel):
    timestamp: str
    cpu: int
    memory: int
    disk: int


class PerfHistory(BaseModel):
    samples: list[PerfSample]


class PerfHistoryResponse(BaseModel):
    perfHistory: PerfHistory


class EmulatorLatencyProfileMs(BaseModel):
    instant: int
    realistic: int
    hardware: int


class EmulatorLatencyConfig(BaseModel):
    profile: str
    profileMs: dict[str, EmulatorLatencyProfileMs]


class EmulatorLatencyConfigResponse(BaseModel):
    emulatorLatency: EmulatorLatencyConfig


class EmulatorLatencyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: str | None = None
    profileMs: dict[str, EmulatorLatencyProfileMs] | None = None


class HAStatus(BaseModel):
    enabled: bool
    role: str
    partner: str | None = None
    state: str
    lastFailover: str | None = None


class HAStatusResponse(BaseModel):
    haStatus: HAStatus


class CallHomeConfig(BaseModel):
    enabled: bool
    endpoint: str
    interval: int
    lastContact: str | None = None


class CallHomeConfigResponse(BaseModel):
    callHomeConfig: CallHomeConfig


class RemoteServiceConfig(BaseModel):
    enabled: bool
    port: int | None = None


class RemoteConfig(BaseModel):
    ssh: RemoteServiceConfig
    vnc: RemoteServiceConfig
    rdp: RemoteServiceConfig


class RemoteConfigResponse(BaseModel):
    remoteConfig: RemoteConfig


class ProxyConfig(BaseModel):
    enabled: bool
    host: str
    port: int
    user: str
    noProxy: list[str]


class ProxyConfigResponse(BaseModel):
    proxyConfig: ProxyConfig


class InterfaceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ip: str | None = None
    mask: str | None = None
    gateway: str | None = None
    duplex: str | None = None


class InterfaceUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interface: InterfaceUpdate


class DNSConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary: str | None = None
    secondary: str | None = None
    search: list[str] | None = None
    domain: str | None = None


class DNSConfigUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dnsConfig: DNSConfigUpdate


class RouteUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destination: str
    mask: str | None = None
    gateway: str | None = None
    interface: str | None = None
    metric: int | None = None


class RouteUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: RouteUpdate


class NTPConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    servers: list[str] | None = None
    status: str | None = None
    lastSync: str | None = None


class NTPConfigUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ntpConfig: NTPConfigUpdate


class SystemConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hostname: str | None = None
    timezone: str | None = None
    locale: str | None = None
    dateFormat: str | None = None
    temperatureUnit: str | None = None


class SystemConfigUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    systemConfig: SystemConfigUpdate


class HostnameUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


class HostnameUpdateEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hostname: HostnameUpdatePayload


class TimezoneUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


class TimezoneUpdateEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: TimezoneUpdatePayload


class TimeUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    utc: str

    @field_validator("utc")
    @classmethod
    def _validate_utc(cls, value: str) -> str:
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, TypeError) as exc:
            raise ValueError(f"utc must be a valid ISO-8601 timestamp, got {value!r}") from exc
        return value


class TimeUpdateEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    systemTime: TimeUpdatePayload


class LocaleConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: str | None = None
    dateFormat: str | None = None
    timeFormat: str | None = None
    temperatureUnit: str | None = None


class LocaleConfigUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locale: LocaleConfigUpdate


class SecurityConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tlsEnabled: bool | None = None
    tlsVersion: str | None = None
    cipherSuites: list[str] | None = None
    certExpiry: str | None = None
    sshEnabled: bool | None = None
    loginBanner: str | None = None


class SecurityConfigUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    securityConfig: SecurityConfigUpdate


class CertInfoUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str | None = None
    issuer: str | None = None
    notBefore: str | None = None
    notAfter: str | None = None
    fingerprint: str | None = None
    status: str | None = None


class CertInfoUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    certInfo: CertInfoUpdate


class SNMPConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    version: str | None = None
    community: str | None = None
    trapHosts: list[str] | None = None
    contact: str | None = None
    location: str | None = None


class SNMPConfigUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snmpConfig: SNMPConfigUpdate


class EmailConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    enabled: bool | None = None
    smtpHost: str | None = None
    smtpPort: int | None = None
    smtpUser: str | None = None
    from_: str | None = Field(default=None, alias="from")
    tls: bool | None = None
    recipients: list[str] | None = None


class EmailConfigUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    emailConfig: EmailConfigUpdate


class SyslogConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    host: str | None = None
    port: int | None = None
    protocol: str | None = None
    facility: str | None = None
    severity: str | None = None


class SyslogConfigUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    syslogConfig: SyslogConfigUpdate


class StorageFormatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    volume: str | None = None
    name: str | None = None


class StorageFormatPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: StorageFormatRequest


class RestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    location: str


class RestorePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    restore: RestoreRequest


class CertificateImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    subject: str | None = None
    expiry: str | None = None
    issuer: str | None = None
    notBefore: str | None = None
    notAfter: str | None = None
    fingerprint: str | None = None
    status: str | None = None
    type: str | None = None


class CertificateImportPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cert: CertificateImportRequest


class RebootRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delay: int | None = None
    force: bool | None = None


class RebootPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reboot: RebootRequest


class DebugInfoUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    logLevel: str | None = None
    debugMode: bool | None = None
    traceEnabled: bool | None = None


class DebugInfoUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    debugInfo: DebugInfoUpdate


class PreferencesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sessionTimeout: int | None = None
    idleTimeout: int | None = None
    passwordPolicy: dict[str, Any] | None = None
    auditLog: bool | None = None


class PreferencesUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preferences: PreferencesUpdate


class HAStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    role: str | None = None
    partner: str | None = None
    state: str | None = None
    lastFailover: str | None = None


class HAStatusUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    haStatus: HAStatusUpdate


class CallHomeConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    endpoint: str | None = None
    interval: int | None = None
    lastContact: str | None = None


class CallHomeConfigUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    callHomeConfig: CallHomeConfigUpdate


class RemoteServiceConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    port: int | None = None


class RemoteConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ssh: RemoteServiceConfigUpdate | None = None
    vnc: RemoteServiceConfigUpdate | None = None
    rdp: RemoteServiceConfigUpdate | None = None


class RemoteConfigUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    remoteConfig: RemoteConfigUpdate


class ProxyConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    host: str | None = None
    port: int | None = None
    user: str | None = None
    noProxy: list[str] | None = None


class ProxyConfigUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proxyConfig: ProxyConfigUpdate


class NetworkInterfaceImport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: str
    ip: str
    mask: str
    gateway: str
    mac: str
    status: str
    speed: str
    duplex: str
    enabled: bool


class NetworkConfigImport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interfaces: dict[str, NetworkInterfaceImport] | None = None
    dns: DNSConfigUpdate | None = None
    ntp: NTPConfigUpdate | None = None
    routes: list[RouteUpdate] | None = None


class SystemConfigImportData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    systemConfig: SystemConfigUpdate | None = None
    networkConfig: NetworkConfigImport | None = None
    snmpConfig: SNMPConfigUpdate | None = None
    emailConfig: EmailConfigUpdate | None = None
    syslogConfig: SyslogConfigUpdate | None = None
    securityConfig: SecurityConfigUpdate | None = None
    debugInfo: DebugInfoUpdate | None = None
    preferences: PreferencesUpdate | None = None
    haStatus: HAStatusUpdate | None = None
    callHomeConfig: CallHomeConfigUpdate | None = None
    remoteConfig: RemoteConfigUpdate | None = None
    proxyConfig: ProxyConfigUpdate | None = None


class SystemConfigImportPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config: SystemConfigImportData


def _ws_result(summary: str) -> WSResultCode:
    return WSResultCode(summary=summary)



def _job_response(job_type: str, message: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    job_id = str(uuid4())
    aml_state.set_aml_job(job_id, {"type": job_type, "status": "queued", "result": message, "metadata": metadata or {}})
    return {"job_id": job_id, "status": "queued", "message": message}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _current_utc() -> datetime:
    manual_time_utc = aml_state.get_aml_system_manual_time_utc()
    if manual_time_utc is not None:
        return datetime.fromisoformat(manual_time_utc.replace("Z", "+00:00"))
    return _now()


def _uptime_seconds() -> int:
    return int(max(time.time() - get_aml_system_started_at(), 0))


def _formatted_uptime(seconds: int) -> str:
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{days}d {hours}h {minutes}m {secs}s"


def _timezone_offset(value: str) -> str:
    offsets = {"UTC": "+00:00", "America/New_York": "-05:00", "America/Los_Angeles": "-08:00", "Europe/London": "+00:00", "Asia/Tokyo": "+09:00"}
    return offsets.get(value, "+00:00")


def _local_time_string(utc_value: datetime) -> str:
    offset = _timezone_offset(str(get_aml_system_config().get("timezone", "UTC")))
    sign = 1 if offset.startswith("+") else -1
    hours, minutes = [int(part) for part in offset[1:].split(":", 1)]
    delta = timedelta(hours=hours * sign, minutes=minutes * sign)
    return _iso(utc_value + delta)


def _serial_number(context: AppContext) -> str:
    return context.library.inventory().library_id.upper()


def _extract_payload(payload: dict[str, Any] | None, key: str) -> dict[str, Any]:
    if payload is None:
        return {}
    nested = payload.get(key)
    if isinstance(nested, dict):
        return nested
    return payload



def _model_updates(payload: BaseModel, key: str) -> dict[str, Any]:
    data = payload.model_dump(by_alias=True, exclude_none=True, exclude_unset=True)
    nested = data.get(key)
    if isinstance(nested, dict):
        return nested
    return data



def _merge_validated_model(model_cls: type[BaseModel], current: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    try:
        validated = model_cls.model_validate({**current, **{key: value for key, value in updates.items() if value is not None}})
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.errors()) from exc
    return validated.model_dump(by_alias=True)


def _record_audit(current_user: AmlUser | None, action: str, resource: str, *, result: str = "success") -> None:
    get_aml_audit_log().append(
        {
            "timestamp": _iso(_now()),
            "user": current_user.name if current_user is not None else "system",
            "action": action,
            "resource": resource,
            "result": result,
            "ip": None,
        }
    )
    if len(get_aml_audit_log()) > 1000:
        del get_aml_audit_log()[:-1000]


def _network_interfaces() -> list[dict[str, Any]]:
    return [
        {key: value for key, value in interface.items() if key != "enabled"}
        for interface in get_aml_network_config()["interfaces"].values()
    ]


def _get_interface_or_404(name: str) -> dict[str, Any]:
    interface = get_aml_network_config()["interfaces"].get(name)
    if interface is None:
        raise HTTPException(status_code=404, detail="Interface not found")
    return interface


def _get_service_or_404(name: str) -> dict[str, Any]:
    service = get_aml_services().get(name)
    if service is None:
        raise HTTPException(status_code=404, detail="Service not found")
    return service


def _get_volume_or_404(name: str, volumes: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    current_volumes = aml_state.get_aml_system_storage_volumes() if volumes is None else volumes
    volume = current_volumes.get(name)
    if volume is None:
        raise HTTPException(status_code=404, detail="Volume not found")
    return volume


def _system_cpu_usage() -> int:
    return 18


def _system_mem_usage() -> int:
    return 42


def _system_disk_usage() -> int:
    return max(
        (_volume_response_payload(volume)["percent"] for volume in aml_state.get_aml_system_storage_volumes().values()),
        default=0,
    )


def _system_network_health() -> str:
    return "good" if all(item.get("status") == "up" for item in get_aml_network_config()["interfaces"].values()) else "warning"


def _service_health() -> str:
    return "good" if all(item.get("status") == "running" for item in get_aml_services().values()) else "warning"


def _export_config() -> dict[str, Any]:
    return {
        "systemConfig": deepcopy(get_aml_system_config()),
        "networkConfig": deepcopy(get_aml_network_config()),
        "snmpConfig": deepcopy(get_aml_snmp_config()),
        "emailConfig": deepcopy(get_aml_email_config()),
        "syslogConfig": deepcopy(get_aml_syslog_config()),
        "securityConfig": deepcopy(get_aml_system_security()),
        "debugInfo": deepcopy(get_aml_debug_config()),
        "preferences": deepcopy(get_aml_system_preferences()),
        "haStatus": deepcopy(get_aml_ha_config()),
        "callHomeConfig": deepcopy(get_aml_callhome_config()),
        "remoteConfig": deepcopy(get_aml_remote_config()),
        "proxyConfig": deepcopy(get_aml_proxy_config()),
    }


def _reset_system_defaults() -> None:
    get_aml_system_config().update(
        {
            "hostname": "openblade-1",
            "timezone": "UTC",
            "locale": "en_US",
            "dateFormat": "YYYY-MM-DD",
            "temperatureUnit": "celsius",
        }
    )
    get_aml_network_config()["dns"] = {"primary": "8.8.8.8", "secondary": "8.8.4.4", "search": ["local"], "domain": "local"}
    get_aml_network_config()["ntp"] = {
        "enabled": True,
        "servers": ["pool.ntp.org", "time.cloudflare.com"],
        "status": "synced",
        "lastSync": "2024-01-15T06:00:00Z",
    }
    get_aml_network_config()["routes"] = []
    get_aml_system_security().update(
        {
            "tlsEnabled": True,
            "tlsVersion": "TLS1.3",
            "cipherSuites": ["TLS_AES_256_GCM_SHA384"],
            "certExpiry": "2025-12-31",
            "sshEnabled": True,
            "loginBanner": "",
        }
    )
    get_aml_snmp_config().update(
        {
            "enabled": True,
            "version": "v2c",
            "community": "public",
            "trapHosts": [],
            "contact": "admin@example.com",
            "location": "Data Center",
        }
    )
    get_aml_email_config().update(
        {
            "enabled": False,
            "smtpHost": "",
            "smtpPort": 587,
            "smtpUser": "",
            "from": "openblade@example.com",
            "tls": True,
            "recipients": [],
        }
    )
    get_aml_syslog_config().update(
        {
            "enabled": False,
            "host": "",
            "port": 514,
            "protocol": "UDP",
            "facility": "local0",
            "severity": "warning",
        }
    )
    get_aml_ha_config().update({"enabled": False, "role": "standalone", "partner": None, "state": "active", "lastFailover": None})
    get_aml_callhome_config().update({"enabled": False, "endpoint": "https://callhome.quantum.com", "interval": 3600, "lastContact": None})
    get_aml_debug_config().update({"logLevel": "INFO", "debugMode": False, "traceEnabled": False})
    get_aml_system_preferences().update(
        {
            "sessionTimeout": 1800,
            "idleTimeout": 900,
            "passwordPolicy": {"minLength": 8, "requireSpecial": True},
            "auditLog": True,
        }
    )
    get_aml_remote_config().update({"ssh": {"enabled": True, "port": 22}, "vnc": {"enabled": False, "port": 5900}, "rdp": {"enabled": False}})
    get_aml_proxy_config().update({"enabled": False, "host": "", "port": 8080, "user": "", "noProxy": ["localhost", "127.0.0.1"]})


# Network configuration
@router.get("/network", response_model=NetworkConfigResponse)
async def get_network_overview(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> NetworkConfigResponse:
    _ensure_state(context)
    return NetworkConfigResponse(
        networkConfig=NetworkConfig(
            interfaces=[Interface.model_validate(item) for item in _network_interfaces()],
            dns=deepcopy(get_aml_network_config()["dns"]),
            ntp=deepcopy(get_aml_network_config()["ntp"]),
            hostname=str(get_aml_system_config().get("hostname", "openblade-1")),
            domain=str(get_aml_network_config()["dns"].get("domain", "local")),
        )
    )


@router.get("/network/interfaces", response_model=InterfaceListResponse)
async def list_network_interfaces(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> InterfaceListResponse:
    _ensure_state(context)
    return InterfaceListResponse(interfaceList=InterfaceListResource(interface=[Interface.model_validate(item) for item in _network_interfaces()]))


@router.get("/network/interface/{name}", response_model=InterfaceResponse)
async def get_network_interface(
    name: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> InterfaceResponse:
    _ensure_state(context)
    interface = _get_interface_or_404(name)
    return InterfaceResponse(interface=Interface.model_validate({key: value for key, value in interface.items() if key != "enabled"}))


@router.put("/network/interface/{name}", response_model=InterfaceResponse)
async def update_network_interface(
    name: str,
    payload: InterfaceUpdate | InterfaceUpdatePayload = Body(default_factory=dict),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> InterfaceResponse:
    _ensure_state(context)
    _require_admin(current_user)
    interface = _get_interface_or_404(name)
    updates = _model_updates(payload, "interface")
    for field in ("ip", "mask", "gateway", "duplex"):
        if field in updates and updates[field] is not None:
            interface[field] = updates[field]
    _record_audit(current_user, "update", f"network/interface/{name}")
    return InterfaceResponse(interface=Interface.model_validate({key: value for key, value in interface.items() if key != "enabled"}))


@router.post("/network/interface/{name}/enable", response_model=WSResultCode)
async def enable_network_interface(
    name: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    interface = _get_interface_or_404(name)
    interface["enabled"] = True
    interface["status"] = "up"
    _record_audit(current_user, "enable", f"network/interface/{name}")
    return _ws_result(f"Enabled interface {name}")


@router.post("/network/interface/{name}/disable", response_model=WSResultCode)
async def disable_network_interface(
    name: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    interface = _get_interface_or_404(name)
    interface["enabled"] = False
    interface["status"] = "down"
    _record_audit(current_user, "disable", f"network/interface/{name}")
    return _ws_result(f"Disabled interface {name}")


@router.get("/network/dns", response_model=DNSConfigResponse)
async def get_dns_config(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> DNSConfigResponse:
    _ensure_state(context)
    return DNSConfigResponse(dnsConfig=DNSConfig.model_validate(get_aml_network_config()["dns"]))


@router.put("/network/dns", response_model=DNSConfigResponse)
async def update_dns_config(
    payload: DNSConfigUpdate | DNSConfigUpdatePayload = Body(default_factory=dict),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> DNSConfigResponse:
    _ensure_state(context)
    _require_admin(current_user)
    updates = _model_updates(payload, "dnsConfig")
    get_aml_network_config()["dns"].update({key: value for key, value in updates.items() if value is not None})
    _record_audit(current_user, "update", "network/dns")
    return DNSConfigResponse(dnsConfig=DNSConfig.model_validate(get_aml_network_config()["dns"]))


@router.get("/network/routing", response_model=RouteListResponse)
async def get_routing_table(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> RouteListResponse:
    _ensure_state(context)
    return RouteListResponse(routeList=RouteListResource(route=[Route.model_validate(item) for item in get_aml_network_config()["routes"]]))


@router.post("/network/routing", response_model=WSResultCode)
async def add_route(
    payload: RouteUpdate | RouteUpdatePayload = Body(default_factory=dict),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    route = _model_updates(payload, "route")
    destination = str(route.get("destination", "")).strip()
    if not destination:
        raise HTTPException(status_code=400, detail="Destination is required")
    get_aml_network_config()["routes"] = [item for item in get_aml_network_config()["routes"] if item.get("destination") != destination]
    get_aml_network_config()["routes"].append(
        {
            "destination": destination,
            "mask": route.get("mask", "255.255.255.0"),
            "gateway": route.get("gateway", "0.0.0.0"),
            "interface": route.get("interface", "eth0"),
            "metric": int(route.get("metric", 1)),
        }
    )
    _record_audit(current_user, "create", f"network/routing/{destination}")
    return _ws_result(f"Added route {destination}")


@router.delete("/network/routing/{destination}", response_model=WSResultCode)
async def delete_route(
    destination: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    routes = get_aml_network_config()["routes"]
    remaining = [item for item in routes if item.get("destination") != destination]
    if len(remaining) == len(routes):
        raise HTTPException(status_code=404, detail="Route not found")
    get_aml_network_config()["routes"] = remaining
    _record_audit(current_user, "delete", f"network/routing/{destination}")
    return _ws_result(f"Removed route {destination}")


@router.get("/network/ntp", response_model=NTPConfigResponse)
async def get_ntp_config(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> NTPConfigResponse:
    _ensure_state(context)
    return NTPConfigResponse(ntpConfig=NTPConfig.model_validate(get_aml_network_config()["ntp"]))


@router.put("/network/ntp", response_model=NTPConfigResponse)
async def update_ntp_config(
    payload: NTPConfigUpdate | NTPConfigUpdatePayload = Body(default_factory=dict),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> NTPConfigResponse:
    _ensure_state(context)
    _require_admin(current_user)
    updates = _model_updates(payload, "ntpConfig")
    if "servers" in updates and updates["servers"] is not None:
        get_aml_network_config()["ntp"]["servers"] = list(updates["servers"])
    if "enabled" in updates and updates["enabled"] is not None:
        get_aml_network_config()["ntp"]["enabled"] = bool(updates["enabled"])
    get_aml_network_config()["ntp"]["status"] = "synced" if get_aml_network_config()["ntp"].get("enabled") else "disabled"
    get_aml_network_config()["ntp"]["lastSync"] = _iso(_now()) if get_aml_network_config()["ntp"].get("enabled") else None
    _record_audit(current_user, "update", "network/ntp")
    return NTPConfigResponse(ntpConfig=NTPConfig.model_validate(get_aml_network_config()["ntp"]))


@router.post("/network/ntp/sync", response_model=WSResultCode)
async def sync_ntp(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    get_aml_network_config()["ntp"]["status"] = "synced"
    get_aml_network_config()["ntp"]["lastSync"] = _iso(_now())
    _record_audit(current_user, "sync", "network/ntp")
    return _ws_result("NTP synchronization completed")


@router.put("/system/network", response_model=NetworkConfigResponse)
async def update_system_network(
    payload: NetworkConfig,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> NetworkConfigResponse:
    _ensure_state(context)
    _require_admin(current_user)
    network = get_aml_network_config()
    network["interfaces"] = {
        str(item.name): {**item.model_dump(), "enabled": str(item.status).lower() != "down"}
        for item in payload.interfaces
    }
    network["dns"] = deepcopy(payload.dns)
    network["ntp"] = deepcopy(payload.ntp)
    get_aml_system_config()["hostname"] = payload.hostname
    network["dns"]["domain"] = payload.domain
    _record_audit(current_user, "update", "system/network")
    return NetworkConfigResponse(networkConfig=payload)


@router.get("/system/software", response_model=dict[str, Any])
async def get_system_software(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    _ensure_state(context)
    return {
        "model": _SYSTEM_MODEL,
        "firmware": _FIRMWARE_VERSION,
        "software": _SOFTWARE_VERSION,
        "api": _API_VERSION,
        "buildDate": _BUILD_DATE,
        "buildNumber": _BUILD_NUMBER,
    }


@router.get("/system/sensors", response_model=dict[str, list[dict[str, Any]]])
async def get_system_sensors(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, list[dict[str, Any]]]:
    _ensure_state(context)
    return {
        "sensors": [
            {"name": "ambientTemp", "value": 24.5, "unit": "C", "status": "normal"},
            {"name": "cpuTemp", "value": 47.0, "unit": "C", "status": "normal"},
            {"name": "fanTrayA", "value": 9800, "unit": "rpm", "status": "normal"},
            {"name": "fanTrayB", "value": 9750, "unit": "rpm", "status": "normal"},
        ]
    }


@router.get("/system/snapshot", response_model=dict[str, Any])
async def get_system_snapshot(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    _ensure_state(context)
    status_payload = deepcopy(get_aml_system_backup_status())
    return {
        "state": str(status_payload.get("state", "idle")),
        "lastSnapshot": status_payload.get("lastBackup"),
        "nextSnapshot": status_payload.get("nextBackup"),
        "progress": int(status_payload.get("progress", 0)),
    }


@router.post("/system/snapshot", status_code=status.HTTP_202_ACCEPTED)
async def create_system_snapshot(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    _ensure_state(context)
    _require_admin(current_user)
    status_payload = get_aml_system_backup_status()
    status_payload.update({"state": "queued", "lastBackup": _iso(_now()), "progress": 0})
    _record_audit(current_user, "create", "system/snapshot")
    return _job_response("system-snapshot", "System snapshot queued")


# System info
@router.get("/system", response_model=SystemInfoResponse)
async def get_system_overview(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> SystemInfoResponse:
    _ensure_state(context)
    return SystemInfoResponse(
        systemInfo=SystemInfo(
            hostname=str(get_aml_system_config().get("hostname", "openblade-1")),
            model=_SYSTEM_MODEL,
            serialNumber=_serial_number(context),
            firmware=_FIRMWARE_VERSION,
            uptime=_uptime_seconds(),
            cpuUsage=_system_cpu_usage(),
            memUsage=_system_mem_usage(),
            diskUsage=_system_disk_usage(),
        )
    )


@router.get("/system/info", response_model=SystemDetailResponse)
async def get_system_info(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> SystemDetailResponse:
    _ensure_state(context)
    total_disk = sum(volume["total"] for volume in aml_state.get_aml_system_storage_volumes().values())
    return SystemDetailResponse(
        systemDetail=SystemDetail(
            os="Linux",
            kernel="6.6.0-mock",
            arch="x86_64",
            cpuModel="Intel(R) Xeon(R)",
            cpuCount=8,
            totalMem=32768,
            totalDisk=total_disk,
            installedDate=_INSTALLED_DATE,
        )
    )


@router.get("/system/version", response_model=VersionInfoResponse)
async def get_system_version(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> VersionInfoResponse:
    _ensure_state(context)
    return VersionInfoResponse(versionInfo=VersionInfo(firmware=_FIRMWARE_VERSION, software=_SOFTWARE_VERSION, api=_API_VERSION, buildDate=_BUILD_DATE, buildNumber=_BUILD_NUMBER))


@router.get("/system/status", response_model=SystemStatusResponse)
async def get_system_status(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> SystemStatusResponse:
    _ensure_state(context)
    cpu = "warning" if _system_cpu_usage() >= 80 else "good"
    memory = "warning" if _system_mem_usage() >= 80 else "good"
    disk = "warning" if _system_disk_usage() >= 80 else "good"
    network = _system_network_health()
    services = _service_health()
    overall = "failed" if "failed" in {cpu, memory, disk, network, services} else "warning" if "warning" in {cpu, memory, disk, network, services} else "good"
    return SystemStatusResponse(systemStatus=SystemStatus(overall=overall, cpu=cpu, memory=memory, disk=disk, network=network, services=services))


@router.get("/system/uptime", response_model=UptimeInfoResponse)
async def get_system_uptime(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> UptimeInfoResponse:
    _ensure_state(context)
    seconds = _uptime_seconds()
    boot_time = _iso(_now() - timedelta(seconds=seconds))
    return UptimeInfoResponse(uptimeInfo=UptimeInfo(seconds=seconds, formatted=_formatted_uptime(seconds), bootTime=boot_time))


# System configuration (static routes first)
@router.get("/system/config/export")
async def export_system_config(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    _ensure_state(context)
    return _export_config()


@router.post("/system/config/import", response_model=WSResultCode)
async def import_system_config(
    payload: SystemConfigImportData | SystemConfigImportPayload = Body(default_factory=dict),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    config = _model_updates(payload, "config")
    if "systemConfig" in config:
        get_aml_system_config().update(config["systemConfig"])
    if "networkConfig" in config:
        network = config["networkConfig"]
        if "interfaces" in network:
            get_aml_network_config()["interfaces"] = deepcopy(network["interfaces"])
        for key in ("dns", "ntp", "routes"):
            if key in network:
                get_aml_network_config()[key] = deepcopy(network[key])
    if "snmpConfig" in config:
        get_aml_snmp_config().update(config["snmpConfig"])
    if "emailConfig" in config:
        get_aml_email_config().update(config["emailConfig"])
    if "syslogConfig" in config:
        get_aml_syslog_config().update(config["syslogConfig"])
    if "securityConfig" in config:
        get_aml_system_security().update(config["securityConfig"])
    if "debugInfo" in config:
        get_aml_debug_config().update(config["debugInfo"])
    if "preferences" in config:
        get_aml_system_preferences().update(config["preferences"])
    if "haStatus" in config:
        get_aml_ha_config().update(config["haStatus"])
    if "callHomeConfig" in config:
        get_aml_callhome_config().update(config["callHomeConfig"])
    if "remoteConfig" in config:
        get_aml_remote_config().update(config["remoteConfig"])
    if "proxyConfig" in config:
        get_aml_proxy_config().update(config["proxyConfig"])
    _record_audit(current_user, "import", "system/config")
    return _ws_result("Imported system configuration")


@router.post("/system/config/reset", response_model=WSResultCode)
async def reset_system_config(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    _reset_system_defaults()
    _record_audit(current_user, "reset", "system/config")
    return _ws_result("Reset system configuration to defaults")


@router.get("/system/config", response_model=SystemConfigResponse)
async def get_system_config(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> SystemConfigResponse:
    _ensure_state(context)
    return SystemConfigResponse(systemConfig=SystemConfig.model_validate(get_aml_system_config()))


@router.put("/system/config", response_model=SystemConfigResponse)
async def update_system_config(
    payload: SystemConfigUpdate | SystemConfigUpdatePayload = Body(default_factory=dict),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> SystemConfigResponse:
    _ensure_state(context)
    _require_admin(current_user)
    updates = _model_updates(payload, "systemConfig")
    get_aml_system_config().update({key: value for key, value in updates.items() if value is not None})
    _record_audit(current_user, "update", "system/config")
    return SystemConfigResponse(systemConfig=SystemConfig.model_validate(get_aml_system_config()))


@router.get("/system/hostname", response_model=HostnameResponse)
async def get_hostname(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> HostnameResponse:
    _ensure_state(context)
    return HostnameResponse(hostname=HostnameValue(value=str(get_aml_system_config().get("hostname", "openblade-1"))))


@router.put("/system/hostname", response_model=HostnameResponse)
async def update_hostname(
    payload: HostnameUpdatePayload | HostnameUpdateEnvelope = Body(default_factory=dict),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> HostnameResponse:
    _ensure_state(context)
    _require_admin(current_user)
    updates = _model_updates(payload, "hostname")
    value = updates.get("value")
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(status_code=400, detail="Hostname is required")
    get_aml_system_config()["hostname"] = value.strip()
    _record_audit(current_user, "update", "system/hostname")
    return HostnameResponse(hostname=HostnameValue(value=get_aml_system_config()["hostname"]))


@router.get("/system/timezone", response_model=TimezoneResponse)
async def get_timezone(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> TimezoneResponse:
    _ensure_state(context)
    timezone_value = str(get_aml_system_config().get("timezone", "UTC"))
    return TimezoneResponse(timezone=TimezoneValue(value=timezone_value, offset=_timezone_offset(timezone_value)))


@router.put("/system/timezone", response_model=TimezoneResponse)
async def update_timezone(
    payload: TimezoneUpdatePayload | TimezoneUpdateEnvelope = Body(default_factory=dict),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> TimezoneResponse:
    _ensure_state(context)
    _require_admin(current_user)
    updates = _model_updates(payload, "timezone")
    value = updates.get("value")
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(status_code=400, detail="Timezone is required")
    get_aml_system_config()["timezone"] = value.strip()
    _record_audit(current_user, "update", "system/timezone")
    return TimezoneResponse(timezone=TimezoneValue(value=get_aml_system_config()["timezone"], offset=_timezone_offset(get_aml_system_config()["timezone"])))


@router.get("/system/time", response_model=SystemTimeResponse)
async def get_system_time(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> SystemTimeResponse:
    _ensure_state(context)
    utc_value = _current_utc()
    return SystemTimeResponse(systemTime=SystemTime(utc=_iso(utc_value), local=_local_time_string(utc_value), timezone=str(get_aml_system_config().get("timezone", "UTC")), ntp=bool(get_aml_network_config()["ntp"].get("enabled", False))))


@router.put("/system/time", response_model=WSResultCode)
async def set_system_time(
    payload: TimeUpdatePayload | TimeUpdateEnvelope = Body(default_factory=dict),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    updates = _model_updates(payload, "systemTime")
    utc_value = updates.get("utc")
    if not isinstance(utc_value, str) or not utc_value.strip():
        raise HTTPException(status_code=400, detail="UTC time is required")
    aml_state.set_aml_system_manual_time_utc(utc_value.strip())
    get_aml_network_config()["ntp"]["enabled"] = False
    get_aml_network_config()["ntp"]["status"] = "manual"
    _record_audit(current_user, "update", "system/time")
    return _ws_result("System time updated")


@router.get("/system/locale", response_model=LocaleResponse)
async def get_locale(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> LocaleResponse:
    _ensure_state(context)
    return LocaleResponse(locale=LocaleConfig(language=str(get_aml_system_config().get("locale", "en_US")), dateFormat=str(get_aml_system_config().get("dateFormat", "YYYY-MM-DD")), timeFormat="HH:mm:ss", temperatureUnit=str(get_aml_system_config().get("temperatureUnit", "celsius"))))


@router.put("/system/locale", response_model=LocaleResponse)
async def update_locale(
    payload: LocaleConfigUpdate | LocaleConfigUpdatePayload = Body(default_factory=dict),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> LocaleResponse:
    _ensure_state(context)
    _require_admin(current_user)
    updates = _model_updates(payload, "locale")
    if "language" in updates:
        get_aml_system_config()["locale"] = updates["language"]
    if "dateFormat" in updates:
        get_aml_system_config()["dateFormat"] = updates["dateFormat"]
    if "temperatureUnit" in updates:
        get_aml_system_config()["temperatureUnit"] = updates["temperatureUnit"]
    time_format = updates.get("timeFormat", "HH:mm:ss")
    _record_audit(current_user, "update", "system/locale")
    return LocaleResponse(locale=LocaleConfig(language=str(get_aml_system_config().get("locale", "en_US")), dateFormat=str(get_aml_system_config().get("dateFormat", "YYYY-MM-DD")), timeFormat=str(time_format), temperatureUnit=str(get_aml_system_config().get("temperatureUnit", "celsius"))))


# Security/TLS
@router.get("/system/security", response_model=SecurityConfigResponse)
async def get_security_config(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> SecurityConfigResponse:
    _ensure_state(context)
    return SecurityConfigResponse(securityConfig=SecurityConfig.model_validate(get_aml_system_security()))


@router.put("/system/security", response_model=SecurityConfigResponse)
async def update_security_config(
    payload: SecurityConfigUpdate | SecurityConfigUpdatePayload = Body(default_factory=dict),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> SecurityConfigResponse:
    _ensure_state(context)
    _require_admin(current_user)
    updates = _model_updates(payload, "securityConfig")
    get_aml_system_security().update({key: value for key, value in updates.items() if value is not None})
    _record_audit(current_user, "update", "system/security")
    return SecurityConfigResponse(securityConfig=SecurityConfig.model_validate(get_aml_system_security()))


@router.get("/system/security/certificate", response_model=CertInfoResponse)
async def get_security_certificate(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> CertInfoResponse:
    _ensure_state(context)
    return CertInfoResponse(certInfo=CertInfo.model_validate(aml_state.get_aml_system_cert_info()))


@router.post("/system/security/certificate", response_model=WSResultCode)
async def upload_security_certificate(
    payload: CertInfoUpdate | CertInfoUpdatePayload = Body(default_factory=dict),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    data = _model_updates(payload, "certInfo")
    cert_info = aml_state.get_aml_system_cert_info()
    if data:
        cert_info.update({key: value for key, value in data.items() if value is not None})
    aml_state.set_aml_system_cert_info(cert_info)
    get_aml_system_security()["certExpiry"] = str(cert_info.get("notAfter", "2025-12-31T23:59:59Z"))[:10]
    _sync_default_certificate(cert_info)
    _record_audit(current_user, "upload", "system/security/certificate")
    return _ws_result("Certificate updated")


@router.post("/system/security/certificate/generate", response_model=WSResultCode)
async def generate_security_certificate(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    now = _now()
    cert_info = aml_state.get_aml_system_cert_info()
    cert_info.update({
        "subject": f"CN={get_aml_system_config().get('hostname', 'openblade-1')},O=OpenBlade",
        "issuer": "CN=OpenBlade Self-Signed CA",
        "notBefore": _iso(now),
        "notAfter": _iso(now + timedelta(days=365)),
        "fingerprint": now.strftime("%H:%M:%S:%f"),
        "status": "active",
    })
    aml_state.set_aml_system_cert_info(cert_info)
    get_aml_system_security()["certExpiry"] = str(cert_info["notAfter"])[:10]
    _sync_default_certificate(cert_info)
    _record_audit(current_user, "generate", "system/security/certificate")
    return _ws_result("Generated self-signed certificate")


# SNMP
@router.get("/system/snmp", response_model=SNMPConfigResponse)
async def get_snmp_config(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> SNMPConfigResponse:
    _ensure_state(context)
    return SNMPConfigResponse(snmpConfig=SNMPConfig.model_validate(get_aml_snmp_config()))


@router.put("/system/snmp", response_model=SNMPConfigResponse)
async def update_snmp_config(
    payload: SNMPConfigUpdate | SNMPConfigUpdatePayload = Body(default_factory=dict),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> SNMPConfigResponse:
    _ensure_state(context)
    _require_admin(current_user)
    updates = _model_updates(payload, "snmpConfig")
    get_aml_snmp_config().update({key: value for key, value in updates.items() if value is not None})
    _record_audit(current_user, "update", "system/snmp")
    return SNMPConfigResponse(snmpConfig=SNMPConfig.model_validate(get_aml_snmp_config()))


@router.post("/system/snmp/test", response_model=WSResultCode)
async def test_snmp(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    host = get_aml_snmp_config().get("trapHosts", ["127.0.0.1"])
    traps = aml_state.get_aml_system_recent_traps()
    traps.insert(0, {"timestamp": _iso(_now()), "oid": "1.3.6.1.4.1.3764.1", "value": "OpenBlade SNMP test", "host": host[0] if host else "127.0.0.1"})
    aml_state.set_aml_system_recent_traps(traps[:20])
    _record_audit(current_user, "test", "system/snmp")
    return _ws_result("Sent SNMP test trap")


@router.get("/system/snmp/traps", response_model=TrapListResponse)
async def list_snmp_traps(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> TrapListResponse:
    _ensure_state(context)
    return TrapListResponse(
        trapList=TrapListResource(trap=[Trap.model_validate(item) for item in aml_state.get_aml_system_recent_traps()])
    )


# Email/alerting
@router.get("/system/email", response_model=EmailConfigResponse)
async def get_email_config(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> EmailConfigResponse:
    _ensure_state(context)
    return EmailConfigResponse(emailConfig=EmailConfig.model_validate(get_aml_email_config()))


@router.put("/system/email", response_model=EmailConfigResponse)
async def update_email_config(
    payload: EmailConfigUpdate | EmailConfigUpdatePayload = Body(default_factory=dict),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> EmailConfigResponse:
    _ensure_state(context)
    _require_admin(current_user)
    updates = _model_updates(payload, "emailConfig")
    get_aml_email_config().update({key: value for key, value in updates.items() if value is not None})
    _record_audit(current_user, "update", "system/email")
    return EmailConfigResponse(emailConfig=EmailConfig.model_validate(get_aml_email_config()))


@router.post("/system/email/test", response_model=WSResultCode)
async def test_email(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    _record_audit(current_user, "test", "system/email")
    return _ws_result("Sent test email")


# Syslog
@router.get("/system/syslog", response_model=SyslogConfigResponse)
async def get_syslog_config(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> SyslogConfigResponse:
    _ensure_state(context)
    return SyslogConfigResponse(syslogConfig=SyslogConfig.model_validate(get_aml_syslog_config()))


@router.put("/system/syslog", response_model=SyslogConfigResponse)
async def update_syslog_config(
    payload: SyslogConfigUpdate | SyslogConfigUpdatePayload = Body(default_factory=dict),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> SyslogConfigResponse:
    _ensure_state(context)
    _require_admin(current_user)
    updates = _model_updates(payload, "syslogConfig")
    get_aml_syslog_config().update({key: value for key, value in updates.items() if value is not None})
    _record_audit(current_user, "update", "system/syslog")
    return SyslogConfigResponse(syslogConfig=SyslogConfig.model_validate(get_aml_syslog_config()))


@router.post("/system/syslog/test", response_model=WSResultCode)
async def test_syslog(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    _record_audit(current_user, "test", "system/syslog")
    return _ws_result("Sent test syslog message")


# Storage/Disk
@router.get("/system/storage", response_model=StorageInfoResponse)
async def get_storage_overview(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> StorageInfoResponse:
    _ensure_state(context)
    return StorageInfoResponse(
        storageInfo=StorageInfo(
            volumes=[
                Volume.model_validate(_volume_response_payload(item))
                for item in aml_state.get_aml_system_storage_volumes().values()
            ]
        )
    )


@router.post("/system/storage/format", response_model=WSResultCode)
async def format_storage_volume(
    payload: StorageFormatRequest | StorageFormatPayload = Body(default_factory=dict),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    request = _model_updates(payload, "format")
    volume_name = str(request.get("volume", request.get("name", ""))).strip()
    if not volume_name:
        raise HTTPException(status_code=400, detail="Volume is required")
    volumes = aml_state.get_aml_system_storage_volumes()
    volume = _get_volume_or_404(volume_name, volumes)
    volume["used"] = 0
    volume["free"] = volume["total"]
    volume["percent"] = 0
    aml_state.set_aml_system_storage_volumes(volumes)
    _record_audit(current_user, "format", f"system/storage/{volume_name}")
    return _ws_result(f"Formatted volume {volume_name}")


@router.get("/system/storage/{volume}", response_model=VolumeResponse)
async def get_storage_volume(
    volume: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> VolumeResponse:
    _ensure_state(context)
    return VolumeResponse(volume=Volume.model_validate(_volume_response_payload(_get_volume_or_404(volume))))


# Services
@router.get("/system/services", response_model=ServiceListResponse)
async def list_services(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> ServiceListResponse:
    _ensure_state(context)
    return ServiceListResponse(serviceList=ServiceListResource(service=[Service.model_validate(item) for item in get_aml_services().values()]))


@router.get("/system/service/{name}", response_model=ServiceResponse)
async def get_service(
    name: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> ServiceResponse:
    _ensure_state(context)
    return ServiceResponse(service=Service.model_validate(_get_service_or_404(name)))


@router.post("/system/service/{name}/start", response_model=WSResultCode)
async def start_service(
    name: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    service = _get_service_or_404(name)
    service.update({"status": "running", "pid": service.get("pid") or int(time.time()) % 100000, "uptime": 0})
    _record_audit(current_user, "start", f"system/service/{name}")
    return _ws_result(f"Started service {name}")


@router.post("/system/service/{name}/stop", response_model=WSResultCode)
async def stop_service(
    name: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    service = _get_service_or_404(name)
    service.update({"status": "stopped", "pid": None, "uptime": 0})
    _record_audit(current_user, "stop", f"system/service/{name}")
    return _ws_result(f"Stopped service {name}")


@router.post("/system/service/{name}/restart", response_model=WSResultCode)
async def restart_service(
    name: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    service = _get_service_or_404(name)
    service.update({"status": "running", "pid": int(time.time()) % 100000, "uptime": 0})
    _record_audit(current_user, "restart", f"system/service/{name}")
    return _ws_result(f"Restarted service {name}")


# Backup/Restore
@router.get("/system/backup", response_model=BackupStatusResponse)
async def get_backup_status(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> BackupStatusResponse:
    _ensure_state(context)
    return BackupStatusResponse(
        backupStatus=BackupStatus.model_validate(_backup_status_payload(aml_state.get_aml_system_backup_status()))
    )


@router.post("/system/backup", response_model=WSResultCode)
async def trigger_backup(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    timestamp = _iso(_now())
    item = {
        "timestamp": timestamp,
        "location": f"/var/backups/openblade/openblade-backup-{timestamp[:10].replace('-', '')}.tar.gz",
        "size": 1052672,
        "status": "completed",
    }
    backup_status = aml_state.get_aml_system_backup_status()
    backup_status.update(
        {
            "state": item["status"],
            "lastBackup": item["timestamp"],
            "nextBackup": None,
            "progress": 100,
            "location": item["location"],
            "size": item["size"],
            "status": item["status"],
        }
    )
    aml_state.set_aml_system_backup_status(backup_status)
    aml_state.append_aml_system_backup(item)
    _record_audit(current_user, "backup", "system/backup")
    return _ws_result("Backup completed")


@router.post("/system/restore", response_model=WSResultCode)
async def restore_backup(
    payload: RestoreRequest | RestorePayload = Body(default_factory=dict),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    request = _model_updates(payload, "restore")
    location = str(request.get("location", "")).strip()
    if not location:
        raise HTTPException(status_code=400, detail="Restore location is required")
    _record_audit(current_user, "restore", "system/restore")
    return _ws_result(f"Restore started from {location}")


@router.get("/system/backup/history", response_model=BackupListResponse)
async def get_backup_history(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> BackupListResponse:
    _ensure_state(context)
    return BackupListResponse(
        backupList=BackupListResource(
            backup=[BackupItem.model_validate(item) for item in aml_state.get_aml_system_backup_history()]
        )
    )


# Updates/Patches
@router.get("/system/updates", response_model=UpdateListResponse)
async def get_updates(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> UpdateListResponse:
    _ensure_state(context)
    return UpdateListResponse(
        updateList=UpdateListResource(update=[Update.model_validate(item) for item in aml_state.get_aml_system_available_updates()])
    )


@router.post("/system/updates/install", response_model=WSResultCode)
async def install_updates(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    update_status = aml_state.get_aml_system_update_status()
    update_status.update(
        {
            "state": "completed",
            "status": "completed",
            "currentUpdate": None,
            "progress": 100,
            "lastChecked": update_status.get("lastChecked", _iso(_now())),
            "lastInstalled": _iso(_now()),
            "message": "Installed updates",
        }
    )
    aml_state.set_aml_system_update_status(update_status)
    _record_audit(current_user, "install", "system/updates")
    return _ws_result("Installed updates")


@router.get("/system/updates/status", response_model=UpdateStatusResponse)
async def get_update_status(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> UpdateStatusResponse:
    _ensure_state(context)
    return UpdateStatusResponse(
        updateStatus=UpdateStatusInfo.model_validate(_update_status_payload(aml_state.get_aml_system_update_status()))
    )


# Licensing
@router.get("/system/license", response_model=SystemLicenseResponse)
async def get_system_license(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> SystemLicenseResponse:
    _ensure_state(context)
    return SystemLicenseResponse(systemLicense=SystemLicense(serialNumber=_serial_number(context), model=_SYSTEM_MODEL, tier="base", features=[item.get("feature", "base") for item in aml_state.list_aml_licenses()], expiry=None))


# HTTPS/Certificates
@router.get("/system/certificates", response_model=CertListResponse)
async def list_certificates(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> CertListResponse:
    _ensure_state(context)
    return CertListResponse(
        certList=CertListResource(
            cert=[Certificate.model_validate(_certificate_summary(item)) for item in aml_state.get_aml_system_certificates()]
        )
    )


@router.post("/system/certificate/import", response_model=WSResultCode)
async def import_certificate(
    payload: CertificateImportRequest | CertificateImportPayload = Body(default_factory=dict),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    request = _model_updates(payload, "cert")
    certificates = aml_state.get_aml_system_certificates()
    name = str(request.get("name", f"imported-{len(certificates) + 1}")).strip()
    subject = str(request.get("subject", f"CN={name},O=OpenBlade")).strip()
    expiry = str(request.get("expiry", "2025-12-31"))
    certificates.append(
        {
            "id": f"cert-{len(certificates) + 1:03d}",
            "name": name,
            "subject": subject,
            "issuer": request.get("issuer", "CN=OpenBlade CA"),
            "notBefore": request.get("notBefore", _iso(_now())),
            "notAfter": request.get("notAfter", f"{expiry}T23:59:59Z"),
            "fingerprint": request.get("fingerprint", f"IMPORTED-{len(certificates) + 1:03d}"),
            "status": request.get("status", "valid"),
            "type": request.get("type", "imported"),
        }
    )
    aml_state.set_aml_system_certificates(certificates)
    _record_audit(current_user, "import", f"system/certificate/{name}")
    return _ws_result(f"Imported certificate {name}")


@router.delete("/system/certificate/{name}", response_model=WSResultCode)
async def delete_certificate(
    name: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    certificates = aml_state.get_aml_system_certificates()
    remaining = [item for item in certificates if item.get("name") != name and item.get("id") != name]
    if len(remaining) == len(certificates):
        raise HTTPException(status_code=404, detail="Certificate not found")
    aml_state.set_aml_system_certificates(remaining)
    _record_audit(current_user, "delete", f"system/certificate/{name}")
    return _ws_result(f"Deleted certificate {name}")


# Reboot/Shutdown
@router.post("/system/reboot", response_model=WSResultCode)
async def reboot_system(
    payload: RebootRequest | RebootPayload = Body(default_factory=dict),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    request = _model_updates(payload, "reboot")
    delay = int(request.get("delay", 0))
    force = bool(request.get("force", False))
    _record_audit(current_user, "reboot", "system/reboot")
    return _ws_result(f"System reboot scheduled in {delay} seconds (force={str(force).lower()})")


@router.post("/system/shutdown", response_model=WSResultCode)
async def shutdown_system(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    _record_audit(current_user, "shutdown", "system/shutdown")
    return _ws_result("System shutdown scheduled")


@router.post("/system/factory-reset", response_model=WSResultCode)
async def factory_reset(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    _reset_system_defaults()
    get_aml_audit_log().clear()
    _record_audit(current_user, "factory-reset", "system/factory-reset")
    return _ws_result("Factory reset initiated")


# Diagnostics (static routes first)
@router.post("/system/diagnostics/run", response_model=WSResultCode)
async def run_full_diagnostics(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    aml_state.set_aml_system_last_diagnostics(
        {
            "state": "completed",
            "lastRun": _iso(_now()),
            "result": "passed",
            "tests": [
                {"name": "cpu", "status": "passed", "details": "CPU diagnostics completed"},
                {"name": "memory", "status": "passed", "details": "Memory diagnostics completed"},
                {"name": "disk", "status": "passed", "details": "Disk diagnostics completed"},
                {"name": "network", "status": "passed", "details": "Network diagnostics completed"},
            ],
        }
    )
    _record_audit(current_user, "run", "system/diagnostics")
    return _ws_result("Full diagnostics started")


@router.get("/system/diagnostics/results", response_model=DiagResultResponse)
async def get_full_diagnostics_results(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> DiagResultResponse:
    _ensure_state(context)
    return DiagResultResponse(
        diagResult=DiagResult.model_validate(_diagnostics_payload(aml_state.get_aml_system_last_diagnostics()))
    )


@router.get("/system/diagnostics", response_model=DiagResultResponse)
async def get_quick_diagnostics(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> DiagResultResponse:
    _ensure_state(context)
    payload = {
        "timestamp": _iso(_now()),
        "tests": [
            {"name": "cpu", "status": "passed", "details": "CPU usage normal"},
            {"name": "memory", "status": "passed", "details": "Memory usage normal"},
            {"name": "network", "status": "passed", "details": "Interfaces reachable"},
        ],
    }
    return DiagResultResponse(diagResult=DiagResult.model_validate(payload))


# Support/Debug
@router.get("/system/support", response_model=SupportInfoResponse)
async def get_support_info(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> SupportInfoResponse:
    _ensure_state(context)
    return SupportInfoResponse(
        support=SupportInfo.model_validate(_support_bundle_payload(aml_state.get_aml_system_support_bundle()))
    )


@router.post("/system/support/generate", response_model=WSResultCode)
async def generate_support_bundle(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    timestamp = _iso(_now())
    aml_state.set_aml_system_support_bundle(
        {
            "state": "ready",
            "status": "ready",
            "filename": f"openblade-support-{timestamp[:10].replace('-', '')}.tgz",
            "createdAt": timestamp,
            "lastGenerated": timestamp,
            "location": f"/var/support/openblade-support-{timestamp[:10].replace('-', '')}.tgz",
            "size": 409600,
        }
    )
    _record_audit(current_user, "generate", "system/support")
    return _ws_result("Generated support bundle")


@router.get("/system/debug", response_model=DebugInfoResponse)
async def get_debug_info(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> DebugInfoResponse:
    _ensure_state(context)
    return DebugInfoResponse(debugInfo=DebugInfo.model_validate(get_aml_debug_config()))


@router.put("/system/debug", response_model=DebugInfoResponse)
async def update_debug_info(
    payload: DebugInfoUpdate | DebugInfoUpdatePayload = Body(default_factory=dict),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> DebugInfoResponse:
    _ensure_state(context)
    _require_admin(current_user)
    updates = _model_updates(payload, "debugInfo")
    get_aml_debug_config().update({key: value for key, value in updates.items() if value is not None})
    _record_audit(current_user, "update", "system/debug")
    return DebugInfoResponse(debugInfo=DebugInfo.model_validate(get_aml_debug_config()))


# User preferences
@router.get("/system/preferences", response_model=PreferencesResponse)
async def get_preferences(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PreferencesResponse:
    _ensure_state(context)
    return PreferencesResponse(preferences=Preferences.model_validate(get_aml_system_preferences()))


@router.put("/system/preferences", response_model=PreferencesResponse)
async def update_preferences(
    payload: PreferencesUpdate | PreferencesUpdatePayload = Body(default_factory=dict),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PreferencesResponse:
    _ensure_state(context)
    _require_admin(current_user)
    updates = _model_updates(payload, "preferences")
    set_aml_system_preferences(_merge_validated_model(Preferences, get_aml_system_preferences(), updates))
    _record_audit(current_user, "update", "system/preferences")
    return PreferencesResponse(preferences=Preferences.model_validate(get_aml_system_preferences()))


# Audit log
@router.get("/system/audit", response_model=AuditListResponse)
async def get_audit_log(
    limit: int = Query(default=100, ge=0),
    offset: int = Query(default=0, ge=0),
    user: str | None = Query(default=None),
    action: str | None = Query(default=None),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> AuditListResponse:
    _ensure_state(context)
    records = get_aml_audit_log()
    if user is not None:
        records = [item for item in records if item.get("user") == user]
    if action is not None:
        records = [item for item in records if item.get("action") == action]
    return AuditListResponse(auditList=AuditListResource(audit=[AuditItem.model_validate(item) for item in records[offset : offset + limit]]))


@router.delete("/system/audit", response_model=WSResultCode)
async def clear_audit_log(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    get_aml_audit_log().clear()
    return _ws_result("Cleared audit log")


# Performance
@router.get("/system/performance", response_model=PerfMetricsResponse)
async def get_performance_metrics(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PerfMetricsResponse:
    _ensure_state(context)
    return PerfMetricsResponse(perfMetrics=PerfMetrics(cpu=_system_cpu_usage(), memory=_system_mem_usage(), disk=_system_disk_usage(), network=12, libraryOps=5))


@router.get("/system/performance/history", response_model=PerfHistoryResponse)
async def get_performance_history(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PerfHistoryResponse:
    _ensure_state(context)
    now = _now()
    samples = [
        {"timestamp": _iso(now - timedelta(minutes=idx * 5)), "cpu": 15 + idx, "memory": 40 + idx, "disk": 28 + idx}
        for idx in range(6)
    ]
    samples.reverse()
    return PerfHistoryResponse(perfHistory=PerfHistory(samples=[PerfSample.model_validate(item) for item in samples]))


@router.get("/system/emulator/latency", response_model=EmulatorLatencyConfigResponse)
async def get_emulator_latency(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> EmulatorLatencyConfigResponse:
    _ensure_state(context)
    _ = current_user
    return EmulatorLatencyConfigResponse(
        emulatorLatency=EmulatorLatencyConfig.model_validate(get_aml_emulator_latency_config())
    )


@router.put("/system/emulator/latency", response_model=EmulatorLatencyConfigResponse)
async def update_emulator_latency(
    payload: EmulatorLatencyUpdate,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> EmulatorLatencyConfigResponse:
    _ensure_state(context)
    _require_admin(current_user)
    current = get_aml_emulator_latency_config()
    updated = dict(current)
    if payload.profile is not None:
        normalized = payload.profile.strip().lower()
        if normalized not in {"instant", "realistic", "hardware", "custom"}:
            raise HTTPException(status_code=400, detail="Invalid latency profile")
        updated["profile"] = normalized
    if payload.profileMs is not None:
        updated["profileMs"] = {
            key: value.model_dump()
            for key, value in payload.profileMs.items()
        }
    stored = set_aml_emulator_latency_config(updated)
    _record_audit(current_user, "update", "system/emulator/latency")
    return EmulatorLatencyConfigResponse(
        emulatorLatency=EmulatorLatencyConfig.model_validate(stored)
    )


# HA
@router.get("/system/ha", response_model=HAStatusResponse)
async def get_ha_status(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> HAStatusResponse:
    _ensure_state(context)
    return HAStatusResponse(haStatus=HAStatus.model_validate(get_aml_ha_config()))


@router.put("/system/ha", response_model=HAStatusResponse)
async def update_ha_status(
    payload: HAStatusUpdate | HAStatusUpdatePayload = Body(default_factory=dict),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> HAStatusResponse:
    _ensure_state(context)
    _require_admin(current_user)
    updates = _model_updates(payload, "haStatus")
    get_aml_ha_config().update({key: value for key, value in updates.items() if value is not None})
    _record_audit(current_user, "update", "system/ha")
    return HAStatusResponse(haStatus=HAStatus.model_validate(get_aml_ha_config()))


@router.post("/system/ha/failover", response_model=WSResultCode)
async def failover_ha(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    get_aml_ha_config()["lastFailover"] = _iso(_now())
    get_aml_ha_config()["role"] = "secondary" if get_aml_ha_config().get("role") == "primary" else "primary"
    get_aml_ha_config()["state"] = "active"
    _record_audit(current_user, "failover", "system/ha")
    return _ws_result("HA failover completed")


@router.post("/system/ha/sync", response_model=WSResultCode)
async def sync_ha(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    get_aml_ha_config()["state"] = "synced"
    _record_audit(current_user, "sync", "system/ha")
    return _ws_result("HA configuration synchronized")


# Call Home
@router.get("/system/callhome", response_model=CallHomeConfigResponse)
async def get_callhome_config(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> CallHomeConfigResponse:
    _ensure_state(context)
    return CallHomeConfigResponse(callHomeConfig=CallHomeConfig.model_validate(get_aml_callhome_config()))


@router.put("/system/callhome", response_model=CallHomeConfigResponse)
async def update_callhome_config(
    payload: CallHomeConfigUpdate | CallHomeConfigUpdatePayload = Body(default_factory=dict),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> CallHomeConfigResponse:
    _ensure_state(context)
    _require_admin(current_user)
    updates = _model_updates(payload, "callHomeConfig")
    get_aml_callhome_config().update({key: value for key, value in updates.items() if value is not None})
    _record_audit(current_user, "update", "system/callhome")
    return CallHomeConfigResponse(callHomeConfig=CallHomeConfig.model_validate(get_aml_callhome_config()))


@router.post("/system/callhome/test", response_model=WSResultCode)
async def test_callhome(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    get_aml_callhome_config()["lastContact"] = _iso(_now())
    _record_audit(current_user, "test", "system/callhome")
    return _ws_result("Call home test completed")


# Remote access
@router.get("/system/remote", response_model=RemoteConfigResponse)
async def get_remote_config(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> RemoteConfigResponse:
    _ensure_state(context)
    return RemoteConfigResponse(remoteConfig=RemoteConfig.model_validate(get_aml_remote_config()))


@router.put("/system/remote", response_model=RemoteConfigResponse)
async def update_remote_config(
    payload: RemoteConfigUpdate | RemoteConfigUpdatePayload = Body(default_factory=dict),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> RemoteConfigResponse:
    _ensure_state(context)
    _require_admin(current_user)
    updates = _model_updates(payload, "remoteConfig")
    for key in ("ssh", "vnc", "rdp"):
        if key in updates and isinstance(updates[key], dict):
            get_aml_remote_config().setdefault(key, {}).update({inner_key: inner_value for inner_key, inner_value in updates[key].items() if inner_value is not None})
    _record_audit(current_user, "update", "system/remote")
    return RemoteConfigResponse(remoteConfig=RemoteConfig.model_validate(get_aml_remote_config()))


# Proxy
@router.get("/system/proxy", response_model=ProxyConfigResponse)
async def get_proxy_config(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> ProxyConfigResponse:
    _ensure_state(context)
    return ProxyConfigResponse(proxyConfig=ProxyConfig.model_validate(get_aml_proxy_config()))


@router.put("/system/proxy", response_model=ProxyConfigResponse)
async def update_proxy_config(
    payload: ProxyConfigUpdate | ProxyConfigUpdatePayload = Body(default_factory=dict),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> ProxyConfigResponse:
    _ensure_state(context)
    _require_admin(current_user)
    updates = _model_updates(payload, "proxyConfig")
    get_aml_proxy_config().update({key: value for key, value in updates.items() if value is not None})
    _record_audit(current_user, "update", "system/proxy")
    return ProxyConfigResponse(proxyConfig=ProxyConfig.model_validate(get_aml_proxy_config()))
