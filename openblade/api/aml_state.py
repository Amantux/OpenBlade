"""Shared Authentication & Access Control simulator state for AML routes."""

from __future__ import annotations

import base64
import hashlib
import secrets
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
