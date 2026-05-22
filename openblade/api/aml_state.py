"""Shared Authentication & Access Control simulator state for AML routes."""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import secrets
import time
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pyotp
from sqlalchemy import select

from openblade.catalog.db import get_session, init_db
from openblade.catalog.models import AmlUser
from openblade.simulator.i3_config import scalar_i3_default_config

_DEFAULT_ADMIN_PASSWORD = "password"
_DEFAULT_SERVICE_PASSWORD = "service123"

_aml_log = logging.getLogger(__name__)

def _get_admin_password() -> str:
    pw = os.environ.get("OPENBLADE_ADMIN_PASSWORD", _DEFAULT_ADMIN_PASSWORD)
    if pw == _DEFAULT_ADMIN_PASSWORD:
        _aml_log.warning(
            "Using default admin password 'password'. "
            "Set OPENBLADE_ADMIN_PASSWORD env var before deploying to production."
        )
    return pw

def _get_service_password() -> str:
    pw = os.environ.get("OPENBLADE_SERVICE_PASSWORD", _DEFAULT_SERVICE_PASSWORD)
    if pw == _DEFAULT_SERVICE_PASSWORD:
        _aml_log.warning(
            "Using default service password. "
            "Set OPENBLADE_SERVICE_PASSWORD env var before deploying to production."
        )
    return pw

# Keep module-level names for backward compat (test code may reference these)
DEFAULT_ADMIN_PASSWORD = _DEFAULT_ADMIN_PASSWORD
DEFAULT_SERVICE_PASSWORD = _DEFAULT_SERVICE_PASSWORD
_PASSWORD_SALT_BYTES = 16
_PASSWORD_KEY_BYTES = 32
_PASSWORD_ITERATIONS = 390_000


@dataclass
class StoredAsset:
    name: str | None
    date: str | None

    def as_dict(self) -> dict[str, str | None]:
        return {"name": self.name, "date": self.date}


@dataclass
class SessionRecord:
    token: str
    user_name: str
    role: int
    created_at: datetime
    expires_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "user": self.user_name,
            "role": self.role,
            "createdAt": _isoformat(self.created_at),
            "expiresAt": _isoformat(self.expires_at),
        }


@dataclass
class MFARecord:
    type: str
    enabled: bool
    secret: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "enabled": self.enabled,
            "authenticationCode": None,
        }


