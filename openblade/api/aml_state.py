"""Shared Authentication & Access Control simulator state for AML routes."""

from __future__ import annotations

import base64
import hashlib
import secrets
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pyotp
from sqlalchemy import select

from openblade.catalog.db import get_session, init_db
from openblade.catalog.models import AmlUser

DEFAULT_ADMIN_PASSWORD = "password"
DEFAULT_SERVICE_PASSWORD = "service123"
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
    return {
        "partition1": {
            "name": "partition1",
            "id": "PART-001",
            "status": "online",
            "type": "data",
            "driveCount": 2,
            "slotCount": 20,
            "ieSlotCount": 6,
            "cleaningSlots": 2,
            "mediaCount": 10,
            "drives": ["DRV-001", "DRV-002"],
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
                "cleaningTapeBarcode": None,
                "lastCleaned": None,
            },
            "worm": {"enabled": False, "mode": "none"},
            "encryption": {"enabled": False, "type": "none", "keyManager": None},
            "qos": {"maxMountsPerHour": 60, "priority": "normal", "preemption": False},
            "lme": {"enabled": False, "exportPath": None},
            "alerts": [],
            "moveQueue": [],
            "quota": {"totalSlots": 20, "usedSlots": 10, "totalDrives": 2, "usedDrives": 2},
            "statistics": {
                "mountCount": 0,
                "unmountCount": 0,
                "errorCount": 0,
                "lastMount": None,
                "lastUnmount": None,
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



def _default_aml_media() -> dict[str, dict[str, Any]]:
    return {
        "VOL001L9": {
            "barcode": "VOL001L9",
            "type": "LTO-9",
            "partition": "partition1",
            "slotAddress": "1,1,1",
            "state": "home",
            "writeProtected": False,
            "worm": False,
            "generations": 1,
            "loadCount": 5,
            "errorCount": 0,
            "lastLoaded": "2024-01-15T10:00:00Z",
        },
        "VOL002L9": {
            "barcode": "VOL002L9",
            "type": "LTO-9",
            "partition": "partition1",
            "slotAddress": "1,1,2",
            "state": "home",
            "writeProtected": False,
            "worm": False,
            "generations": 1,
            "loadCount": 3,
            "errorCount": 0,
            "lastLoaded": "2024-01-14T08:00:00Z",
        },
        "CLN001L9": {
            "barcode": "CLN001L9",
            "type": "LTO-9-CLN",
            "partition": "partition1",
            "slotAddress": "1,0,1",
            "state": "home",
            "writeProtected": True,
            "worm": False,
            "generations": 0,
            "loadCount": 2,
            "errorCount": 0,
            "lastLoaded": "2024-01-10T12:00:00Z",
        },
    }



def _default_aml_media_pools() -> dict[str, dict[str, Any]]:
    return {
        "default": {"name": "default", "type": "LTO-9", "mediaCount": 2, "policy": "scratch"},
        "cleaning": {"name": "cleaning", "type": "LTO-9-CLN", "mediaCount": 1, "policy": "cleaning"},
    }



def _default_aml_drives() -> dict[str, dict[str, Any]]:
    return {
        "DRV-001": {
            "serialNumber": "DRV-001",
            "model": "IBM LTO-9 HH",
            "type": "LTO-9",
            "status": "online",
            "state": "idle",
            "partition": "partition1",
            "location": "1,1,1",
            "firmware": "H3J4",
            "loadCount": 142,
            "errorCount": 0,
            "cleaningCount": 3,
            "lastCleaned": "2024-01-10T08:00:00Z",
            "loadedMedia": None,
            "config": {"compression": True, "encryption": False, "speed": "400MB/s", "bufferSize": "256MB"},
            "errors": [],
            "diagnosticResult": None,
        },
        "DRV-002": {
            "serialNumber": "DRV-002",
            "model": "IBM LTO-9 HH",
            "type": "LTO-9",
            "status": "online",
            "state": "idle",
            "partition": "partition1",
            "location": "1,1,2",
            "firmware": "H3J4",
            "loadCount": 87,
            "errorCount": 0,
            "cleaningCount": 2,
            "lastCleaned": "2024-01-08T14:00:00Z",
            "loadedMedia": None,
            "config": {"compression": True, "encryption": False, "speed": "400MB/s", "bufferSize": "256MB"},
            "errors": [],
            "diagnosticResult": None,
        },
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
                    "wwpn": f"50:00:00:00:00:00:00:0{i}",
                    "speed": "16G",
                    "status": "online",
                    "mode": "target",
                    "topology": "point-to-point",
                }
                for i in range(1, 5)
            ],
        }
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
    return {
        "IE-1": {
            "id": "IE-1",
            "serialNumber": "IE0001",
            "status": "online",
            "state": "closed",
            "slotCount": 6,
            "slots": [
                {
                    "id": f"IE-1-S{i}",
                    "address": f"0,0,{i}",
                    "state": "empty",
                    "barcode": None,
                    "type": "ie",
                }
                for i in range(1, 7)
            ],
        }
    }


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


def ensure_initialized(db_url: str) -> None:
    global _STATE
    normalized_db_url = _normalize_db_url(db_url)
    init_db(normalized_db_url)
    if _STATE.db_url != normalized_db_url:
        _STATE = AMLState(db_url=normalized_db_url)
    _seed_default_users()
    _migrate_plaintext_passwords()
    purge_expired_sessions()


def _seed_default_users() -> None:
    with get_session() as session:
        admin = session.get(AmlUser, "admin")
        if admin is None:
            session.add(
                AmlUser(
                    name="admin",
                    password=hash_password(DEFAULT_ADMIN_PASSWORD),
                    role=0,
                    require_password_change=True,
                )
            )
        service = session.get(AmlUser, "service")
        if service is None:
            session.add(
                AmlUser(
                    name="service",
                    password=hash_password(DEFAULT_SERVICE_PASSWORD),
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
                password=hash_password(DEFAULT_ADMIN_PASSWORD),
                role=0,
                require_password_change=True,
            )
            session.add(admin)
        else:
            admin.password = hash_password(DEFAULT_ADMIN_PASSWORD)
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
    for pool in _STATE.aml_media_pools.values():
        pool["mediaCount"] = sum(1 for media in _STATE.aml_media.values() if media.get("type") == pool.get("type"))



def list_aml_media() -> list[dict[str, Any]]:
    _sync_aml_media_counts()
    return [deepcopy(item) for _, item in sorted(_STATE.aml_media.items())]



def get_aml_media(barcode: str) -> dict[str, Any] | None:
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



def get_aml_media_pool(name: str) -> dict[str, Any] | None:
    _sync_aml_media_counts()
    pool = _STATE.aml_media_pools.get(name)
    return deepcopy(pool) if pool is not None else None



def create_aml_media_pool(name: str, pool: dict[str, Any]) -> dict[str, Any] | None:
    if name in _STATE.aml_media_pools:
        return None
    stored = {
        "name": name,
        "type": pool.get("type", "LTO-9"),
        "mediaCount": 0,
        "policy": pool.get("policy", "scratch"),
    }
    _STATE.aml_media_pools[name] = stored
    _sync_aml_media_counts()
    return get_aml_media_pool(name)



def update_aml_media_pool(name: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    pool = _STATE.aml_media_pools.get(name)
    if pool is None:
        return None
    for key, value in deepcopy(updates).items():
        if key == "name" or value is None:
            continue
        pool[key] = value
    _sync_aml_media_counts()
    return get_aml_media_pool(name)



def delete_aml_media_pool(name: str) -> bool:
    if name not in _STATE.aml_media_pools:
        return False
    del _STATE.aml_media_pools[name]
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