@dataclass
class AMLState:
    db_url: str | None = None
    sessions: dict[str, SessionRecord] = field(default_factory=dict)
    ldap_config: dict[str, Any] = field(default_factory=lambda: _default_ldap_config())
    ldap_users: dict[str, str] = field(default_factory=lambda: {"ldapuser": "ldappass"})
    ldap_certificates: list[StoredAsset] = field(default_factory=list)
    mfa_configs: dict[str, dict[str, MFARecord]] = field(default_factory=dict)
    login_activity: list[dict[str, Any]] = field(default_factory=list)
    login_mode: int = 1
    lui_access: dict[str, Any] = field(default_factory=lambda: {"mode": 2, "pin": None})
    service_access: dict[str, Any] = field(
        default_factory=lambda: {"enabled": True, "authenticationCodeExpiry": 300}
    )
    service_access_code: str = field(default_factory=lambda: _generate_numeric_code(8))
    service_access_generated_at: datetime = field(default_factory=lambda: _utcnow())
    aml_system_config: dict[str, Any] = field(default_factory=lambda: {
        "hostname": "openblade-1",
        "timezone": "UTC",
        "locale": "en_US",
        "dateFormat": "YYYY-MM-DD",
        "temperatureUnit": "celsius",
    })
    aml_network_config: dict[str, Any] = field(default_factory=lambda: {
        "interfaces": {
            "eth0": {
                "name": "eth0",
                "type": "ethernet",
                "ip": "192.168.1.100",
                "mask": "255.255.255.0",
                "gateway": "192.168.1.1",
                "mac": "00:1A:2B:3C:4D:5E",
                "status": "up",
                "speed": "1G",
                "duplex": "full",
                "enabled": True,
            },
            "eth1": {
                "name": "eth1",
                "type": "ethernet",
                "ip": "10.0.0.100",
                "mask": "255.255.255.0",
                "gateway": "10.0.0.1",
                "mac": "00:1A:2B:3C:4D:5F",
                "status": "up",
                "speed": "1G",
                "duplex": "full",
                "enabled": True,
            },
        },
        "dns": {"primary": "8.8.8.8", "secondary": "8.8.4.4", "search": ["local"], "domain": "local"},
        "ntp": {
            "enabled": True,
            "servers": ["pool.ntp.org", "time.cloudflare.com"],
            "status": "synced",
            "lastSync": "2024-01-15T06:00:00Z",
        },
        "routes": [],
    })
    aml_snmp_config: dict[str, Any] = field(default_factory=lambda: {
        "enabled": True,
        "version": "v2c",
        "community": "public",
        "trapHosts": [],
        "contact": "admin@example.com",
        "location": "Data Center",
    })
    aml_email_config: dict[str, Any] = field(default_factory=lambda: {
        "enabled": False,
        "smtpHost": "",
        "smtpPort": 587,
        "smtpUser": "",
        "from": "openblade@example.com",
        "tls": True,
        "recipients": [],
    })
    aml_syslog_config: dict[str, Any] = field(default_factory=lambda: {
        "enabled": False,
        "host": "",
        "port": 514,
        "protocol": "UDP",
        "facility": "local0",
        "severity": "warning",
    })
    aml_services: dict[str, dict[str, Any]] = field(default_factory=lambda: {
        "api": {
            "name": "api",
            "status": "running",
            "pid": 1234,
            "uptime": 86400,
            "description": "OpenBlade API",
        },
        "web": {
            "name": "web",
            "status": "running",
            "pid": 1235,
            "uptime": 86400,
            "description": "Web UI",
        },
        "archiver": {
            "name": "archiver",
            "status": "running",
            "pid": 1236,
            "uptime": 86400,
            "description": "Archive Service",
        },
    })
    aml_audit_log: list[dict[str, Any]] = field(default_factory=list)
    aml_ha_config: dict[str, Any] = field(default_factory=lambda: {
        "enabled": False,
        "role": "standalone",
        "partner": None,
        "state": "active",
        "lastFailover": None,
    })
    aml_callhome_config: dict[str, Any] = field(default_factory=lambda: {
        "enabled": False,
        "endpoint": "https://callhome.quantum.com",
        "interval": 3600,
        "lastContact": None,
    })
    aml_system_security: dict[str, Any] = field(default_factory=lambda: {
        "tlsEnabled": True,
        "tlsVersion": "TLS1.3",
        "cipherSuites": ["TLS_AES_256_GCM_SHA384"],
        "certExpiry": "2025-12-31",
        "sshEnabled": True,
        "loginBanner": "",
    })
    aml_debug_config: dict[str, Any] = field(default_factory=lambda: {"logLevel": "INFO", "debugMode": False, "traceEnabled": False})
    aml_system_preferences: dict[str, Any] = field(default_factory=lambda: {
        "sessionTimeout": 1800,
        "idleTimeout": 900,
        "passwordPolicy": {"minLength": 8, "requireSpecial": True},
        "auditLog": True,
    })
    aml_remote_config: dict[str, Any] = field(default_factory=lambda: {
        "ssh": {"enabled": True, "port": 22},
        "vnc": {"enabled": False, "port": 5900},
        "rdp": {"enabled": False},
    })
    aml_proxy_config: dict[str, Any] = field(default_factory=lambda: {
        "enabled": False,
        "host": "",
        "port": 8080,
        "user": "",
        "noProxy": ["localhost", "127.0.0.1"],
    })
    aml_system_started_at: float = field(default_factory=lambda: time.time() - 86400.0)
    session_timeout_minutes: int = 30
    password_policy: dict[str, Any] = field(
        default_factory=lambda: {
            "minLength": 8,
            "maxLength": 64,
            "minLowercase": 0,
            "minUppercase": 0,
            "minNumeric": 0,
            "minSpecial": 0,
            "disallowUsername": False,
        }
    )
    library_name: str = "OpenBlade Scalar i3"
    library_mode: str = "online"
    # Access groups: dict[name, {name, devices: list[str], hosts: list[str]}]
    access_groups: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Hosts: dict[WWPN, {WWPN, alias, groups: list[str]}]
    aml_hosts: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Licenses: dict[serialNumber, {serialNumber, type, description, status, feature, expiry}]
    aml_licenses: dict[str, dict[str, Any]] = field(default_factory=lambda: _default_aml_licenses())
    eth_blades: dict[str, dict[str, Any]] = field(default_factory=lambda: _default_eth_blades())
    fc_blades: dict[str, dict[str, Any]] = field(default_factory=lambda: _default_fc_blades())
    mgmt_blades: dict[str, dict[str, Any]] = field(default_factory=lambda: _default_mgmt_blades())
    blade_firmware: list[dict[str, Any]] = field(default_factory=lambda: _default_blade_firmware())
    drive_firmware_images: dict[str, dict[str, Any]] = field(default_factory=lambda: _default_drive_firmware_images())
    system_firmware_info: dict[str, Any] = field(default_factory=lambda: _default_system_firmware_info())
    drive_sleds: dict[str, dict[str, Any]] = field(default_factory=lambda: _default_drive_sleds())
    power_supplies: dict[str, dict[str, Any]] = field(default_factory=lambda: _default_power_supplies())
    aml_fans: dict[str, dict[str, Any]] = field(default_factory=lambda: _default_aml_fans())
    aml_robots: dict[str, dict[str, Any]] = field(default_factory=lambda: _default_aml_robots())
    aml_towers: dict[str, dict[str, Any]] = field(default_factory=lambda: _default_aml_towers())
    aml_ie_stations: dict[str, dict[str, Any]] = field(default_factory=lambda: _default_aml_ie_stations())
    aml_magazines: dict[str, dict[str, Any]] = field(default_factory=lambda: _default_aml_magazines())
    aml_partitions: dict[str, dict[str, Any]] = field(default_factory=lambda: _default_aml_partitions())
    aml_partitions_global: dict[str, Any] = field(default_factory=lambda: _default_aml_partitions_global())
    aml_media: dict[str, dict[str, Any]] = field(default_factory=lambda: _default_aml_media())
    aml_media_pools: dict[str, dict[str, Any]] = field(default_factory=lambda: _default_aml_media_pools())
    aml_drives: dict[str, dict[str, Any]] = field(default_factory=lambda: _default_aml_drives())
    aml_jobs: dict[str, dict[str, Any]] = field(default_factory=dict)
    aml_job_history: list[dict[str, Any]] = field(default_factory=list)
    aml_moves: dict[str, dict[str, Any]] = field(default_factory=dict)
    aml_mounts: dict[str, dict[str, Any]] = field(default_factory=dict)
    aml_events: list[dict[str, Any]] = field(default_factory=lambda: _default_aml_events())
    aml_ras_tickets: dict[str, dict[str, Any]] = field(default_factory=dict)
    aml_logs_store: dict[str, dict[str, Any]] = field(default_factory=lambda: _default_aml_logs_store())
    aml_alerts_store: dict[str, dict[str, Any]] = field(default_factory=dict)
    aml_tapealerts: list[dict[str, Any]] = field(default_factory=list)
    aml_notifications: dict[str, dict[str, Any]] = field(default_factory=dict)
    aml_log_level: dict[str, Any] = field(default_factory=lambda: _default_aml_log_level())
    aml_event_subscriptions: list[dict[str, Any]] = field(default_factory=list)
    aml_inventory_status: dict[str, Any] = field(
        default_factory=lambda: {
            "state": "idle",
            "startTime": None,
            "completedTime": "2024-01-15T06:00:00Z",
            "progress": 100,
            "elementsScanned": 72,
            "elementsTotal": 72,
        }
    )
    aml_import_status: dict[str, Any] = field(
        default_factory=lambda: {"state": "idle", "startTime": None, "completedTime": None}
    )
    aml_export_status: dict[str, Any] = field(
        default_factory=lambda: {"state": "idle", "startTime": None, "completedTime": None}
    )
    aml_cleaning_status: dict[str, Any] = field(
        default_factory=lambda: {"state": "idle", "startTime": None, "completedTime": None, "drives": []}
    )
    aml_drive_cleaning_reports: list[dict[str, Any]] = field(default_factory=lambda: _default_aml_drive_cleaning_reports())
    aml_drive_operation_tasks: dict[str, dict[str, Any]] = field(default_factory=lambda: _default_aml_drive_operation_tasks())
    aml_diagnostic_tests: dict[str, dict[str, Any]] = field(default_factory=lambda: _default_aml_diagnostic_tests())
    aml_diagnostic_results: dict[str, dict[str, Any]] = field(default_factory=lambda: _default_aml_diagnostic_results())
    aml_robotics_last_test_time: str | None = None
    aml_system_certificates: list[dict[str, Any]] = field(default_factory=lambda: [{
        "id": "cert-001", "name": "default", "subject": "CN=OpenBlade",
        "issuer": "CN=OpenBlade CA", "notBefore": "2024-01-01T00:00:00Z",
        "notAfter": "2025-01-01T00:00:00Z", "fingerprint": "AA:BB:CC:DD",
        "status": "active", "type": "self-signed"
    }])
    aml_system_cert_info: dict[str, Any] = field(default_factory=lambda: {
        "subject": "CN=OpenBlade", "issuer": "CN=OpenBlade CA",
        "notBefore": "2024-01-01T00:00:00Z", "notAfter": "2025-01-01T00:00:00Z",
        "fingerprint": "AA:BB:CC:DD", "status": "active"
    })
    aml_system_recent_traps: list[dict[str, Any]] = field(default_factory=list)
    aml_system_storage_volumes: dict[str, dict[str, Any]] = field(default_factory=lambda: {
        "system": {"name": "system", "total": 500, "used": 120, "free": 380, "unit": "GB", "status": "healthy"},
        "data": {"name": "data", "total": 2000, "used": 800, "free": 1200, "unit": "GB", "status": "healthy"},
    })
    aml_system_backup_status: dict[str, Any] = field(default_factory=lambda: {
        "state": "idle", "lastBackup": None, "nextBackup": None, "progress": 0
    })
    aml_system_backup_history: list[dict[str, Any]] = field(default_factory=list)
    aml_system_available_updates: list[dict[str, Any]] = field(default_factory=list)
    aml_system_update_status: dict[str, Any] = field(default_factory=lambda: {
        "state": "idle", "progress": 0, "message": "No update in progress"
    })
    aml_system_last_diagnostics: dict[str, Any] = field(default_factory=lambda: {
        "state": "idle", "lastRun": None, "result": None, "tests": []
    })
    aml_system_support_bundle: dict[str, Any] = field(default_factory=lambda: {
        "state": "idle", "filename": None, "createdAt": None, "size": 0
    })
    aml_system_manual_time_utc: str | None = None
    aml_ltfs_sections: dict[str, dict[str, Any]] = field(default_factory=lambda: _default_aml_ltfs_sections())
    aml_iscsi_blades: dict[str, dict[str, Any]] = field(default_factory=lambda: _default_aml_iscsi_blades())
    aml_advanced_ha_config: dict[str, Any] = field(default_factory=lambda: _default_aml_advanced_ha_config())
    aml_advanced_ha_nodes: dict[str, dict[str, Any]] = field(default_factory=lambda: _default_aml_advanced_ha_nodes())
    aml_ekm_config: dict[str, Any] = field(default_factory=lambda: _default_aml_ekm_config())
    aml_ekm_status: dict[str, Any] = field(default_factory=lambda: _default_aml_ekm_status())
    aml_ekm_keys: dict[str, dict[str, Any]] = field(default_factory=lambda: _default_aml_ekm_keys())
    aml_sharing_config: dict[str, Any] = field(default_factory=lambda: _default_aml_sharing_config())
    aml_sharing_status: dict[str, Any] = field(default_factory=lambda: _default_aml_sharing_status())
    aml_sharing_clients: dict[str, dict[str, Any]] = field(default_factory=lambda: _default_aml_sharing_clients())
    aml_remote_libraries: dict[str, dict[str, Any]] = field(default_factory=lambda: _default_aml_remote_libraries())
    aml_supported_media: list[dict[str, Any]] = field(default_factory=lambda: _default_aml_supported_media())
    iblade_messages: list[dict[str, Any]] = field(default_factory=lambda: _default_iblade_messages())
    iblade_hosts: dict[str, dict[str, Any]] = field(default_factory=lambda: _default_iblade_hosts())
    iblade_network_config: dict[str, Any] = field(default_factory=lambda: _default_iblade_network_config())
    iblade_system_settings: dict[str, Any] = field(default_factory=lambda: _default_iblade_system_settings())
    iblade_volume_groups: dict[int, dict[str, Any]] = field(default_factory=lambda: _default_iblade_volume_groups())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _isoformat(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _normalize_db_url(db_url: str) -> str:
    if db_url.startswith("sqlite+aiosqlite:///"):
        return db_url.replace("sqlite+aiosqlite:///", "sqlite:///", 1)
    return db_url


def _default_ldap_config() -> dict[str, Any]:
    return {
        "enabled": False,
        "primaryServer": "ldap.example.com",
        "alternateServer": None,
        "serverPort": 389,
        "secureMode": False,
        "searchUser": "cn=admin,dc=example,dc=com",
        "searchUserPassword": None,
        "usersContext": "ou=users,dc=example,dc=com",
        "groupContext": "ou=groups,dc=example,dc=com",
        "libraryAccessGroupsUser": "library-users",
        "libraryAccessGroupsAdmin": "library-admins",
        "realm": None,
        "keyDistributionCenter": None,
        "domainMapping": None,
        "keytabFile": {"name": None, "date": None},
    }


def _default_aml_licenses() -> dict[str, dict[str, Any]]:
    return {
        "LIC-BASE-001": {
            "serialNumber": "LIC-BASE-001",
            "type": "BASE",
            "description": "Base Library License",
            "status": "active",
            "feature": "base",
            "expiry": None,
        },
        "LIC-PART-001": {
            "serialNumber": "LIC-PART-001",
            "type": "PARTITION",
            "description": "Partition License (2 partitions)",
            "status": "active",
            "feature": "partitions",
            "expiry": None,
        },
    }


def _default_aml_partitions() -> dict[str, dict[str, Any]]:
    config = scalar_i3_default_config()
    partition = config["partition"]
    drives = [str(drive["id"]) for drive in config["drives"]]
    data_media = [media for media in config["media"] if media.get("role") != "cleaning"]
    cleaning_barcodes = [str(media["barcode"]) for media in config["media"] if media.get("role") == "cleaning"]
    data_slot_addresses = [str(media["slotAddress"]) for media in data_media]
    return {
        str(partition["name"]): {
            "name": str(partition["name"]),
            "id": str(partition["id"]),
            "status": str(partition["status"]),
            "type": str(partition["type"]),
            "driveCount": len(drives),
            "slotCount": int(partition["slotCount"]),
            "ieSlotCount": int(partition["ieSlotCount"]),
            "cleaningSlots": int(partition["cleaningSlots"]),
            "mediaCount": len(data_media),
            "drives": drives,
            "slotAddresses": data_slot_addresses,
            "policy": {
                "autoClean": True,
                "cleaningThreshold": 100,
                "mediaAutoAssign": True,
                "mountTimeout": 300,
                "unmountTimeout": 60,
                "ejectTimeout": 30,
                "roboticsTimeout": 120,
            },
            "access": {"mode": "readWrite", "groups": [], "hosts": []},
            "cleaning": {
                "autoClean": True,
                "threshold": 100,
                "cleaningTapeBarcode": cleaning_barcodes[0] if cleaning_barcodes else None,
                "lastCleaned": "2024-01-20T03:15:00Z",
            },
            "worm": {"enabled": False, "mode": "none"},
            "encryption": {"enabled": False, "type": "none", "keyManager": None},
            "qos": {"maxMountsPerHour": 60, "priority": "normal", "preemption": False},
            "lme": {"enabled": False, "exportPath": None},
            "alerts": [],
            "moveQueue": [],
            "quota": {
                "totalSlots": int(partition["slotCount"]),
                "usedSlots": len(data_media),
                "totalDrives": len(drives),
                "usedDrives": len(drives),
            },
            "statistics": {
                "mountCount": sum(int(drive.get("loadCount", 0)) for drive in config["drives"]),
                "unmountCount": sum(int(drive.get("loadCount", 0)) for drive in config["drives"]),
                "errorCount": sum(int(drive.get("errorCount", 0)) for drive in config["drives"]),
                "lastMount": max((str(media.get("lastLoaded")) for media in data_media if media.get("lastLoaded")), default=None),
                "lastUnmount": max((str(media.get("lastLoaded")) for media in data_media if media.get("lastLoaded")), default=None),
                "mediaUsage": [],
            },
        }
    }



def _default_aml_partitions_global() -> dict[str, Any]:
    return {
        "defaultMountTimeout": 300,
        "defaultCleaningThreshold": 100,
        "maxPartitions": 8,
        "currentPartitions": 1,
    }



def _default_aml_events() -> list[dict[str, Any]]:
    return [
        {
            "id": str(uuid4()),
            "timestamp": "2024-01-15T10:00:00Z",
            "severity": "info",
            "component": "library",
            "message": "Library initialized",
            "details": {},
        },
        {
            "id": str(uuid4()),
            "timestamp": "2024-01-15T10:05:00Z",
            "severity": "warning",
            "component": "drive",
            "message": "Drive DRV-001 cleaning recommended",
            "details": {"drive": "DRV-001"},
        },
    ]



def _default_aml_logs_store() -> dict[str, dict[str, Any]]:
    return {
        "system.log": {
            "name": "system.log",
            "size": 102400,
            "lastModified": "2024-01-15T10:00:00Z",
            "type": "system",
            "lines": [
                "2024-01-15T10:00:00Z INFO Library initialized",
                "2024-01-15T10:05:00Z WARN Drive needs cleaning",
            ],
        },
        "audit.log": {
            "name": "audit.log",
            "size": 20480,
            "lastModified": "2024-01-15T09:00:00Z",
            "type": "audit",
            "lines": ["2024-01-15T09:00:00Z admin LOGIN success"],
        },
        "error.log": {
            "name": "error.log",
            "size": 0,
            "lastModified": "2024-01-15T00:00:00Z",
            "type": "error",
            "lines": [],
        },
    }



def _default_aml_log_level() -> dict[str, Any]:
    return {
        "level": "INFO",
        "components": {"api": "INFO", "archive": "INFO", "robotics": "DEBUG"},
    }



def _default_aml_media() -> dict[str, dict[str, Any]]:
    media: dict[str, dict[str, Any]] = {}
    for item in scalar_i3_default_config()["media"]:
        barcode = str(item["barcode"])
        is_cleaning = str(item.get("role", "data")) == "cleaning"
        capacity_bytes = int(item.get("capacityBytes", 18_000_000_000 if not is_cleaning else 0))
        used_bytes = int(item.get("usedBytes", 0))
        capacity_gb = round(capacity_bytes / (1024**3), 1) if capacity_bytes > 0 else 0.0
        used_gb = round(used_bytes / (1024**3), 1) if used_bytes > 0 else 0.0
        percent_used = round((used_bytes / capacity_bytes) * 100) if capacity_bytes > 0 else 0
        media[barcode] = {
            "barcode": barcode,
            "type": str(item["type"]),
            "partition": None if item.get("partition") is None else str(item["partition"]),
            "slotAddress": str(item["slotAddress"]),
            "state": "home",
            "writeProtected": bool(item.get("writeProtected", is_cleaning)),
            "worm": bool(item.get("worm", False)),
            "generations": int(item.get("generations", 0 if is_cleaning else 1)),
            "loadCount": int(item.get("loadCount", 0)),
            "errorCount": int(item.get("errorCount", 0)),
            "lastLoaded": item.get("lastLoaded"),
            "usedBytes": used_bytes,
            "capacityBytes": capacity_bytes,
            "usedGB": used_gb,
            "capacityGB": capacity_gb,
            "percentUsed": percent_used,
            "poolName": str(item["poolName"]) if is_cleaning and item.get("poolName") else None,
            "metadata": deepcopy(item.get("metadata", {})),
            "history": [],
        }
    return media



def _normalize_aml_media_pool_id(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return normalized or f"pool-{uuid4().hex[:8]}"



def _build_aml_media_pool(
    *,
    pool_id: str,
    name: str,
    policy: str,
    max_drives: int,
    target_lto_generation: str | None,
    quota_gb: int | None,
    color: str,
    assigned_barcodes: list[str] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    normalized_pool_id = _normalize_aml_media_pool_id(pool_id)
    normalized_name = str(name).strip() or normalized_pool_id
    normalized_barcodes = [barcode.strip() for barcode in assigned_barcodes or [] if isinstance(barcode, str) and barcode.strip()]
    normalized_target = str(target_lto_generation).strip() if isinstance(target_lto_generation, str) and target_lto_generation.strip() else None
    normalized_max_drives = max(1, int(max_drives))
    normalized_quota = None if quota_gb is None else max(1, int(quota_gb))
    normalized_color = str(color).strip().upper() or "#2563EB"
    stored = {
        "id": normalized_pool_id,
        "name": normalized_name,
        "type": normalized_target or "LTO-9",
        "mediaCount": len(normalized_barcodes),
        "policy": str(policy).strip() or "standard",
        "maxDrives": normalized_max_drives,
        "targetLtoGeneration": normalized_target,
        "quotaGB": normalized_quota,
        "color": normalized_color,
        "assignedBarcodes": normalized_barcodes,
        "barcodes": list(normalized_barcodes),
        "createdAt": created_at or _isoformat(_utcnow()),
    }
    return stored



def _default_aml_media_pools() -> dict[str, dict[str, Any]]:
    return {
        "default": _build_aml_media_pool(
            pool_id="default",
            name="Default",
            policy="standard",
            max_drives=2,
            target_lto_generation="LTO-9",
            quota_gb=100000,
            color="#2563EB",
            assigned_barcodes=[],
        ),
        "cleaning": _build_aml_media_pool(
            pool_id="cleaning",
            name="Cleaning",
            policy="cleaning",
            max_drives=1,
            target_lto_generation="LTO-9-CLN",
            quota_gb=None,
            color="#6B7280",
            assigned_barcodes=[],
        ),
        "pool-critical": _build_aml_media_pool(
            pool_id="pool-critical",
            name="Critical Backups",
            policy="critical",
            max_drives=2,
            target_lto_generation="LTO-9",
            quota_gb=50000,
            color="#EF4444",
            assigned_barcodes=[],
        ),
        "pool-general": _build_aml_media_pool(
            pool_id="pool-general",
            name="General Archive",
            policy="standard",
            max_drives=3,
            target_lto_generation="LTO-9",
            quota_gb=120000,
            color="#2563EB",
            assigned_barcodes=[],
        ),
        "pool-cold": _build_aml_media_pool(
            pool_id="pool-cold",
            name="Cold Storage",
            policy="archive",
            max_drives=1,
            target_lto_generation="LTO-9",
            quota_gb=180000,
            color="#64748B",
            assigned_barcodes=[],
        ),
    }



def _default_aml_drives() -> dict[str, dict[str, Any]]:
    drives: dict[str, dict[str, Any]] = {}
    for item in scalar_i3_default_config()["drives"]:
        drive_id = str(item["id"])
        drives[drive_id] = {
            "serialNumber": str(item["serial"]),
            "hardwareSerialNumber": str(item["serial"]),
            "model": str(item["model"]),
            "type": str(item["type"]),
            "status": str(item.get("status", "online")),
            "state": str(item.get("state", "idle")),
            "partition": "partition1",
            "location": str(item["location"]),
            "firmware": str(item.get("firmware", "H9A3")),
            "loadCount": int(item.get("loadCount", 80)),
            "errorCount": int(item.get("errorCount", 0)),
            "cleaningCount": int(item.get("cleaningCount", 1)),
            "lastCleaned": item.get("lastCleaned"),
            "loadedMedia": None,
            "config": {"compression": True, "encryption": False, "speed": "400MB/s", "bufferSize": "256MB"},
            "encryptionState": {
                "enabled": False,
                "mode": "applicationManaged",
                "keyManager": None,
                "keyAlias": None,
                "status": "disabled",
            },
            "errors": [],
            "diagnosticResult": None,
        }
    return drives



def _default_iblade_messages() -> list[dict[str, Any]]:
    return [
        {
            "id": "MSG-001",
            "code": "IBLADE-1001",
            "severity": "warning",
            "summary": "Drive cleaning recommended",
            "description": "Drive DRV-001 has exceeded the recommended cleaning threshold.",
            "action": "Schedule a cleaning cycle for the affected drive.",
            "created_at": "2024-01-15T10:05:00Z",
            "acknowledged": False,
        },
        {
            "id": "MSG-002",
            "code": "IBLADE-2001",
            "severity": "info",
            "summary": "Configuration saved",
            "description": "The most recent library configuration was saved successfully.",
            "action": "No action required.",
            "created_at": "2024-01-15T09:45:00Z",
            "acknowledged": True,
        },
    ]


def _default_iblade_hosts() -> dict[str, dict[str, Any]]:
    return {
        "HOST-001": {
            "id": "HOST-001",
            "hostname": "backup-a",
            "ip": "192.168.10.21",
            "wwn": "10:00:00:00:00:00:00:01",
            "connection_type": "fibre-channel",
            "state": "connected",
        },
        "HOST-002": {
            "id": "HOST-002",
            "hostname": "backup-b",
            "ip": "192.168.10.22",
            "wwn": "10:00:00:00:00:00:00:02",
            "connection_type": "ethernet",
            "state": "standby",
        },
    }


def _default_iblade_network_config() -> dict[str, Any]:
    return {
        "hostname": "iblade-1",
        "management_ip": "192.168.10.10",
        "subnet_mask": "255.255.255.0",
        "gateway": "192.168.10.1",
        "dns": ["8.8.8.8", "1.1.1.1"],
        "mtu": 1500,
        "vlan": 110,
        "bondMode": "active-backup",
    }


def _default_iblade_system_settings() -> dict[str, Any]:
    return {
        "autoDiscovery": True,
        "defaultVolumeGroup": "VG-001",
        "exportPolicy": "manual",
        "ioThrottle": "balanced",
        "retentionLock": False,
        "serviceMode": False,
        "snapshotRetention": 5,
    }


def _default_iblade_volume_groups() -> dict[int, dict[str, Any]]:
    data_barcodes = [
        str(item["barcode"])
        for item in scalar_i3_default_config()["media"]
        if str(item.get("role", "data")) == "data"
    ]
    return {
        1: {
            "index": 1,
            "name": "VG-001",
            "state": "READY",
            "reason": "NONE",
            "mediaCount": 5,
            "policy": "standard",
            "tapes": data_barcodes[:5],
        },
        2: {
            "index": 2,
            "name": "VG-002",
            "state": "READY",
            "reason": "NONE",
            "mediaCount": max(len(data_barcodes[5:10]), 0),
            "policy": "archive",
            "tapes": data_barcodes[5:10],
        },
    }


def _default_blade_firmware() -> list[dict[str, Any]]:
    return [
        {
            "name": "eth-blade-2.1.0.bundle",
            "target": "ethernet",
            "version": "2.1.0",
            "status": "active",
            "uploadedAt": "2024-01-05T12:00:00Z",
            "size": 15728640,
            "checksum": "simulated-eth-210",
        },
        {
            "name": "fc-blade-3.2.1.bundle",
            "target": "fibre-channel",
            "version": "3.2.1",
            "status": "active",
            "uploadedAt": "2024-01-05T12:05:00Z",
            "size": 17825792,
            "checksum": "simulated-fc-321",
        },
        {
            "name": "mgmt-blade-5.0.1.bundle",
            "target": "management",
            "version": "5.0.1",
            "status": "active",
            "uploadedAt": "2024-01-05T12:10:00Z",
            "size": 33554432,
            "checksum": "simulated-mgmt-501",
        },
    ]



def _default_drive_firmware_images() -> dict[str, dict[str, Any]]:
    return {
        "lto9-h3j4.img": {
            "name": "lto9-h3j4.img",
            "version": "H3J4",
            "driveType": "LTO-9",
            "extension": ".img",
            "size": 8388608,
            "uploadedAt": "2024-01-05T13:00:00Z",
            "checksum": "simulated-h3j4",
            "active": True,
        },
        "lto9-h3j5.fmr": {
            "name": "lto9-h3j5.fmr",
            "version": "H3J5",
            "driveType": "LTO-9",
            "extension": ".fmr",
            "size": 8650752,
            "uploadedAt": "2024-01-20T13:00:00Z",
            "checksum": "simulated-h3j5",
            "active": False,
        },
    }



def _default_system_firmware_info() -> dict[str, Any]:
    return {
        "currentVersion": "6.0.1",
        "stagedPackage": None,
        "uploadedPackages": [
            {
                "name": "system-6.0.1.pkg",
                "version": "6.0.1",
                "size": 50331648,
                "uploadedAt": "2024-01-05T14:00:00Z",
                "checksum": "simulated-system-601",
                "active": True,
            }
        ],
        "status": {
            "state": "idle",
            "progress": 0,
            "message": "No firmware activation pending",
            "currentVersion": "6.0.1",
            "stagedVersion": None,
            "lastUpdated": "2024-01-05T14:00:00Z",
            "lastActivated": "2024-01-05T14:00:00Z",
        },
        "lastActivated": "2024-01-05T14:00:00Z",
    }



def _default_eth_blades() -> dict[str, dict[str, Any]]:
    return {
        "ETH-1": {
            "id": "ETH-1",
            "serialNumber": "ETHB0001",
            "model": "Ethernet Blade 4-Port",
            "status": "online",
            "firmware": "2.1.0",
            "portCount": 4,
            "ports": [
                {
                    "id": f"ETH-1-P{i}",
                    "mac": f"00:1A:2B:3C:4D:{i:02X}",
                    "ip": f"192.168.1.{10 + i}",
                    "status": "up",
                    "speed": "1G",
                    "duplex": "full",
                }
                for i in range(1, 5)
            ],
        }
    }


def _default_fc_blades() -> dict[str, dict[str, Any]]:
    return {
        "FC-1": {
            "id": "FC-1",
            "serialNumber": "FCB0001",
            "model": "FC Blade 4-Port 16Gb",
            "status": "online",
            "firmware": "3.2.1",
            "portCount": 4,
            "ports": [
                {
                    "id": f"FC-1-P{i}",
                    "portNumber": i,
                    "wwpn": f"50:00:00:00:00:00:00:0{i}",
                    "speed": "16G",
                    "status": "online",
                    "mode": "target",
                    "topology": "point-to-point",
                    "fabricLoginState": "logged-in",
                    "alias": f"FCB0001-Port{i}",
                    "statistics": {
                        "framesTx": 100000 * i,
                        "framesRx": 98000 * i,
                        "linkResets": 0,
                        "lossOfSignal": 0,
                        "crcErrors": 0,
                        "secondsSinceReset": 86400 + (i * 120),
                    },
                }
                for i in range(1, 5)
            ],
            "hpf": {
                "enabled": True,
                "mode": "auto",
                "preferredPort": 1,
                "partnerBladeSerial": "FCB0002",
                "interventionRequired": False,
                "autoRestore": True,
                "state": "protected",
            },
            "zoning": {
                "enabled": True,
                "mode": "singleInitiatorSingleTarget",
                "defaultZoneSet": "OpenBlade-ZoneSet-A",
                "activeZoneCount": 4,
                "pendingChanges": False,
            },
            "wwn": {
                "serialNumber": "FCB0001",
                "nodeWwn": "50:00:00:00:00:00:10:01",
                "virtualWwnEnabled": False,
                "virtualNodeWwn": None,
                "portWwns": [f"50:00:00:00:00:00:00:0{i}" for i in range(1, 5)],
            },
            "dataPath": {
                "serialNumber": "FCB0001",
                "status": "healthy",
                "activePaths": 4,
                "preferredPath": "fabric-a",
                "lastTest": "2024-01-15T09:30:00Z",
                "lastResult": "pass",
            },
        },
        "FC-2": {
            "id": "FC-2",
            "serialNumber": "FCB0002",
            "model": "FC Blade 4-Port 16Gb",
            "status": "online",
            "firmware": "3.2.1",
            "portCount": 4,
            "ports": [
                {
                    "id": f"FC-2-P{i}",
                    "portNumber": i,
                    "wwpn": f"50:00:00:00:00:00:00:1{i}",
                    "speed": "16G",
                    "status": "online",
                    "mode": "target",
                    "topology": "point-to-point",
                    "fabricLoginState": "logged-in",
                    "alias": f"FCB0002-Port{i}",
                    "statistics": {
                        "framesTx": 92000 * i,
                        "framesRx": 91000 * i,
                        "linkResets": 0,
                        "lossOfSignal": 0,
                        "crcErrors": 0,
                        "secondsSinceReset": 84000 + (i * 90),
                    },
                }
                for i in range(1, 5)
            ],
            "hpf": {
                "enabled": True,
                "mode": "auto",
                "preferredPort": 1,
                "partnerBladeSerial": "FCB0001",
                "interventionRequired": False,
                "autoRestore": True,
                "state": "protected",
            },
            "zoning": {
                "enabled": True,
                "mode": "singleInitiatorSingleTarget",
                "defaultZoneSet": "OpenBlade-ZoneSet-B",
                "activeZoneCount": 4,
                "pendingChanges": False,
            },
            "wwn": {
                "serialNumber": "FCB0002",
                "nodeWwn": "50:00:00:00:00:00:10:02",
                "virtualWwnEnabled": False,
                "virtualNodeWwn": None,
                "portWwns": [f"50:00:00:00:00:00:00:1{i}" for i in range(1, 5)],
            },
            "dataPath": {
                "serialNumber": "FCB0002",
                "status": "healthy",
                "activePaths": 4,
                "preferredPath": "fabric-b",
                "lastTest": "2024-01-15T09:45:00Z",
                "lastResult": "pass",
            },
        },
    }


def _default_mgmt_blades() -> dict[str, dict[str, Any]]:
    return {
        "MGMT-1": {
            "id": "MGMT-1",
            "serialNumber": "MGMT0001",
            "model": "iBlade Controller",
            "status": "active",
            "firmware": "5.0.1",
            "role": "primary",
        },
        "MGMT-2": {
            "id": "MGMT-2",
            "serialNumber": "MGMT0002",
            "model": "iBlade Controller",
            "status": "standby",
            "firmware": "5.0.1",
            "role": "secondary",
        },
    }


def _default_drive_sleds() -> dict[str, dict[str, Any]]:
    return {
        "SLED-1": {
            "id": "SLED-1",
            "serialNumber": "SLD0001",
            "model": "Drive Sled 4-Bay",
            "status": "online",
            "drives": ["DRV-001", "DRV-002"],
        }
    }


def _default_power_supplies() -> dict[str, dict[str, Any]]:
    return {
        "PSU-1": {
            "id": "PSU-1",
            "location": "left",
            "status": "ok",
            "voltage": 12.1,
            "wattage": 450,
        },
        "PSU-2": {
            "id": "PSU-2",
            "location": "right",
            "status": "ok",
            "voltage": 12.0,
            "wattage": 450,
        },
    }


def _default_aml_fans() -> dict[str, dict[str, Any]]:
    return {
        "FAN-1": {
            "id": "FAN-1",
            "location": "front-left",
            "status": "ok",
            "rpm": 3200,
            "speed": "normal",
        },
        "FAN-2": {
            "id": "FAN-2",
            "location": "front-right",
            "status": "ok",
            "rpm": 3150,
            "speed": "normal",
        },
        "FAN-3": {
            "id": "FAN-3",
            "location": "rear",
            "status": "ok",
            "rpm": 2800,
            "speed": "normal",
        },
    }


def _default_aml_robots() -> dict[str, dict[str, Any]]:
    return {
        "ROB-1": {
            "id": "ROB-1",
            "serialNumber": "ROB0001",
            "model": "Scalar i3 Robot",
            "status": "online",
            "state": "idle",
            "location": "base",
            "homeSlot": "1,1,1",
        }
    }


def _default_aml_towers() -> dict[str, dict[str, Any]]:
    return {
        "TWR-1": {
            "id": "TWR-1",
            "serialNumber": "TWR0001",
            "model": "Scalar i3 Base Module",
            "status": "online",
            "slots": 50,
            "occupiedSlots": 12,
            "drives": ["DRV-001", "DRV-002"],
        }
    }


def _default_aml_ie_stations() -> dict[str, dict[str, Any]]:
    stations: dict[str, dict[str, Any]] = {}
    for item in scalar_i3_default_config()["ieStations"]:
        slots = [deepcopy(slot) for slot in item["slots"]]
        stations[str(item["id"])] = {
            "id": str(item["id"]),
            "serialNumber": str(item["serialNumber"]),
            "status": str(item["status"]),
            "state": str(item["state"]),
            "slotCount": len(slots),
            "slots": slots,
        }
    return stations


def _default_aml_magazines() -> dict[str, dict[str, Any]]:
    return {
        "MAG-1": {
            "id": "MAG-1",
            "location": "TWR-1,col1,row1",
            "status": "online",
            "slotCount": 10,
            "occupiedSlots": 3,
            "tapes": ["VOL001L9", "VOL002L9", "VOL003L9"],
        }
    }



def _default_aml_ltfs_sections() -> dict[str, dict[str, Any]]:
    return {
        str(section["sectionNumber"]): deepcopy(section)
        for section in scalar_i3_default_config()["ltfsSections"]
    }



def _default_aml_iscsi_blades() -> dict[str, dict[str, Any]]:
    return {
        "FCB0001": {
            "serialNumber": "FCB0001",
            "enabled": False,
            "iqn": "iqn.2024-01.com.openblade:fcb0001",
            "ipAddress": "192.168.150.11",
            "subnetMask": "255.255.255.0",
            "gateway": "192.168.150.1",
            "authMode": "none",
            "mtu": 1500,
            "sessions": [],
            "targets": [
                {
                    "name": "iqn.2024-01.com.openblade:fcb0001.target0",
                    "status": "ready",
                    "luns": ["partition1"],
                }
            ],
            "initiators": [],
        },
        "FCB0002": {
            "serialNumber": "FCB0002",
            "enabled": False,
            "iqn": "iqn.2024-01.com.openblade:fcb0002",
            "ipAddress": "192.168.150.12",
            "subnetMask": "255.255.255.0",
            "gateway": "192.168.150.1",
            "authMode": "none",
            "mtu": 1500,
            "sessions": [],
            "targets": [
                {
                    "name": "iqn.2024-01.com.openblade:fcb0002.target0",
                    "status": "ready",
                    "luns": ["partition1"],
                }
            ],
            "initiators": [],
        },
    }



def _default_aml_advanced_ha_config() -> dict[str, Any]:
    return {
        "enabled": False,
        "mode": "standalone",
        "clusterName": "OpenBlade-HA",
        "heartbeatInterval": 5,
        "autoFailback": False,
    }



def _default_aml_advanced_ha_nodes() -> dict[str, dict[str, Any]]:
    return {
        "node-1": {
            "id": "node-1",
            "name": "openblade-a",
            "role": "active",
            "state": "standalone",
            "ipAddress": "192.168.100.10",
            "lastHeartbeat": "2024-01-15T10:00:00Z",
            "healthy": True,
        },
        "node-2": {
            "id": "node-2",
            "name": "openblade-b",
            "role": "standby",
            "state": "idle",
            "ipAddress": "192.168.100.11",
            "lastHeartbeat": None,
            "healthy": True,
        },
    }



def _default_aml_ekm_config() -> dict[str, Any]:
    return {
        "enabled": False,
        "primaryServer": "ekm.example.com",
        "secondaryServer": None,
        "port": 5696,
        "protocol": "kmip",
        "timeoutSeconds": 10,
        "clientCertificate": None,
    }



def _default_aml_ekm_status() -> dict[str, Any]:
    return {
        "connected": False,
        "lastTest": None,
        "error": None,
        "cacheAgeSeconds": 0,
    }



def _default_aml_ekm_keys() -> dict[str, dict[str, Any]]:
    return {
        "key-001": {
            "keyId": "key-001",
            "alias": "OpenBlade-Partition1",
            "state": "cached",
            "algorithm": "AES-256",
            "updatedAt": "2024-01-15T08:00:00Z",
        }
    }



def _default_aml_sharing_config() -> dict[str, Any]:
    return {
        "enabled": False,
        "mode": "disabled",
        "serverId": "openblade-share-1",
        "exportedPartitions": [],
    }



def _default_aml_sharing_status() -> dict[str, Any]:
    return {
        "state": "disabled",
        "connectedClients": 0,
        "lastSync": None,
        "health": "ok",
    }



def _default_aml_sharing_clients() -> dict[str, dict[str, Any]]:
    return {}



def _default_aml_remote_libraries() -> dict[str, dict[str, Any]]:
    return {}



def _default_aml_supported_media() -> list[dict[str, Any]]:
    return [
        {
            "name": "LTO-8",
            "description": "LTO Ultrium 8 data cartridge",
            "nativeCapacity": "12 TB",
            "compressedCapacity": "30 TB",
            "generations": ["LTO-7", "LTO-8"],
            "cleaning": False,
        },
        {
            "name": "LTO-9",
            "description": "LTO Ultrium 9 data cartridge",
            "nativeCapacity": "18 TB",
            "compressedCapacity": "45 TB",
            "generations": ["LTO-8", "LTO-9"],
            "cleaning": False,
        },
        {
            "name": "LTO-9-CLN",
            "description": "LTO Ultrium 9 cleaning cartridge",
            "nativeCapacity": "N/A",
            "compressedCapacity": "N/A",
            "generations": ["LTO-9"],
            "cleaning": True,
        },
    ]



def _default_aml_drive_cleaning_reports() -> list[dict[str, Any]]:
    return [
        {
            "serialNumber": "DRV-001",
            "lastCleaned": "2024-01-10T08:00:00Z",
            "mediaBarcode": "CLN001L9",
            "useCount": 0,
            "expired": False,
        },
        {
            "serialNumber": "DRV-002",
            "lastCleaned": "2024-01-08T14:00:00Z",
            "mediaBarcode": "CLN002L9",
            "useCount": 0,
            "expired": False,
        },
    ]



def _default_aml_drive_operation_tasks() -> dict[str, dict[str, Any]]:
    return {
        "task-load-drv-001": {
            "id": "task-load-drv-001",
            "componentId": "DRV-001",
            "type": "load",
            "opened": "2024-01-15T10:00:00Z",
            "closed": "2024-01-15T10:00:12Z",
            "state": 5,
            "status": "Completed",
            "description": "Loaded VOL001L9 into drive DRV-001",
            "sessionId": "session-001",
        },
        "task-unload-drv-001": {
            "id": "task-unload-drv-001",
            "componentId": "DRV-001",
            "type": "unload",
            "opened": "2024-01-15T10:30:00Z",
            "closed": "2024-01-15T10:30:10Z",
            "state": 5,
            "status": "Completed",
            "description": "Unloaded VOL001L9 from drive DRV-001",
            "sessionId": "session-001",
        },
        "task-load-drv-002": {
            "id": "task-load-drv-002",
            "componentId": "DRV-002",
            "type": "load",
            "opened": "2024-01-14T08:00:00Z",
            "closed": "2024-01-14T08:00:15Z",
            "state": 5,
            "status": "Completed",
            "description": "Loaded VOL002L9 into drive DRV-002",
            "sessionId": "session-002",
        },
        "task-unload-drv-002": {
            "id": "task-unload-drv-002",
            "componentId": "DRV-002",
            "type": "unload",
            "opened": "2024-01-14T08:45:00Z",
            "closed": "2024-01-14T08:45:09Z",
            "state": 5,
            "status": "Completed",
            "description": "Unloaded VOL002L9 from drive DRV-002",
            "sessionId": "session-002",
        },
        "task-clean-drv-001": {
            "id": "task-clean-drv-001",
            "componentId": "DRV-001",
            "type": "clean",
            "opened": "2024-01-10T08:00:00Z",
            "closed": "2024-01-10T08:04:00Z",
            "state": 5,
            "status": "Completed",
            "description": "Cleaned drive DRV-001 using CLN001L9",
            "sessionId": "session-003",
        },
    }



def _default_aml_diagnostic_tests() -> dict[str, dict[str, Any]]:
    return {
        "inventory-integrity": {
            "id": "inventory-integrity",
            "name": "Inventory Integrity",
            "description": "Validate slot inventory, media assignments, and barcode visibility.",
            "category": "library",
            "estimatedDuration": 90,
        },
        "robotics-path": {
            "id": "robotics-path",
            "name": "Robotics Path Check",
            "description": "Exercise the robot arm pathing and confirm home position transitions.",
            "category": "robotics",
            "estimatedDuration": 120,
        },
        "drive-connectivity": {
            "id": "drive-connectivity",
            "name": "Drive Connectivity",
            "description": "Verify each configured drive responds and reports healthy transport links.",
            "category": "drives",
            "estimatedDuration": 60,
        },
    }



def _default_aml_diagnostic_results() -> dict[str, dict[str, Any]]:
    return {
        "diag-result-001": {
            "id": "diag-result-001",
            "testId": "suite:default",
            "startTime": "2024-01-15T09:00:00Z",
            "endTime": "2024-01-15T09:02:30Z",
            "status": "completed",
            "passed": 3,
            "failed": 0,
            "details": [
                {
                    "name": "Inventory Integrity",
                    "status": "passed",
                    "message": "Inventory matches configured media layout.",
                },
                {
                    "name": "Robotics Path Check",
                    "status": "passed",
                    "message": "Robot arm returned to home position without errors.",
                },
                {
                    "name": "Drive Connectivity",
                    "status": "passed",
                    "message": "All configured drives reported online.",
                },
            ],
        }
    }


def _generate_numeric_code(length: int) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(length))


def _generate_totp_secret() -> str:
    return pyotp.random_base32(length=32)


def _password_bytes(password: str) -> bytes:
    return password.encode("utf-8")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(_PASSWORD_SALT_BYTES)
    derived_key = hashlib.pbkdf2_hmac(
        "sha256", _password_bytes(password), salt, _PASSWORD_ITERATIONS, dklen=_PASSWORD_KEY_BYTES
    )
    return base64.b64encode(salt + derived_key).decode("ascii")


def _decode_password_hash(password_hash: str) -> tuple[bytes, bytes] | None:
    try:
        decoded = base64.b64decode(password_hash.encode("ascii"), validate=True)
    except Exception:
        return None
    expected_length = _PASSWORD_SALT_BYTES + _PASSWORD_KEY_BYTES
    if len(decoded) != expected_length:
        return None
    return decoded[:_PASSWORD_SALT_BYTES], decoded[_PASSWORD_SALT_BYTES:]


def is_password_hash(password_hash: str) -> bool:
    return _decode_password_hash(password_hash) is not None


def verify_password(password_hash: str, password: str) -> bool:
    decoded = _decode_password_hash(password_hash)
    if decoded is None:
        return False
    salt, expected_key = decoded
    actual_key = hashlib.pbkdf2_hmac(
        "sha256", _password_bytes(password), salt, _PASSWORD_ITERATIONS, dklen=_PASSWORD_KEY_BYTES
    )
    return secrets.compare_digest(actual_key, expected_key)


_STATE = AMLState()


def ensure_initialized(db_url: str, *, force_reset: bool = False) -> None:
    global _STATE
    normalized_db_url = _normalize_db_url(db_url)
    init_db(normalized_db_url)
    if force_reset or _STATE.db_url != normalized_db_url:
        _STATE = AMLState(db_url=normalized_db_url)
    _seed_default_users()
    _migrate_plaintext_passwords()
    purge_expired_sessions()


def get_aml_system_config() -> dict[str, Any]:
    return _STATE.aml_system_config


def set_aml_system_config(v: dict[str, Any]) -> None:
    _STATE.aml_system_config = v


def get_aml_network_config() -> dict[str, Any]:
    return _STATE.aml_network_config


def set_aml_network_config(v: dict[str, Any]) -> None:
    _STATE.aml_network_config = v


def get_aml_snmp_config() -> dict[str, Any]:
    return _STATE.aml_snmp_config


def set_aml_snmp_config(v: dict[str, Any]) -> None:
    _STATE.aml_snmp_config = v


def get_aml_email_config() -> dict[str, Any]:
    return _STATE.aml_email_config


def set_aml_email_config(v: dict[str, Any]) -> None:
    _STATE.aml_email_config = v


def get_aml_syslog_config() -> dict[str, Any]:
    return _STATE.aml_syslog_config


def set_aml_syslog_config(v: dict[str, Any]) -> None:
    _STATE.aml_syslog_config = v


def get_aml_services() -> dict[str, dict[str, Any]]:
    return _STATE.aml_services


def set_aml_services(v: dict[str, dict[str, Any]]) -> None:
    _STATE.aml_services = v


def get_aml_audit_log() -> list[dict[str, Any]]:
    return _STATE.aml_audit_log


def set_aml_audit_log(v: list[dict[str, Any]]) -> None:
    _STATE.aml_audit_log = v


def get_aml_ha_config() -> dict[str, Any]:
    return _STATE.aml_ha_config


def set_aml_ha_config(v: dict[str, Any]) -> None:
    _STATE.aml_ha_config = v


def get_aml_callhome_config() -> dict[str, Any]:
    return _STATE.aml_callhome_config


def set_aml_callhome_config(v: dict[str, Any]) -> None:
    _STATE.aml_callhome_config = v


def get_aml_system_security() -> dict[str, Any]:
    return _STATE.aml_system_security


def set_aml_system_security(v: dict[str, Any]) -> None:
    _STATE.aml_system_security = v


def get_aml_debug_config() -> dict[str, Any]:
    return _STATE.aml_debug_config


def set_aml_debug_config(v: dict[str, Any]) -> None:
    _STATE.aml_debug_config = v


def get_aml_system_preferences() -> dict[str, Any]:
    return _STATE.aml_system_preferences


def set_aml_system_preferences(v: dict[str, Any]) -> None:
    _STATE.aml_system_preferences = v


def get_aml_remote_config() -> dict[str, Any]:
    return _STATE.aml_remote_config


def set_aml_remote_config(v: dict[str, Any]) -> None:
    _STATE.aml_remote_config = v


def get_aml_proxy_config() -> dict[str, Any]:
    return _STATE.aml_proxy_config


def set_aml_proxy_config(v: dict[str, Any]) -> None:
    _STATE.aml_proxy_config = v


def get_aml_system_started_at() -> float:
    return _STATE.aml_system_started_at


def set_aml_system_started_at(v: float) -> None:
    _STATE.aml_system_started_at = v


def _seed_default_users() -> None:
    with get_session() as session:
        admin = session.get(AmlUser, "admin")
        if admin is None:
            session.add(
                AmlUser(
                    name="admin",
                    password=hash_password(_get_admin_password()),
                    role=0,
                    require_password_change=True,
                )
            )
        service = session.get(AmlUser, "service")
        if service is None:
            session.add(
                AmlUser(
                    name="service",
                    password=hash_password(_get_service_password()),
                    role=2,
                    require_password_change=False,
                )
            )
        session.commit()


def _migrate_plaintext_passwords() -> None:
    with get_session() as session:
        changed = False
        for user in session.execute(select(AmlUser)).scalars():
            if not is_password_hash(user.password):
                user.password = hash_password(user.password)
                changed = True
        if changed:
            session.commit()


def list_users() -> list[AmlUser]:
    with get_session() as session:
        return list(session.execute(select(AmlUser).order_by(AmlUser.name)).scalars())


def get_user(name: str) -> AmlUser | None:
    with get_session() as session:
        return session.get(AmlUser, name.lower())


def create_user(name: str, password: str, role: int) -> AmlUser:
    with get_session() as session:
        user = AmlUser(
            name=name.lower(),
            password=hash_password(password),
            role=role,
            require_password_change=False,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        return user


def create_ldap_user(name: str, role: int = 1) -> AmlUser:
    with get_session() as session:
        user = session.get(AmlUser, name.lower())
        if user is None:
            user = AmlUser(
                name=name.lower(),
                password=hash_password(secrets.token_urlsafe(24)),
                role=role,
                require_password_change=False,
            )
            session.add(user)
            session.commit()
            session.refresh(user)
        return user


def update_user(name: str, *, password: str | None = None, role: int | None = None) -> AmlUser | None:
    with get_session() as session:
        user = session.get(AmlUser, name.lower())
        if user is None:
            return None
        if password is not None:
            user.password = hash_password(password)
            user.require_password_change = False
        if role is not None:
            user.role = role
        session.commit()
        session.refresh(user)
        return user


def delete_user(name: str) -> bool:
    normalized_name = name.lower()
    with get_session() as session:
        user = session.get(AmlUser, normalized_name)
        if user is None:
            return False
        session.delete(user)
        session.commit()
    clear_user_sessions(normalized_name)
    return True


def reset_admin_password() -> AmlUser:
    with get_session() as session:
        admin = session.get(AmlUser, "admin")
        if admin is None:
            admin = AmlUser(
                name="admin",
                password=hash_password(_get_admin_password()),
                role=0,
                require_password_change=True,
            )
            session.add(admin)
        else:
            admin.password = hash_password(_get_admin_password())
            admin.role = 0
            admin.require_password_change = True
        session.commit()
        session.refresh(admin)
    clear_user_sessions("admin")
    return admin


def verify_credentials(name: str, password: str) -> AmlUser | None:
    user = get_user(name)
    if user is None or not verify_password(user.password, password):
        return None
    return user


def is_ldap_user(name: str) -> bool:
    return name.lower() in _STATE.ldap_users


def authenticate_ldap_user(name: str, password: str) -> AmlUser | None:
    normalized_name = name.lower()
    expected_password = _STATE.ldap_users.get(normalized_name)
    if expected_password is None or not secrets.compare_digest(expected_password, password):
        return None
    return get_user(normalized_name) or create_ldap_user(normalized_name)


def purge_expired_sessions() -> None:
    now = _utcnow()
    expired = [token for token, record in _STATE.sessions.items() if record.expires_at <= now]
    for token in expired:
        _STATE.sessions.pop(token, None)


def create_session(user: AmlUser) -> SessionRecord:
    purge_expired_sessions()
    now = _utcnow()
    record = SessionRecord(
        token=str(uuid4()),
        user_name=user.name,
        role=user.role,
        created_at=now,
        expires_at=now + timedelta(minutes=_STATE.session_timeout_minutes),
    )
    _STATE.sessions[record.token] = record
    return record


def get_session_user(token: str) -> AmlUser | None:
    purge_expired_sessions()
    record = _STATE.sessions.get(token)
    if record is None:
        return None
    record.expires_at = _utcnow() + timedelta(minutes=_STATE.session_timeout_minutes)
    return get_user(record.user_name)


def clear_session(token: str) -> None:
    _STATE.sessions.pop(token, None)


def clear_user_sessions(user_name: str) -> None:
    targets = [token for token, record in _STATE.sessions.items() if record.user_name == user_name]
    for token in targets:
        _STATE.sessions.pop(token, None)


def clear_all_sessions() -> None:
    _STATE.sessions.clear()


def list_sessions(*, user_name: str | None = None) -> list[dict[str, Any]]:
    purge_expired_sessions()
    records = sorted(_STATE.sessions.values(), key=lambda item: item.created_at)
    if user_name is not None:
        records = [record for record in records if record.user_name == user_name]
    return [record.as_dict() for record in records]


def get_ldap_config() -> dict[str, Any]:
    return dict(_STATE.ldap_config)


def set_ldap_config(config: dict[str, Any]) -> dict[str, Any]:
    existing_keytab = _STATE.ldap_config.get("keytabFile", {"name": None, "date": None})
    updated = _default_ldap_config()
    updated.update(config)
    keytab = updated.get("keytabFile")
    if keytab is None or (keytab.get("name") is None and keytab.get("date") is None):
        updated["keytabFile"] = existing_keytab
    _STATE.ldap_config = updated
    return get_ldap_config()


def list_ldap_certificates() -> list[dict[str, str | None]]:
    return [certificate.as_dict() for certificate in _STATE.ldap_certificates]


def add_ldap_certificate(filename: str) -> None:
    _STATE.ldap_certificates.append(StoredAsset(name=filename, date=_isoformat(_utcnow())))


def set_ldap_keytab(filename: str) -> dict[str, str | None]:
    record = StoredAsset(name=filename, date=_isoformat(_utcnow()))
    _STATE.ldap_config["keytabFile"] = record.as_dict()
    return record.as_dict()


def get_login_mode() -> int:
    return _STATE.login_mode


def set_login_mode(mode: int) -> int:
    _STATE.login_mode = mode
    return _STATE.login_mode


def get_lui_access() -> dict[str, Any]:
    return dict(_STATE.lui_access)


def set_lui_access(access: dict[str, Any]) -> dict[str, Any]:
    _STATE.lui_access = {"mode": access.get("mode"), "pin": access.get("pin")}
    return get_lui_access()


def _default_user_mfa() -> dict[str, MFARecord]:
    return {"totp": MFARecord(type="totp", enabled=False, secret=_generate_totp_secret())}


def list_user_mfa(user_name: str) -> list[dict[str, Any]]:
    configs = _STATE.mfa_configs.setdefault(user_name, _default_user_mfa())
    return [config.as_dict() for config in configs.values()]


def set_user_mfa(user_name: str, mfa_type: str, enabled: bool) -> dict[str, Any]:
    configs = _STATE.mfa_configs.setdefault(user_name, _default_user_mfa())
    existing = configs.get(mfa_type)
    if existing is None:
        existing = MFARecord(type=mfa_type, enabled=False, secret=_generate_totp_secret())
        configs[mfa_type] = existing
    if not enabled:
        existing.secret = _generate_totp_secret()
    existing.enabled = enabled
    return existing.as_dict()


def get_user_mfa(user_name: str, mfa_type: str) -> MFARecord | None:
    return _STATE.mfa_configs.setdefault(user_name, _default_user_mfa()).get(mfa_type)


def record_login_activity(user_name: str, *, success: bool, remote_address: str | None) -> None:
    _STATE.login_activity.append(
        {
            "user": user_name,
            "timestamp": _isoformat(_utcnow()),
            "success": success,
            "remoteAddress": remote_address,
        }
    )
    if len(_STATE.login_activity) > 500:
        del _STATE.login_activity[:-500]


def get_login_activity() -> list[dict[str, Any]]:
    return list(_STATE.login_activity)


def get_service_access() -> dict[str, Any]:
    return dict(_STATE.service_access)


def set_service_access(enabled: bool, authentication_code_expiry: int | None = None) -> dict[str, Any]:
    _STATE.service_access["enabled"] = enabled
    if authentication_code_expiry is not None:
        _STATE.service_access["authenticationCodeExpiry"] = authentication_code_expiry
    if enabled:
        _STATE.service_access_code = _generate_numeric_code(8)
        _STATE.service_access_generated_at = _utcnow()
    return get_service_access()


def get_service_authentication_code() -> str:
    expiry = int(_STATE.service_access.get("authenticationCodeExpiry", 300))
    if (_utcnow() - _STATE.service_access_generated_at).total_seconds() >= expiry:
        _STATE.service_access_code = _generate_numeric_code(8)
        _STATE.service_access_generated_at = _utcnow()
    return _STATE.service_access_code


def get_session_timeout() -> int:
    return _STATE.session_timeout_minutes


def set_session_timeout(timeout_minutes: int) -> int:
    _STATE.session_timeout_minutes = timeout_minutes
    purge_expired_sessions()
    return _STATE.session_timeout_minutes


def get_password_policy() -> dict[str, Any]:
    return dict(_STATE.password_policy)


def get_library_name() -> str:
    return _STATE.library_name


def set_library_name(name: str) -> str:
    _STATE.library_name = name
    return _STATE.library_name


def get_library_mode() -> str:
    return _STATE.library_mode


def set_library_mode(mode: str) -> str:
    _STATE.library_mode = mode
    return _STATE.library_mode


def set_password_policy(policy: dict[str, Any]) -> dict[str, Any]:
    _STATE.password_policy = dict(policy)
    return get_password_policy()


def get_eth_blades() -> dict[str, dict[str, Any]]:
    return deepcopy(_STATE.eth_blades)


def get_eth_blade(blade_id: str) -> dict[str, Any] | None:
    blade = _STATE.eth_blades.get(blade_id)
    return None if blade is None else deepcopy(blade)


def update_eth_blade(blade_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    blade = _STATE.eth_blades.get(blade_id)
    if blade is None:
        return None
    ports = updates.pop("ports", None)
    blade.update(deepcopy(updates))
    if ports is not None:
        blade["ports"] = [deepcopy(port) for port in ports]
    blade["portCount"] = len(blade.get("ports", []))
    return deepcopy(blade)


def update_eth_port(blade_id: str, port_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    blade = _STATE.eth_blades.get(blade_id)
    if blade is None:
        return None
    for port in blade.get("ports", []):
        if port.get("id") == port_id:
            port.update(deepcopy(updates))
            return deepcopy(port)
    return None


def reset_eth_blade(blade_id: str) -> dict[str, Any] | None:
    blade = _STATE.eth_blades.get(blade_id)
    if blade is None:
        return None
    blade["status"] = "online"
    for port in blade.get("ports", []):
        port["status"] = "up"
    return deepcopy(blade)


def get_fc_blades() -> dict[str, dict[str, Any]]:
    return deepcopy(_STATE.fc_blades)


def get_fc_blade(blade_id: str) -> dict[str, Any] | None:
    blade = _STATE.fc_blades.get(blade_id)
    return None if blade is None else deepcopy(blade)


def update_fc_blade(blade_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    blade = _STATE.fc_blades.get(blade_id)
    if blade is None:
        return None
    ports = updates.pop("ports", None)
    blade.update(deepcopy(updates))
    if ports is not None:
        blade["ports"] = [deepcopy(port) for port in ports]
    blade["portCount"] = len(blade.get("ports", []))
    return deepcopy(blade)


def update_fc_port(blade_id: str, port_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    blade = _STATE.fc_blades.get(blade_id)
    if blade is None:
        return None
    for port in blade.get("ports", []):
        if port.get("id") == port_id:
            port.update(deepcopy(updates))
            return deepcopy(port)
    return None


def reset_fc_blade(blade_id: str) -> dict[str, Any] | None:
    blade = _STATE.fc_blades.get(blade_id)
    if blade is None:
        return None
    blade["status"] = "online"
    for port in blade.get("ports", []):
        port["status"] = "online"
    return deepcopy(blade)


def get_mgmt_blades() -> dict[str, dict[str, Any]]:
    return deepcopy(_STATE.mgmt_blades)


def get_mgmt_blade(blade_id: str) -> dict[str, Any] | None:
    blade = _STATE.mgmt_blades.get(blade_id)
    return None if blade is None else deepcopy(blade)


def update_mgmt_blade(blade_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    blade = _STATE.mgmt_blades.get(blade_id)
    if blade is None:
        return None
    blade.update(deepcopy(updates))
    return deepcopy(blade)


def failover_mgmt_blade(blade_id: str) -> dict[str, Any] | None:
    blade = _STATE.mgmt_blades.get(blade_id)
    if blade is None:
        return None
    for current_id, current in _STATE.mgmt_blades.items():
        if current_id == blade_id:
            current["status"] = "active"
            current["role"] = "primary"
        else:
            current["status"] = "standby"
            current["role"] = "secondary"
    return deepcopy(_STATE.mgmt_blades[blade_id])


def get_drive_sleds() -> dict[str, dict[str, Any]]:
    return deepcopy(_STATE.drive_sleds)


def get_drive_sled(sled_id: str) -> dict[str, Any] | None:
    sled = _STATE.drive_sleds.get(sled_id)
    return None if sled is None else deepcopy(sled)


def update_drive_sled(sled_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    sled = _STATE.drive_sleds.get(sled_id)
    if sled is None:
        return None
    sled.update(deepcopy(updates))
    return deepcopy(sled)


def get_power_supplies() -> dict[str, dict[str, Any]]:
    return deepcopy(_STATE.power_supplies)


def get_power_supply(ps_id: str) -> dict[str, Any] | None:
    power_supply = _STATE.power_supplies.get(ps_id)
    return None if power_supply is None else deepcopy(power_supply)


def get_aml_fans() -> dict[str, dict[str, Any]]:
    return deepcopy(_STATE.aml_fans)


def get_aml_fan(fan_id: str) -> dict[str, Any] | None:
    fan = _STATE.aml_fans.get(fan_id)
    return None if fan is None else deepcopy(fan)


def _normalize_ie_station_slots(slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, slot in enumerate(slots, start=1):
        current = deepcopy(slot)
        current.setdefault("id", f"IE-1-S{index}")
        current.setdefault("address", f"0,0,{index}")
        current.setdefault("state", "empty")
        current.setdefault("barcode", None)
        current.setdefault("type", "ie")
        normalized.append(current)
    return normalized


def get_aml_robots() -> dict[str, dict[str, Any]]:
    return deepcopy(_STATE.aml_robots)


def get_aml_robot(robot_id: str) -> dict[str, Any] | None:
    robot = _STATE.aml_robots.get(robot_id)
    return None if robot is None else deepcopy(robot)


def update_aml_robot(robot_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    robot = _STATE.aml_robots.get(robot_id)
    if robot is None:
        return None
    robot.update(deepcopy(updates))
    return deepcopy(robot)


def move_aml_robot_home(robot_id: str) -> dict[str, Any] | None:
    robot = _STATE.aml_robots.get(robot_id)
    if robot is None:
        return None
    robot["state"] = "idle"
    robot["location"] = robot.get("homeSlot", "base")
    return deepcopy(robot)


def calibrate_aml_robot(robot_id: str) -> dict[str, Any] | None:
    robot = _STATE.aml_robots.get(robot_id)
    if robot is None:
        return None
    robot["state"] = "calibrated"
    robot["status"] = "online"
    return deepcopy(robot)


def get_aml_towers() -> dict[str, dict[str, Any]]:
    return deepcopy(_STATE.aml_towers)


def get_aml_tower(tower_id: str) -> dict[str, Any] | None:
    tower = _STATE.aml_towers.get(tower_id)
    return None if tower is None else deepcopy(tower)


def update_aml_tower(tower_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    tower = _STATE.aml_towers.get(tower_id)
    if tower is None:
        return None
    tower.update(deepcopy(updates))
    return deepcopy(tower)


def get_aml_ie_stations() -> dict[str, dict[str, Any]]:
    return deepcopy(_STATE.aml_ie_stations)


def get_aml_ie_station(station_id: str) -> dict[str, Any] | None:
    station = _STATE.aml_ie_stations.get(station_id)
    return None if station is None else deepcopy(station)


def update_aml_ie_station(station_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    station = _STATE.aml_ie_stations.get(station_id)
    if station is None:
        return None
    slots = updates.pop("slots", None)
    station.update(deepcopy(updates))
    if slots is not None:
        station["slots"] = _normalize_ie_station_slots(slots)
        station["slotCount"] = len(station["slots"])
    return deepcopy(station)


def open_aml_ie_station(station_id: str) -> dict[str, Any] | None:
    station = _STATE.aml_ie_stations.get(station_id)
    if station is None:
        return None
    station["state"] = "open"
    return deepcopy(station)


def close_aml_ie_station(station_id: str) -> dict[str, Any] | None:
    station = _STATE.aml_ie_stations.get(station_id)
    if station is None:
        return None
    station["state"] = "closed"
    return deepcopy(station)


def get_aml_magazines() -> dict[str, dict[str, Any]]:
    return deepcopy(_STATE.aml_magazines)


def get_aml_magazine(magazine_id: str) -> dict[str, Any] | None:
    magazine = _STATE.aml_magazines.get(magazine_id)
    return None if magazine is None else deepcopy(magazine)


def update_aml_magazine(magazine_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    magazine = _STATE.aml_magazines.get(magazine_id)
    if magazine is None:
        return None
    tapes = updates.pop("tapes", None)
    magazine.update(deepcopy(updates))
    if tapes is not None:
        magazine["tapes"] = list(tapes)
    magazine["occupiedSlots"] = len(magazine.get("tapes", []))
    return deepcopy(magazine)


def eject_aml_magazine(magazine_id: str) -> dict[str, Any] | None:
    magazine = _STATE.aml_magazines.get(magazine_id)
    if magazine is None:
        return None
    magazine["status"] = "ejected"
    return deepcopy(magazine)


def insert_aml_magazine(magazine_id: str) -> dict[str, Any] | None:
    magazine = _STATE.aml_magazines.get(magazine_id)
    if magazine is None:
        return None
    magazine["status"] = "online"
    return deepcopy(magazine)


def run_physical_audit() -> dict[str, int]:
    return {
        "robots": len(_STATE.aml_robots),
        "towers": len(_STATE.aml_towers),
        "ieStations": len(_STATE.aml_ie_stations),
        "magazines": len(_STATE.aml_magazines),
    }


def refresh_devices() -> dict[str, int]:
    return {
        "ethBlades": len(_STATE.eth_blades),
        "fcBlades": len(_STATE.fc_blades),
        "mgmtBlades": len(_STATE.mgmt_blades),
        "driveSleds": len(_STATE.drive_sleds),
        "powerSupplies": len(_STATE.power_supplies),
        "fans": len(_STATE.aml_fans),
    }


def list_access_groups() -> list[dict[str, Any]]:
    return [
        {
            "name": item["name"],
            "devices": list(item.get("devices", [])),
            "hosts": list(item.get("hosts", [])),
        }
        for item in sorted(_STATE.access_groups.values(), key=lambda value: value["name"])
    ]


def get_access_group(name: str) -> dict[str, Any] | None:
    group = _STATE.access_groups.get(name)
    if group is None:
        return None
    return {
        "name": group["name"],
        "devices": list(group.get("devices", [])),
        "hosts": list(group.get("hosts", [])),
    }


def create_access_group(name: str) -> dict[str, Any] | None:
    if name in _STATE.access_groups:
        return None
    _STATE.access_groups[name] = {"name": name, "devices": [], "hosts": []}
    return get_access_group(name)


def delete_access_group(name: str) -> bool:
    group = _STATE.access_groups.pop(name, None)
    if group is None:
        return False
    for wwpn in group.get("hosts", []):
        host = _STATE.aml_hosts.get(wwpn)
        if host is not None:
            host["groups"] = [group_name for group_name in host.get("groups", []) if group_name != name]
    return True


def set_access_group_devices(name: str, devices: list[str]) -> dict[str, Any] | None:
    group = _STATE.access_groups.get(name)
    if group is None:
        return None
    group["devices"] = list(dict.fromkeys(devices))
    return get_access_group(name)


def add_access_group_devices(name: str, devices: list[str]) -> dict[str, Any] | None:
    group = _STATE.access_groups.get(name)
    if group is None:
        return None
    merged = list(group.get("devices", []))
    for serial_number in devices:
        if serial_number not in merged:
            merged.append(serial_number)
    group["devices"] = merged
    return get_access_group(name)


def remove_access_group_device(name: str, serial_number: str) -> bool:
    group = _STATE.access_groups.get(name)
    if group is None:
        return False
    devices = group.get("devices", [])
    if serial_number not in devices:
        return False
    group["devices"] = [device for device in devices if device != serial_number]
    return True


def list_aml_hosts() -> list[dict[str, Any]]:
    return [
        {
            "WWPN": item["WWPN"],
            "alias": item.get("alias"),
            "groups": list(item.get("groups", [])),
        }
        for item in sorted(_STATE.aml_hosts.values(), key=lambda value: value["WWPN"])
    ]


def get_aml_host(wwpn: str) -> dict[str, Any] | None:
    host = _STATE.aml_hosts.get(wwpn)
    if host is None:
        return None
    return {
        "WWPN": host["WWPN"],
        "alias": host.get("alias"),
        "groups": list(host.get("groups", [])),
    }


def create_aml_host(wwpn: str, alias: str | None = None) -> dict[str, Any] | None:
    if wwpn in _STATE.aml_hosts:
        return None
    _STATE.aml_hosts[wwpn] = {"WWPN": wwpn, "alias": alias, "groups": []}
    return get_aml_host(wwpn)


def update_aml_host(wwpn: str, *, alias: str | None = None) -> dict[str, Any] | None:
    host = _STATE.aml_hosts.get(wwpn)
    if host is None:
        return None
    host["alias"] = alias
    return get_aml_host(wwpn)


def ensure_aml_host(wwpn: str, alias: str | None = None) -> dict[str, Any]:
    existing = _STATE.aml_hosts.get(wwpn)
    if existing is None:
        _STATE.aml_hosts[wwpn] = {"WWPN": wwpn, "alias": alias, "groups": []}
    elif alias is not None:
        existing["alias"] = alias
    return get_aml_host(wwpn) or {"WWPN": wwpn, "alias": alias, "groups": []}


def list_iblade_messages() -> list[dict[str, Any]]:
    return [deepcopy(item) for item in _STATE.iblade_messages]


def get_iblade_message(message_id: str) -> dict[str, Any] | None:
    for item in _STATE.iblade_messages:
        if str(item.get("id")) == str(message_id):
            return deepcopy(item)
    return None


def update_iblade_message(message_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    for index, item in enumerate(_STATE.iblade_messages):
        if str(item.get("id")) != str(message_id):
            continue
        _STATE.iblade_messages[index] = {**item, **deepcopy(updates)}
        return deepcopy(_STATE.iblade_messages[index])
    return None


def list_iblade_hosts() -> list[dict[str, Any]]:
    return [deepcopy(item) for _, item in sorted(_STATE.iblade_hosts.items())]


def get_iblade_host(host_id: str) -> dict[str, Any] | None:
    host = _STATE.iblade_hosts.get(host_id)
    return deepcopy(host) if host is not None else None


def upsert_iblade_host(host: dict[str, Any]) -> dict[str, Any]:
    current = deepcopy(host)
    host_id = str(current.get("id") or f"HOST-{len(_STATE.iblade_hosts) + 1:03d}")
    current["id"] = host_id
    _STATE.iblade_hosts[host_id] = current
    return deepcopy(current)


def get_iblade_network_config() -> dict[str, Any]:
    return deepcopy(_STATE.iblade_network_config)


def set_iblade_network_config(updates: dict[str, Any]) -> dict[str, Any]:
    for key, value in deepcopy(updates).items():
        if value is not None:
            _STATE.iblade_network_config[key] = value
    return get_iblade_network_config()


def get_iblade_system_settings() -> dict[str, Any]:
    return deepcopy(_STATE.iblade_system_settings)


def set_iblade_system_settings(updates: dict[str, Any]) -> dict[str, Any]:
    for key, value in deepcopy(updates).items():
        if value is not None:
            _STATE.iblade_system_settings[key] = value
    return get_iblade_system_settings()


def list_iblade_volume_groups() -> list[dict[str, Any]]:
    return [deepcopy(item) for _, item in sorted(_STATE.iblade_volume_groups.items())]


def get_iblade_volume_group(index: int | str) -> dict[str, Any] | None:
    key = int(index)
    group = _STATE.iblade_volume_groups.get(key)
    return deepcopy(group) if group is not None else None


def update_iblade_volume_group(index: int | str, updates: dict[str, Any]) -> dict[str, Any] | None:
    key = int(index)
    group = _STATE.iblade_volume_groups.get(key)
    if group is None:
        return None
    for field, value in deepcopy(updates).items():
        if field == "index" or value is None:
            continue
        group[field] = value
    group["mediaCount"] = len(group.get("tapes", []))
    return deepcopy(group)


def replace_iblade_volume_groups(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    _STATE.iblade_volume_groups = {}
    for offset, item in enumerate(groups, start=1):
        current = deepcopy(item)
        current["index"] = offset
        current["mediaCount"] = len(current.get("tapes", []))
        _STATE.iblade_volume_groups[offset] = current
    return list_iblade_volume_groups()


def delete_aml_host(wwpn: str) -> bool:
    host = _STATE.aml_hosts.pop(wwpn, None)
    if host is None:
        return False
    for group_name in host.get("groups", []):
        group = _STATE.access_groups.get(group_name)
        if group is not None:
            group["hosts"] = [host_wwpn for host_wwpn in group.get("hosts", []) if host_wwpn != wwpn]
    return True


def add_host_to_access_group(name: str, wwpn: str, alias: str | None = None) -> bool:
    group = _STATE.access_groups.get(name)
    if group is None:
        return False
    host = _STATE.aml_hosts.get(wwpn)
    if host is None:
        host = {"WWPN": wwpn, "alias": alias, "groups": []}
        _STATE.aml_hosts[wwpn] = host
    elif alias is not None:
        host["alias"] = alias
    if wwpn not in group.get("hosts", []):
        group.setdefault("hosts", []).append(wwpn)
    if name not in host.get("groups", []):
        host.setdefault("groups", []).append(name)
    return True


def remove_host_from_access_group(name: str, wwpn: str) -> bool:
    group = _STATE.access_groups.get(name)
    if group is None:
        return False
    if wwpn not in group.get("hosts", []):
        return False
    group["hosts"] = [host_wwpn for host_wwpn in group.get("hosts", []) if host_wwpn != wwpn]
    host = _STATE.aml_hosts.get(wwpn)
    if host is not None:
        host["groups"] = [group_name for group_name in host.get("groups", []) if group_name != name]
    return True


def list_aml_licenses() -> list[dict[str, Any]]:
    return [
        dict(item)
        for item in sorted(_STATE.aml_licenses.values(), key=lambda value: value["serialNumber"])
    ]


def get_aml_license(serial_number: str) -> dict[str, Any] | None:
    license_item = _STATE.aml_licenses.get(serial_number)
    return None if license_item is None else dict(license_item)


def set_aml_license(license_item: dict[str, Any]) -> dict[str, Any]:
    serial_number = str(license_item["serialNumber"])
    stored = {
        "serialNumber": serial_number,
        "type": license_item.get("type"),
        "description": license_item.get("description"),
        "status": license_item.get("status"),
        "feature": license_item.get("feature"),
        "expiry": license_item.get("expiry"),
    }
    _STATE.aml_licenses[serial_number] = stored
    return dict(stored)


def set_aml_licenses(licenses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [set_aml_license(license_item) for license_item in licenses]


def activate_aml_license(serial_number: str) -> bool:
    license_item = _STATE.aml_licenses.get(serial_number)
    if license_item is None:
        return False
    license_item["status"] = "active"
    return True


def delete_aml_license(serial_number: str) -> bool:
    if serial_number not in _STATE.aml_licenses:
        return False
    del _STATE.aml_licenses[serial_number]
    return True



def _unique_partition_values(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))



def _sync_aml_partition_counts() -> None:
    _STATE.aml_partitions_global["currentPartitions"] = len(_STATE.aml_partitions)



def list_aml_partitions() -> list[dict[str, Any]]:
    _sync_aml_partition_counts()
    return [deepcopy(item) for _, item in sorted(_STATE.aml_partitions.items())]



def get_aml_partition(name: str) -> dict[str, Any] | None:
    partition = _STATE.aml_partitions.get(name)
    return deepcopy(partition) if partition is not None else None



def create_aml_partition(name: str, partition: dict[str, Any]) -> dict[str, Any] | None:
    if name in _STATE.aml_partitions:
        return None
    next_id = f"PART-{len(_STATE.aml_partitions) + 1:03d}"
    drive_count = int(partition.get("driveCount", 0))
    slot_count = int(partition.get("slotCount", 0))
    stored = {
        "name": name,
        "id": partition.get("id", next_id),
        "status": partition.get("status", "online"),
        "type": partition.get("type", "data"),
        "driveCount": drive_count,
        "slotCount": slot_count,
        "ieSlotCount": int(partition.get("ieSlotCount", 0)),
        "cleaningSlots": int(partition.get("cleaningSlots", 0)),
        "mediaCount": int(partition.get("mediaCount", 0)),
        "drives": list(partition.get("drives", [])),
        "policy": deepcopy(
            partition.get("policy")
            or {
                "autoClean": True,
                "cleaningThreshold": _STATE.aml_partitions_global.get("defaultCleaningThreshold", 100),
                "mediaAutoAssign": True,
                "mountTimeout": _STATE.aml_partitions_global.get("defaultMountTimeout", 300),
                "unmountTimeout": 60,
                "ejectTimeout": 30,
                "roboticsTimeout": 120,
            }
        ),
        "access": deepcopy(partition.get("access") or {"mode": "readWrite", "groups": [], "hosts": []}),
        "cleaning": deepcopy(
            partition.get("cleaning")
            or {
                "autoClean": True,
                "threshold": _STATE.aml_partitions_global.get("defaultCleaningThreshold", 100),
                "cleaningTapeBarcode": None,
                "lastCleaned": None,
            }
        ),
        "worm": deepcopy(partition.get("worm") or {"enabled": False, "mode": "none"}),
        "encryption": deepcopy(
            partition.get("encryption") or {"enabled": False, "type": "none", "keyManager": None}
        ),
        "qos": deepcopy(
            partition.get("qos") or {"maxMountsPerHour": 60, "priority": "normal", "preemption": False}
        ),
        "lme": deepcopy(partition.get("lme") or {"enabled": False, "exportPath": None}),
        "alerts": deepcopy(partition.get("alerts") or []),
        "moveQueue": deepcopy(partition.get("moveQueue") or []),
        "quota": deepcopy(
            partition.get("quota")
            or {
                "totalSlots": slot_count,
                "usedSlots": int(partition.get("mediaCount", 0)),
                "totalDrives": drive_count,
                "usedDrives": len(partition.get("drives", [])),
            }
        ),
        "statistics": deepcopy(
            partition.get("statistics")
            or {
                "mountCount": 0,
                "unmountCount": 0,
                "errorCount": 0,
                "lastMount": None,
                "lastUnmount": None,
                "mediaUsage": [],
            }
        ),
    }
    _STATE.aml_partitions[name] = stored
    _sync_aml_partition_counts()
    return get_aml_partition(name)



def update_aml_partition(name: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    partition = _STATE.aml_partitions.get(name)
    if partition is None:
        return None
    for key, value in updates.items():
        if value is not None:
            partition[key] = deepcopy(value)
    quota = partition.setdefault("quota", {})
    quota.setdefault("totalSlots", int(partition.get("slotCount", 0)))
    quota.setdefault("totalDrives", int(partition.get("driveCount", 0)))
    if "slotCount" in updates and updates["slotCount"] is not None:
        quota["totalSlots"] = int(updates["slotCount"])
    if "driveCount" in updates and updates["driveCount"] is not None:
        quota["totalDrives"] = int(updates["driveCount"])
    return get_aml_partition(name)



def delete_aml_partition(name: str) -> bool:
    if name not in _STATE.aml_partitions:
        return False
    del _STATE.aml_partitions[name]
    _sync_aml_partition_counts()
    return True



def get_aml_partitions_global() -> dict[str, Any]:
    _sync_aml_partition_counts()
    return deepcopy(_STATE.aml_partitions_global)



def set_aml_partitions_global(values: dict[str, Any]) -> dict[str, Any]:
    for key, value in values.items():
        if key == "currentPartitions":
            continue
        if value is not None:
            _STATE.aml_partitions_global[key] = value
    _sync_aml_partition_counts()
    return get_aml_partitions_global()



def set_aml_partition_section(name: str, section: str, value: Any) -> Any | None:
    partition = _STATE.aml_partitions.get(name)
    if partition is None:
        return None
    partition[section] = deepcopy(value)
    return deepcopy(partition[section])



def add_aml_partition_list_item(name: str, field: str, value: str) -> list[str] | None:
    partition = _STATE.aml_partitions.get(name)
    if partition is None:
        return None
    items = list(partition.get(field, []))
    if value not in items:
        items.append(value)
    partition[field] = _unique_partition_values(items)
    return list(partition[field])



def remove_aml_partition_list_item(name: str, field: str, value: str) -> bool:
    partition = _STATE.aml_partitions.get(name)
    if partition is None:
        return False
    items = list(partition.get(field, []))
    if value not in items:
        return False
    partition[field] = [item for item in items if item != value]
    return True



def add_aml_partition_access_value(name: str, field: str, value: str) -> list[str] | None:
    partition = _STATE.aml_partitions.get(name)
    if partition is None:
        return None
    access = partition.setdefault("access", {"mode": "readWrite", "groups": [], "hosts": []})
    values = list(access.get(field, []))
    if value not in values:
        values.append(value)
    access[field] = _unique_partition_values(values)
    return list(access[field])



def remove_aml_partition_access_value(name: str, field: str, value: str) -> bool:
    partition = _STATE.aml_partitions.get(name)
    if partition is None:
        return False
    access = partition.setdefault("access", {"mode": "readWrite", "groups": [], "hosts": []})
    values = list(access.get(field, []))
    if value not in values:
        return False
    access[field] = [item for item in values if item != value]
    return True



def _sync_aml_media_counts() -> None:
    partition_counts: dict[str, int] = {}
    for media in _STATE.aml_media.values():
        partition = str(media.get("partition", "partition1"))
        partition_counts[partition] = partition_counts.get(partition, 0) + 1
    for name, partition in _STATE.aml_partitions.items():
        media_count = partition_counts.get(name, 0)
        partition["mediaCount"] = media_count
        quota = partition.setdefault("quota", {})
        quota["usedSlots"] = media_count
        quota.setdefault("totalSlots", int(partition.get("slotCount", 0)))
        quota.setdefault("totalDrives", int(partition.get("driveCount", 0)))
        quota.setdefault("usedDrives", len(partition.get("drives", [])))

    valid_barcodes = set(_STATE.aml_media)
    assigned_barcodes: set[str] = set()
    for pool_id, pool in list(_STATE.aml_media_pools.items()):
        normalized_id = _normalize_aml_media_pool_id(str(pool.get("id", pool_id)))
        raw_barcodes = pool.get("assignedBarcodes", pool.get("barcodes"))
        normalized: list[str] = []
        if isinstance(raw_barcodes, list):
            for barcode in raw_barcodes:
                if not isinstance(barcode, str):
                    continue
                normalized_barcode = barcode.strip()
                if not normalized_barcode or normalized_barcode not in valid_barcodes or normalized_barcode in assigned_barcodes:
                    continue
                normalized.append(normalized_barcode)
                assigned_barcodes.add(normalized_barcode)

        policy = str(pool.get("policy", "standard")).strip() or "standard"
        max_drives = pool.get("maxDrives", 1 if policy == "critical" else 4)
        quota = pool.get("quotaGB")
        try:
            normalized_max_drives = max(1, int(max_drives))
        except (TypeError, ValueError):
            normalized_max_drives = 1 if policy == "critical" else 4
        try:
            normalized_quota = None if quota is None else max(1, int(quota))
        except (TypeError, ValueError):
            normalized_quota = None
        target_generation = pool.get("targetLtoGeneration", pool.get("type"))
        normalized_target = str(target_generation).strip() if isinstance(target_generation, str) and str(target_generation).strip() else None
        created_at = str(pool.get("createdAt", _isoformat(_utcnow())))
        color = str(pool.get("color", "#2563EB")).strip().upper() or "#2563EB"

        pool["id"] = normalized_id
        pool["name"] = str(pool.get("name", normalized_id)).strip() or normalized_id
        pool["policy"] = policy
        pool["maxDrives"] = normalized_max_drives
        pool["targetLtoGeneration"] = normalized_target
        pool["quotaGB"] = normalized_quota
        pool["color"] = color
        pool["assignedBarcodes"] = normalized
        pool["barcodes"] = list(normalized)
        pool["mediaCount"] = len(normalized)
        existing_type = pool.get("type")
        normalized_type = str(existing_type).strip() if isinstance(existing_type, str) and str(existing_type).strip() else None
        pool["type"] = normalized_target if normalized_target is not None else normalized_type
        pool["createdAt"] = created_at

        if normalized_id != pool_id:
            del _STATE.aml_media_pools[pool_id]
            _STATE.aml_media_pools[normalized_id] = pool



def list_aml_media() -> list[dict[str, Any]]:
    _sync_aml_media_counts()
    return [deepcopy(item) for _, item in sorted(_STATE.aml_media.items())]



def get_aml_media(barcode: str) -> dict[str, Any] | None:
    _sync_aml_media_counts()
    media = _STATE.aml_media.get(barcode)
    return deepcopy(media) if media is not None else None



def update_aml_media(barcode: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    media = _STATE.aml_media.get(barcode)
    if media is None:
        return None
    updates = deepcopy(updates)
    updates.pop("barcode", None)
    for key, value in updates.items():
        if value is not None:
            media[key] = value
    _sync_aml_media_counts()
    return get_aml_media(barcode)



def delete_aml_media(barcode: str) -> bool:
    if barcode not in _STATE.aml_media:
        return False
    del _STATE.aml_media[barcode]
    _sync_aml_media_counts()
    return True



def _next_media_slot_address(partition: str) -> str:
    slot_numbers: list[int] = []
    for media in _STATE.aml_media.values():
        if media.get("partition") != partition:
            continue
        parts = str(media.get("slotAddress", "")).split(",")
        if len(parts) != 3:
            continue
        try:
            slot_numbers.append(int(parts[-1]))
        except ValueError:
            continue
    next_slot = max(slot_numbers, default=0) + 1
    return f"1,1,{next_slot}"



def import_aml_media(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    imported: list[dict[str, Any]] = []
    for item in items:
        barcode = str(item["barcode"])
        media_type = str(item.get("type", "LTO-9"))
        existing = _STATE.aml_media.get(barcode)
        if existing is not None:
            existing["type"] = media_type
            imported.append(deepcopy(existing))
            continue
        partition = item.get("partition") or None
        stored = {
            "barcode": barcode,
            "type": media_type,
            "partition": partition,
            "slotAddress": str(item.get("slotAddress") or _next_media_slot_address(partition)),
            "state": str(item.get("state", "home")),
            "writeProtected": bool(item.get("writeProtected", False)),
            "worm": bool(item.get("worm", False)),
            "generations": int(item.get("generations", 1 if media_type.startswith("LTO") and "CLN" not in media_type else 0)),
            "loadCount": int(item.get("loadCount", 0)),
            "errorCount": int(item.get("errorCount", 0)),
            "lastLoaded": item.get("lastLoaded"),
        }
        _STATE.aml_media[barcode] = stored
        imported.append(deepcopy(stored))
    _sync_aml_media_counts()
    return imported



def export_aml_media(barcodes: list[str]) -> list[str]:
    removed: list[str] = []
    for barcode in barcodes:
        if barcode in _STATE.aml_media:
            del _STATE.aml_media[barcode]
            removed.append(barcode)
    _sync_aml_media_counts()
    return removed



def move_aml_media(barcode: str, destination: str) -> dict[str, Any] | None:
    media = _STATE.aml_media.get(barcode)
    if media is None:
        return None
    media["slotAddress"] = destination
    if destination.upper().startswith("DRV"):
        media["state"] = "loaded"
        media["lastLoaded"] = _isoformat(_utcnow())
        media["loadCount"] = int(media.get("loadCount", 0)) + 1
    elif destination.upper().startswith("IE"):
        media["state"] = "exported"
    else:
        media["state"] = "home"
    return get_aml_media(barcode)



def search_aml_media(
    *, partition: str | None = None, media_type: str | None = None, state: str | None = None, barcode: str | None = None
) -> list[dict[str, Any]]:
    items = list_aml_media()
    if partition is not None:
        items = [item for item in items if str(item.get("partition", "")).lower() == partition.lower()]
    if media_type is not None:
        items = [item for item in items if str(item.get("type", "")).lower() == media_type.lower()]
    if state is not None:
        items = [item for item in items if str(item.get("state", "")).lower() == state.lower()]
    if barcode is not None:
        items = [item for item in items if barcode.lower() in str(item.get("barcode", "")).lower()]
    return items



def list_aml_scratch_media(partition: str | None = None, media_type: str | None = None) -> list[dict[str, Any]]:
    items = search_aml_media(partition=partition, media_type=media_type)
    return [
        item
        for item in items
        if not bool(item.get("writeProtected", False))
        and not bool(item.get("worm", False))
        and "CLN" not in str(item.get("type", "")).upper()
        and str(item.get("state", "")).lower() in {"home", "scratch", "available"}
    ]



def list_aml_media_pools() -> list[dict[str, Any]]:
    _sync_aml_media_counts()
    return [deepcopy(item) for _, item in sorted(_STATE.aml_media_pools.items())]



def get_aml_media_pool(pool_id: str) -> dict[str, Any] | None:
    _sync_aml_media_counts()
    normalized_pool_id = _normalize_aml_media_pool_id(pool_id)
    pool = _STATE.aml_media_pools.get(normalized_pool_id)
    return deepcopy(pool) if pool is not None else None



def find_aml_media_pool_name(barcode: str) -> str | None:
    _sync_aml_media_counts()
    for pool in _STATE.aml_media_pools.values():
        if barcode in pool.get("assignedBarcodes", []):
            return str(pool.get("name"))
    return None



def assign_aml_media_to_pool(pool_id: str, barcodes: list[str]) -> dict[str, Any] | None:
    normalized_pool_id = _normalize_aml_media_pool_id(pool_id)
    pool = _STATE.aml_media_pools.get(normalized_pool_id)
    if pool is None:
        return None

    normalized_barcodes = [barcode for barcode in dict.fromkeys(barcodes) if barcode in _STATE.aml_media]
    if not normalized_barcodes:
        _sync_aml_media_counts()
        return get_aml_media_pool(normalized_pool_id)

    for candidate in _STATE.aml_media_pools.values():
        existing = candidate.get("assignedBarcodes") if isinstance(candidate.get("assignedBarcodes"), list) else []
        candidate["assignedBarcodes"] = [barcode for barcode in existing if barcode not in normalized_barcodes]

    assigned = pool.get("assignedBarcodes") if isinstance(pool.get("assignedBarcodes"), list) else []
    for barcode in normalized_barcodes:
        if barcode not in assigned:
            assigned.append(barcode)
    pool["assignedBarcodes"] = assigned
    _sync_aml_media_counts()
    return get_aml_media_pool(normalized_pool_id)



def unassign_aml_media_from_pool(pool_id: str, barcodes: list[str]) -> dict[str, Any] | None:
    normalized_pool_id = _normalize_aml_media_pool_id(pool_id)
    pool = _STATE.aml_media_pools.get(normalized_pool_id)
    if pool is None:
        return None

    normalized_barcodes = set(barcodes)
    existing = pool.get("assignedBarcodes") if isinstance(pool.get("assignedBarcodes"), list) else []
    pool["assignedBarcodes"] = [barcode for barcode in existing if barcode not in normalized_barcodes]
    _sync_aml_media_counts()
    return get_aml_media_pool(normalized_pool_id)



def create_aml_media_pool(pool_id: str, pool: dict[str, Any]) -> dict[str, Any] | None:
    normalized_pool_id = _normalize_aml_media_pool_id(pool_id)
    if normalized_pool_id in _STATE.aml_media_pools:
        return None

    if any(str(existing.get("name", "")).strip().lower() == str(pool.get("name", "")).strip().lower() for existing in _STATE.aml_media_pools.values()):
        return None

    stored = _build_aml_media_pool(
        pool_id=normalized_pool_id,
        name=str(pool.get("name", normalized_pool_id)),
        policy=str(pool.get("policy", "standard")),
        max_drives=int(pool.get("maxDrives", 1 if str(pool.get("policy")) == "critical" else 4)),
        target_lto_generation=pool.get("targetLtoGeneration", pool.get("type")),
        quota_gb=pool.get("quotaGB"),
        color=str(pool.get("color", "#2563EB")),
        assigned_barcodes=pool.get("assignedBarcodes") if isinstance(pool.get("assignedBarcodes"), list) else [],
        created_at=str(pool.get("createdAt")) if pool.get("createdAt") else None,
    )
    _STATE.aml_media_pools[normalized_pool_id] = stored
    _sync_aml_media_counts()
    return get_aml_media_pool(normalized_pool_id)



def update_aml_media_pool(pool_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    normalized_pool_id = _normalize_aml_media_pool_id(pool_id)
    pool = _STATE.aml_media_pools.get(normalized_pool_id)
    if pool is None:
        return None

    next_name = str(updates.get("name", pool.get("name", normalized_pool_id))).strip() or str(pool.get("name", normalized_pool_id))
    for existing_id, existing_pool in _STATE.aml_media_pools.items():
        if existing_id == normalized_pool_id:
            continue
        if str(existing_pool.get("name", "")).strip().lower() == next_name.lower():
            return None

    normalized_updates = deepcopy(updates)
    if "targetLtoGeneration" in normalized_updates:
        target_generation = normalized_updates["targetLtoGeneration"]
        if isinstance(target_generation, str):
            stripped_target = target_generation.strip()
            normalized_updates["type"] = stripped_target if stripped_target.upper().startswith("LTO-") else f"LTO-{stripped_target}"
        else:
            normalized_updates["type"] = None

    for key, value in normalized_updates.items():
        if key == "id":
            continue
        pool[key] = value
    pool["name"] = next_name
    _sync_aml_media_counts()
    return get_aml_media_pool(normalized_pool_id)



def delete_aml_media_pool(pool_id: str) -> bool:
    normalized_pool_id = _normalize_aml_media_pool_id(pool_id)
    if normalized_pool_id not in _STATE.aml_media_pools:
        return False
    del _STATE.aml_media_pools[normalized_pool_id]
    return True



def list_aml_drives() -> list[dict[str, Any]]:
    return [deepcopy(item) for _, item in sorted(_STATE.aml_drives.items())]



def get_aml_drive(serial_number: str) -> dict[str, Any] | None:
    drive = _STATE.aml_drives.get(serial_number)
    return deepcopy(drive) if drive is not None else None



def update_aml_drive(serial_number: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    drive = _STATE.aml_drives.get(serial_number)
    if drive is None:
        return None
    for key, value in deepcopy(updates).items():
        if key == "serialNumber":
            continue
        drive[key] = value
    return get_aml_drive(serial_number)



def list_blade_firmware() -> list[dict[str, Any]]:
    return [deepcopy(item) for item in sorted(_STATE.blade_firmware, key=lambda entry: str(entry.get("name", "")))]



def upsert_blade_firmware(item: dict[str, Any]) -> dict[str, Any]:
    current = deepcopy(item)
    name = str(current.get("name", "")).strip()
    for index, existing in enumerate(_STATE.blade_firmware):
        if str(existing.get("name", "")) == name:
            _STATE.blade_firmware[index] = current
            return deepcopy(current)
    _STATE.blade_firmware.append(current)
    return deepcopy(current)



def list_drive_firmware_images() -> list[dict[str, Any]]:
    return [deepcopy(item) for _, item in sorted(_STATE.drive_firmware_images.items())]



def get_drive_firmware_image(name: str) -> dict[str, Any] | None:
    image = _STATE.drive_firmware_images.get(name)
    return deepcopy(image) if image is not None else None



def upsert_drive_firmware_image(image: dict[str, Any]) -> dict[str, Any]:
    current = deepcopy(image)
    name = str(current.get("name", "")).strip()
    _STATE.drive_firmware_images[name] = current
    return deepcopy(current)



def delete_drive_firmware_image(name: str) -> bool:
    if name not in _STATE.drive_firmware_images:
        return False
    del _STATE.drive_firmware_images[name]
    return True



def activate_drive_firmware_image(name: str) -> dict[str, Any] | None:
    if name not in _STATE.drive_firmware_images:
        return None
    for image_name, image in _STATE.drive_firmware_images.items():
        image["active"] = image_name == name
    return get_drive_firmware_image(name)



def get_system_firmware_info() -> dict[str, Any]:
    return deepcopy(_STATE.system_firmware_info)



def set_system_firmware_info(info: dict[str, Any]) -> dict[str, Any]:
    _STATE.system_firmware_info = deepcopy(info)
    return get_system_firmware_info()



def list_aml_jobs() -> list[dict[str, Any]]:
    return [deepcopy(item) for _, item in sorted(_STATE.aml_jobs.items())]



def get_aml_job(job_id: str) -> dict[str, Any] | None:
    job = _STATE.aml_jobs.get(job_id)
    return deepcopy(job) if job is not None else None



def set_aml_job(job_id: str, job: dict[str, Any]) -> dict[str, Any]:
    stored = deepcopy(job)
    stored["id"] = job_id
    _STATE.aml_jobs[job_id] = stored
    return deepcopy(stored)



def update_aml_job(job_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    job = _STATE.aml_jobs.get(job_id)
    if job is None:
        return None
    for key, value in deepcopy(updates).items():
        if key == "id":
            continue
        job[key] = value
    return deepcopy(job)



def pop_aml_job(job_id: str) -> dict[str, Any] | None:
    job = _STATE.aml_jobs.pop(job_id, None)
    return deepcopy(job) if job is not None else None



def list_aml_job_history() -> list[dict[str, Any]]:
    return [deepcopy(item) for item in _STATE.aml_job_history]



def append_aml_job_history(job: dict[str, Any]) -> dict[str, Any]:
    stored = deepcopy(job)
    _STATE.aml_job_history.append(stored)
    return deepcopy(stored)



def clear_aml_job_history() -> None:
    _STATE.aml_job_history.clear()



def list_aml_moves() -> list[dict[str, Any]]:
    return [deepcopy(item) for _, item in sorted(_STATE.aml_moves.items())]



def get_aml_move(move_id: str) -> dict[str, Any] | None:
    move = _STATE.aml_moves.get(move_id)
    return deepcopy(move) if move is not None else None



def set_aml_move(move_id: str, move: dict[str, Any]) -> dict[str, Any]:
    stored = deepcopy(move)
    stored["id"] = move_id
    _STATE.aml_moves[move_id] = stored
    return deepcopy(stored)



def update_aml_move(move_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    move = _STATE.aml_moves.get(move_id)
    if move is None:
        return None
    for key, value in deepcopy(updates).items():
        if key == "id":
            continue
        move[key] = value
    return deepcopy(move)



def pop_aml_move(move_id: str) -> dict[str, Any] | None:
    move = _STATE.aml_moves.pop(move_id, None)
    return deepcopy(move) if move is not None else None



def list_aml_mounts() -> list[dict[str, Any]]:
    return [deepcopy(item) for _, item in sorted(_STATE.aml_mounts.items())]



def get_aml_mount(mount_id: str) -> dict[str, Any] | None:
    mount = _STATE.aml_mounts.get(mount_id)
    return deepcopy(mount) if mount is not None else None



def set_aml_mount(mount_id: str, mount: dict[str, Any]) -> dict[str, Any]:
    stored = deepcopy(mount)
    stored["id"] = mount_id
    _STATE.aml_mounts[mount_id] = stored
    return deepcopy(stored)



def update_aml_mount(mount_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    mount = _STATE.aml_mounts.get(mount_id)
    if mount is None:
        return None
    for key, value in deepcopy(updates).items():
        if key == "id":
            continue
        mount[key] = value
    return deepcopy(mount)



def pop_aml_mount(mount_id: str) -> dict[str, Any] | None:
    mount = _STATE.aml_mounts.pop(mount_id, None)
    return deepcopy(mount) if mount is not None else None



def get_aml_inventory_status() -> dict[str, Any]:
    return deepcopy(_STATE.aml_inventory_status)



def set_aml_inventory_status(status: dict[str, Any]) -> dict[str, Any]:
    _STATE.aml_inventory_status = deepcopy(status)
    return get_aml_inventory_status()



def get_aml_import_status() -> dict[str, Any]:
    return deepcopy(_STATE.aml_import_status)



def set_aml_import_status(status: dict[str, Any]) -> dict[str, Any]:
    _STATE.aml_import_status = deepcopy(status)
    return get_aml_import_status()



def get_aml_export_status() -> dict[str, Any]:
    return deepcopy(_STATE.aml_export_status)



def set_aml_export_status(status: dict[str, Any]) -> dict[str, Any]:
    _STATE.aml_export_status = deepcopy(status)
    return get_aml_export_status()


def get_aml_cleaning_status() -> dict[str, Any]:
    return deepcopy(_STATE.aml_cleaning_status)


def set_aml_cleaning_status(status: dict[str, Any]) -> dict[str, Any]:
    _STATE.aml_cleaning_status = deepcopy(status)
    return get_aml_cleaning_status()



def list_aml_drive_cleaning_reports() -> list[dict[str, Any]]:
    return [deepcopy(item) for item in sorted(_STATE.aml_drive_cleaning_reports, key=lambda item: str(item.get("lastCleaned", "")), reverse=True)]



def append_aml_drive_cleaning_report(report: dict[str, Any]) -> dict[str, Any]:
    stored = deepcopy(report)
    _STATE.aml_drive_cleaning_reports.append(stored)
    return deepcopy(stored)



def list_aml_drive_operation_tasks(
    *,
    task_type: str | None = None,
    component_id: str | None = None,
) -> list[dict[str, Any]]:
    tasks = [deepcopy(item) for _, item in sorted(_STATE.aml_drive_operation_tasks.items())]
    if task_type is not None:
        tasks = [task for task in tasks if str(task.get("type")) == task_type]
    if component_id is not None:
        tasks = [task for task in tasks if str(task.get("componentId")) == component_id]
    return sorted(tasks, key=lambda item: (str(item.get("opened", "")), str(item.get("id", ""))), reverse=True)



def get_aml_drive_operation_task(task_id: str) -> dict[str, Any] | None:
    task = _STATE.aml_drive_operation_tasks.get(task_id)
    return deepcopy(task) if task is not None else None



def set_aml_drive_operation_task(task_id: str, task: dict[str, Any]) -> dict[str, Any]:
    stored = deepcopy(task)
    stored["id"] = task_id
    _STATE.aml_drive_operation_tasks[task_id] = stored
    return deepcopy(stored)



def list_aml_diagnostic_tests() -> list[dict[str, Any]]:
    return [deepcopy(item) for _, item in sorted(_STATE.aml_diagnostic_tests.items())]



def get_aml_diagnostic_test(test_id: str) -> dict[str, Any] | None:
    test = _STATE.aml_diagnostic_tests.get(test_id)
    return deepcopy(test) if test is not None else None



def list_aml_diagnostic_results() -> list[dict[str, Any]]:
    return [deepcopy(item) for _, item in sorted(_STATE.aml_diagnostic_results.items())]



def get_aml_diagnostic_result(result_id: str) -> dict[str, Any] | None:
    result = _STATE.aml_diagnostic_results.get(result_id)
    return deepcopy(result) if result is not None else None



def set_aml_diagnostic_result(result_id: str, result: dict[str, Any]) -> dict[str, Any]:
    stored = deepcopy(result)
    stored["id"] = result_id
    _STATE.aml_diagnostic_results[result_id] = stored
    return deepcopy(stored)



def get_latest_aml_diagnostic_result() -> dict[str, Any] | None:
    results = list_aml_diagnostic_results()
    if not results:
        return None
    return max(results, key=lambda item: (str(item.get("endTime", "")), str(item.get("startTime", "")), str(item.get("id", ""))))



def get_aml_robotics_last_test_time() -> str | None:
    return _STATE.aml_robotics_last_test_time


def set_aml_robotics_last_test_time(ts: str | None) -> None:
    _STATE.aml_robotics_last_test_time = ts



def list_aml_events() -> list[dict[str, Any]]:
    return [deepcopy(item) for item in sorted(_STATE.aml_events, key=lambda item: str(item.get("timestamp", "")), reverse=True)]



def get_aml_event(event_id: str) -> dict[str, Any] | None:
    for event in _STATE.aml_events:
        if event.get("id") == event_id:
            return deepcopy(event)
    return None



def append_aml_event(event: dict[str, Any]) -> dict[str, Any]:
    stored = deepcopy(event)
    _STATE.aml_events.append(stored)
    _STATE.aml_events.sort(key=lambda item: str(item.get("timestamp", "")), reverse=True)
    return deepcopy(stored)



def clear_aml_events() -> None:
    _STATE.aml_events.clear()



def list_aml_ras_tickets() -> list[dict[str, Any]]:
    return [deepcopy(item) for _, item in sorted(_STATE.aml_ras_tickets.items())]



def get_aml_ras_ticket(ticket_id: str) -> dict[str, Any] | None:
    ticket = _STATE.aml_ras_tickets.get(ticket_id)
    return deepcopy(ticket) if ticket is not None else None



def set_aml_ras_ticket(ticket_id: str, ticket: dict[str, Any]) -> dict[str, Any]:
    stored = deepcopy(ticket)
    stored["id"] = ticket_id
    _STATE.aml_ras_tickets[ticket_id] = stored
    return deepcopy(stored)



def update_aml_ras_ticket(ticket_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    ticket = _STATE.aml_ras_tickets.get(ticket_id)
    if ticket is None:
        return None
    for key, value in deepcopy(updates).items():
        if key == "id":
            continue
        ticket[key] = value
    return deepcopy(ticket)



def pop_aml_ras_ticket(ticket_id: str) -> dict[str, Any] | None:
    ticket = _STATE.aml_ras_tickets.pop(ticket_id, None)
    return deepcopy(ticket) if ticket is not None else None



def list_aml_logs() -> list[dict[str, Any]]:
    return [deepcopy(item) for _, item in sorted(_STATE.aml_logs_store.items())]



def get_aml_log(name: str) -> dict[str, Any] | None:
    log = _STATE.aml_logs_store.get(name)
    return deepcopy(log) if log is not None else None



def set_aml_log(name: str, log: dict[str, Any]) -> dict[str, Any]:
    stored = deepcopy(log)
    stored["name"] = name
    _STATE.aml_logs_store[name] = stored
    return deepcopy(stored)



def pop_aml_log(name: str) -> dict[str, Any] | None:
    log = _STATE.aml_logs_store.pop(name, None)
    return deepcopy(log) if log is not None else None



def list_aml_alerts() -> list[dict[str, Any]]:
    return [deepcopy(item) for _, item in sorted(_STATE.aml_alerts_store.items())]



def get_aml_alert(alert_id: str) -> dict[str, Any] | None:
    alert = _STATE.aml_alerts_store.get(alert_id)
    return deepcopy(alert) if alert is not None else None



def set_aml_alert(alert_id: str, alert: dict[str, Any]) -> dict[str, Any]:
    stored = deepcopy(alert)
    stored["id"] = alert_id
    _STATE.aml_alerts_store[alert_id] = stored
    return deepcopy(stored)



def update_aml_alert(alert_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    alert = _STATE.aml_alerts_store.get(alert_id)
    if alert is None:
        return None
    for key, value in deepcopy(updates).items():
        if key == "id":
            continue
        alert[key] = value
    return deepcopy(alert)



def pop_aml_alert(alert_id: str) -> dict[str, Any] | None:
    alert = _STATE.aml_alerts_store.pop(alert_id, None)
    return deepcopy(alert) if alert is not None else None



def clear_aml_alerts() -> None:
    _STATE.aml_alerts_store.clear()



def list_aml_tapealerts() -> list[dict[str, Any]]:
    return [deepcopy(item) for item in _STATE.aml_tapealerts]



def set_aml_tapealerts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    _STATE.aml_tapealerts = [deepcopy(item) for item in items]
    return list_aml_tapealerts()



def clear_aml_tapealerts() -> None:
    _STATE.aml_tapealerts.clear()



def list_aml_notifications() -> list[dict[str, Any]]:
    return [deepcopy(item) for _, item in sorted(_STATE.aml_notifications.items())]



def get_aml_notification(notification_id: str) -> dict[str, Any] | None:
    notification = _STATE.aml_notifications.get(notification_id)
    return deepcopy(notification) if notification is not None else None



def set_aml_notification(notification_id: str, notification: dict[str, Any]) -> dict[str, Any]:
    stored = deepcopy(notification)
    stored["id"] = notification_id
    _STATE.aml_notifications[notification_id] = stored
    return deepcopy(stored)



def update_aml_notification(notification_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    notification = _STATE.aml_notifications.get(notification_id)
    if notification is None:
        return None
    for key, value in deepcopy(updates).items():
        if key == "id":
            continue
        notification[key] = value
    return deepcopy(notification)



def pop_aml_notification(notification_id: str) -> dict[str, Any] | None:
    notification = _STATE.aml_notifications.pop(notification_id, None)
    return deepcopy(notification) if notification is not None else None



def clear_aml_notifications() -> None:
    _STATE.aml_notifications.clear()



def get_aml_log_level() -> dict[str, Any]:
    return deepcopy(_STATE.aml_log_level)



def set_aml_log_level(payload: dict[str, Any]) -> dict[str, Any]:
    _STATE.aml_log_level = deepcopy(payload)
    return get_aml_log_level()



def list_aml_event_subscriptions() -> list[dict[str, Any]]:
    return [deepcopy(item) for item in _STATE.aml_event_subscriptions]



def add_aml_event_subscription(subscription: dict[str, Any]) -> dict[str, Any]:
    stored = deepcopy(subscription)
    _STATE.aml_event_subscriptions.append(stored)
    return deepcopy(stored)



def clear_aml_event_subscriptions() -> None:
    _STATE.aml_event_subscriptions.clear()


def get_aml_system_certificates() -> list[dict[str, Any]]:
    return deepcopy(_STATE.aml_system_certificates)


def set_aml_system_certificates(certs: list[dict[str, Any]]) -> None:
    _STATE.aml_system_certificates = deepcopy(certs)


def get_aml_system_cert_info() -> dict[str, Any]:
    return deepcopy(_STATE.aml_system_cert_info)


def set_aml_system_cert_info(info: dict[str, Any]) -> None:
    _STATE.aml_system_cert_info = deepcopy(info)


def get_aml_system_recent_traps() -> list[dict[str, Any]]:
    return deepcopy(_STATE.aml_system_recent_traps)


def set_aml_system_recent_traps(traps: list[dict[str, Any]]) -> None:
    _STATE.aml_system_recent_traps = deepcopy(traps)


def append_aml_system_trap(trap: dict[str, Any]) -> None:
    _STATE.aml_system_recent_traps.append(deepcopy(trap))


def get_aml_system_storage_volumes() -> dict[str, dict[str, Any]]:
    return deepcopy(_STATE.aml_system_storage_volumes)


def set_aml_system_storage_volumes(volumes: dict[str, dict[str, Any]]) -> None:
    _STATE.aml_system_storage_volumes = deepcopy(volumes)


def get_aml_system_backup_status() -> dict[str, Any]:
    return deepcopy(_STATE.aml_system_backup_status)


def set_aml_system_backup_status(status: dict[str, Any]) -> None:
    _STATE.aml_system_backup_status = deepcopy(status)


def get_aml_system_backup_history() -> list[dict[str, Any]]:
    return deepcopy(_STATE.aml_system_backup_history)


def set_aml_system_backup_history(entries: list[dict[str, Any]]) -> None:
    _STATE.aml_system_backup_history = deepcopy(entries)


def append_aml_system_backup(entry: dict[str, Any]) -> None:
    _STATE.aml_system_backup_history.append(deepcopy(entry))


def get_aml_system_available_updates() -> list[dict[str, Any]]:
    return deepcopy(_STATE.aml_system_available_updates)


def set_aml_system_available_updates(updates: list[dict[str, Any]]) -> None:
    _STATE.aml_system_available_updates = deepcopy(updates)


def get_aml_system_update_status() -> dict[str, Any]:
    return deepcopy(_STATE.aml_system_update_status)


def set_aml_system_update_status(status: dict[str, Any]) -> None:
    _STATE.aml_system_update_status = deepcopy(status)


def get_aml_system_last_diagnostics() -> dict[str, Any]:
    return deepcopy(_STATE.aml_system_last_diagnostics)


def set_aml_system_last_diagnostics(diag: dict[str, Any]) -> None:
    _STATE.aml_system_last_diagnostics = deepcopy(diag)


def get_aml_system_support_bundle() -> dict[str, Any]:
    return deepcopy(_STATE.aml_system_support_bundle)


def set_aml_system_support_bundle(bundle: dict[str, Any]) -> None:
    _STATE.aml_system_support_bundle = deepcopy(bundle)


def get_aml_system_manual_time_utc() -> str | None:
    return _STATE.aml_system_manual_time_utc


def set_aml_system_manual_time_utc(ts: str | None) -> None:
    _STATE.aml_system_manual_time_utc = ts



def get_fc_blade_by_serial(serial_number: str) -> dict[str, Any] | None:
    for blade in _STATE.fc_blades.values():
        if str(blade.get("serialNumber")) == serial_number:
            return deepcopy(blade)
    return None



def update_fc_blade_by_serial(serial_number: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    for blade_id, blade in _STATE.fc_blades.items():
        if str(blade.get("serialNumber")) == serial_number:
            return update_fc_blade(blade_id, updates)
    return None



def get_fc_port_by_number(serial_number: str, port_number: int) -> dict[str, Any] | None:
    blade = get_fc_blade_by_serial(serial_number)
    if blade is None:
        return None
    for port in blade.get("ports", []):
        if int(port.get("portNumber", 0)) == port_number:
            return port
    return None



def update_fc_port_by_number(serial_number: str, port_number: int, updates: dict[str, Any]) -> dict[str, Any] | None:
    for blade in _STATE.fc_blades.values():
        if str(blade.get("serialNumber")) != serial_number:
            continue
        for port in blade.get("ports", []):
            if int(port.get("portNumber", 0)) == port_number:
                port.update(deepcopy(updates))
                return deepcopy(port)
        return None
    return None



def list_aml_ltfs_sections() -> list[dict[str, Any]]:
    return [deepcopy(item) for _, item in sorted(_STATE.aml_ltfs_sections.items(), key=lambda entry: int(entry[0]))]



def get_aml_ltfs_section(section_number: int | str) -> dict[str, Any] | None:
    section = _STATE.aml_ltfs_sections.get(str(section_number))
    return deepcopy(section) if section is not None else None



def update_aml_ltfs_section(section_number: int | str, updates: dict[str, Any]) -> dict[str, Any] | None:
    section = _STATE.aml_ltfs_sections.get(str(section_number))
    if section is None:
        return None
    for key, value in deepcopy(updates).items():
        if value is not None:
            section[key] = value
    return get_aml_ltfs_section(section_number)



def get_aml_iscsi_blade(serial_number: str) -> dict[str, Any] | None:
    blade = _STATE.aml_iscsi_blades.get(serial_number)
    return deepcopy(blade) if blade is not None else None



def update_aml_iscsi_blade(serial_number: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    blade = _STATE.aml_iscsi_blades.get(serial_number)
    if blade is None:
        return None
    for key, value in deepcopy(updates).items():
        if value is not None:
            blade[key] = value
    return get_aml_iscsi_blade(serial_number)



def get_aml_advanced_ha_config() -> dict[str, Any]:
    return deepcopy(_STATE.aml_advanced_ha_config)



def set_aml_advanced_ha_config(updates: dict[str, Any]) -> dict[str, Any]:
    for key, value in deepcopy(updates).items():
        if value is not None:
            _STATE.aml_advanced_ha_config[key] = value
    return get_aml_advanced_ha_config()



def list_aml_advanced_ha_nodes() -> list[dict[str, Any]]:
    return [deepcopy(item) for _, item in sorted(_STATE.aml_advanced_ha_nodes.items())]



def get_aml_advanced_ha_node(node_id: str) -> dict[str, Any] | None:
    node = _STATE.aml_advanced_ha_nodes.get(node_id)
    return deepcopy(node) if node is not None else None



def update_aml_advanced_ha_node(node_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    node = _STATE.aml_advanced_ha_nodes.get(node_id)
    if node is None:
        return None
    for key, value in deepcopy(updates).items():
        if value is not None:
            node[key] = value
    return get_aml_advanced_ha_node(node_id)



def get_aml_ekm_config() -> dict[str, Any]:
    return deepcopy(_STATE.aml_ekm_config)



def set_aml_ekm_config(updates: dict[str, Any]) -> dict[str, Any]:
    for key, value in deepcopy(updates).items():
        if value is not None:
            _STATE.aml_ekm_config[key] = value
    return get_aml_ekm_config()



def get_aml_ekm_status() -> dict[str, Any]:
    return deepcopy(_STATE.aml_ekm_status)



def set_aml_ekm_status(status: dict[str, Any]) -> dict[str, Any]:
    _STATE.aml_ekm_status = deepcopy(status)
    return get_aml_ekm_status()



def list_aml_ekm_keys() -> list[dict[str, Any]]:
    return [deepcopy(item) for _, item in sorted(_STATE.aml_ekm_keys.items())]



def set_aml_ekm_keys(keys: list[dict[str, Any]]) -> list[dict[str, Any]]:
    _STATE.aml_ekm_keys = {str(item.get("keyId")): deepcopy(item) for item in keys}
    return list_aml_ekm_keys()



def get_aml_sharing_config() -> dict[str, Any]:
    return deepcopy(_STATE.aml_sharing_config)



def set_aml_sharing_config(updates: dict[str, Any]) -> dict[str, Any]:
    for key, value in deepcopy(updates).items():
        if value is not None:
            _STATE.aml_sharing_config[key] = value
    return get_aml_sharing_config()



def get_aml_sharing_status() -> dict[str, Any]:
    return deepcopy(_STATE.aml_sharing_status)



def set_aml_sharing_status(status: dict[str, Any]) -> dict[str, Any]:
    _STATE.aml_sharing_status = deepcopy(status)
    return get_aml_sharing_status()



def list_aml_sharing_clients() -> list[dict[str, Any]]:
    return [deepcopy(item) for _, item in sorted(_STATE.aml_sharing_clients.items())]



def set_aml_sharing_clients(clients: list[dict[str, Any]]) -> list[dict[str, Any]]:
    _STATE.aml_sharing_clients = {str(item.get("id")): deepcopy(item) for item in clients}
    return list_aml_sharing_clients()



def list_aml_remote_libraries() -> list[dict[str, Any]]:
    return [deepcopy(item) for _, item in sorted(_STATE.aml_remote_libraries.items())]



def get_aml_remote_library(library_id: str) -> dict[str, Any] | None:
    library = _STATE.aml_remote_libraries.get(library_id)
    return deepcopy(library) if library is not None else None



def create_aml_remote_library(payload: dict[str, Any]) -> dict[str, Any]:
    next_id = f"rlib-{len(_STATE.aml_remote_libraries) + 1}"
    library = {"id": next_id, **deepcopy(payload)}
    _STATE.aml_remote_libraries[next_id] = library
    return deepcopy(library)



def update_aml_remote_library(library_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    library = _STATE.aml_remote_libraries.get(library_id)
    if library is None:
        return None
    for key, value in deepcopy(updates).items():
        if key == "id":
            continue
        if value is not None:
            library[key] = value
    return get_aml_remote_library(library_id)



def delete_aml_remote_library(library_id: str) -> bool:
    if library_id not in _STATE.aml_remote_libraries:
        return False
    del _STATE.aml_remote_libraries[library_id]
    return True



def list_aml_supported_media() -> list[dict[str, Any]]:
    return deepcopy(_STATE.aml_supported_media)
