"""OpenBlade configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class BackendMode(str, Enum):
    MOCK = "mock"
    REAL = "real"


class IBladeCompatibilityMode(str, Enum):
    STRICT = "strict"
    EXTENDED = "extended"


_DEFAULT_HOME = Path.home() / ".openblade"
_DEFAULT_EMULATOR_URLS = (
    "http://localhost:8010",
    "http://localhost:8011",
    "http://localhost:8012",
)
_LATENCY_PROFILES = {"instant", "realistic", "hardware", "custom"}


@dataclass(frozen=True)
class OpenBladeConfig:
    backend: BackendMode = BackendMode.MOCK
    real_hardware_enabled: bool = False
    db_url: str = f"sqlite:///{_DEFAULT_HOME / 'openblade.db'}"
    log_level: str = "INFO"
    cache_dir: str = str(_DEFAULT_HOME / "cache")
    staging_dir: str = str(_DEFAULT_HOME / "staging")
    restore_dir: str = str(_DEFAULT_HOME / "restore")
    fuse_mount_point: str = str(_DEFAULT_HOME / "mount")
    ltfs_mount_root: str = str(_DEFAULT_HOME / "ltfs")
    job_timeout_seconds: int = 3600
    changer_timeout_seconds: int = 60
    drive_timeout_seconds: int = 300
    hardware_dry_run: bool = False
    changer_device: str | None = None
    # Host tape devices, in LIBRARY drive-element order when no serial map is
    # declared. Prefer the no-rewind nodes (/dev/nst0,...). Empty means "fall back
    # to auto-discovery order", which is NOT a verified ordering — see
    # drive_serial_map and openblade/hardware/correlation.py.
    drive_devices: tuple[str, ...] = ()
    # Operator-declared correlation of drive unit serial numbers to library drive
    # elements (mtx Data Transfer Element indices), parsed from
    # OPENBLADE_DRIVE_SERIAL_MAP="<serial>:<dte>,...". Verified live against sg_inq
    # at startup; empty means unverified positional order.
    drive_serial_map: tuple[tuple[str, int], ...] = ()
    # Robotics transport for BackendMode.REAL: "scsi" (mtx/host changer) or
    # "webservices" (drive robotics over a real Scalar i3 AML Web Services API).
    robotics_transport: str = "scsi"
    scalar_url: str | None = None
    scalar_user: str = "admin"
    scalar_password: str = ""
    scalar_verify_tls: bool = True
    emulator_urls: tuple[str, ...] = _DEFAULT_EMULATOR_URLS
    emulator_latency_profile: str = "instant"
    emulator_latency_enabled: bool = True
    scalar_api_only: bool = False
    iblade_compat_mode: IBladeCompatibilityMode = IBladeCompatibilityMode.EXTENDED


def _env_bool(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _load_emulator_urls() -> tuple[str, ...]:
    raw_urls = os.environ.get("OPENBLADE_EMULATOR_URLS", "")
    if not raw_urls.strip():
        return _DEFAULT_EMULATOR_URLS

    parsed = tuple(
        candidate.rstrip("/")
        for candidate in (url.strip() for url in raw_urls.split(","))
        if candidate
    )
    return parsed or _DEFAULT_EMULATOR_URLS


def _load_emulator_latency_profile() -> str:
    raw = os.environ.get("OPENBLADE_EMULATOR_LATENCY_PROFILE") or os.environ.get(
        "EMULATOR_LATENCY_PROFILE", "instant"
    )
    normalized = raw.strip().lower()
    if normalized in _LATENCY_PROFILES:
        return normalized
    return "instant"


def _load_emulator_latency_enabled() -> bool:
    if "OPENBLADE_EMULATOR_LATENCY_ENABLED" in os.environ:
        return _env_bool("OPENBLADE_EMULATOR_LATENCY_ENABLED", default=True)
    return _env_bool("EMULATOR_LATENCY_ENABLED", default=True)


def parse_drive_serial_map(raw: str) -> tuple[tuple[str, int], ...]:
    """Parse ``"SERIAL:DTE,SERIAL:DTE"`` into ``((serial, drive_id), ...)``.

    Raises ``ValueError`` on a malformed entry: a typo in this variable must fail
    loudly at load time, never silently drop a drive from the mapping (a dropped
    entry degrades to positional order, which is the bug this map exists to stop).
    """
    entries: list[tuple[str, int]] = []
    seen_serials: set[str] = set()
    seen_drive_ids: set[int] = set()
    for chunk in raw.split(","):
        item = chunk.strip()
        if not item:
            continue
        serial, separator, drive_text = item.rpartition(":")
        serial = serial.strip()
        drive_text = drive_text.strip()
        if not separator or not serial or not drive_text:
            raise ValueError(
                f"OPENBLADE_DRIVE_SERIAL_MAP entry {item!r} is not of the form "
                "'<serial>:<drive_element_id>'"
            )
        try:
            drive_id = int(drive_text)
        except ValueError as exc:
            raise ValueError(
                f"OPENBLADE_DRIVE_SERIAL_MAP entry {item!r} has a non-integer "
                f"drive element id {drive_text!r}"
            ) from exc
        if drive_id < 0:
            raise ValueError(
                f"OPENBLADE_DRIVE_SERIAL_MAP entry {item!r} has a negative drive element id"
            )
        if serial in seen_serials:
            raise ValueError(f"OPENBLADE_DRIVE_SERIAL_MAP lists serial {serial!r} more than once")
        if drive_id in seen_drive_ids:
            raise ValueError(
                f"OPENBLADE_DRIVE_SERIAL_MAP lists drive element id {drive_id} more than once"
            )
        seen_serials.add(serial)
        seen_drive_ids.add(drive_id)
        entries.append((serial, drive_id))
    return tuple(entries)


def _load_iblade_compat_mode() -> IBladeCompatibilityMode:
    raw = os.environ.get("OPENBLADE_IBLADE_COMPAT_MODE", "extended").strip().lower()
    if raw in {"strict", "strict-interface"}:
        return IBladeCompatibilityMode.STRICT
    if raw in {"extended", "openblade-extended"}:
        return IBladeCompatibilityMode.EXTENDED
    return IBladeCompatibilityMode.EXTENDED


def load_config() -> OpenBladeConfig:
    backend_str = os.environ.get("OPENBLADE_BACKEND", "mock").lower()
    try:
        backend = BackendMode(backend_str)
    except ValueError:
        backend = BackendMode.MOCK

    real_hw = os.environ.get("OPENBLADE_REAL_HARDWARE_ENABLED", "false").lower() == "true"
    drive_devices = tuple(
        device.strip()
        for device in os.environ.get("OPENBLADE_DRIVE_DEVICES", "").split(",")
        if device.strip()
    )

    return OpenBladeConfig(
        backend=backend,
        real_hardware_enabled=real_hw,
        db_url=os.environ.get("OPENBLADE_DB_URL", f"sqlite:///{_DEFAULT_HOME / 'openblade.db'}"),
        log_level=os.environ.get("OPENBLADE_LOG_LEVEL", "INFO"),
        cache_dir=os.environ.get("OPENBLADE_CACHE_DIR", str(_DEFAULT_HOME / "cache")),
        staging_dir=os.environ.get("OPENBLADE_STAGING_DIR", str(_DEFAULT_HOME / "staging")),
        restore_dir=os.environ.get("OPENBLADE_RESTORE_DIR", str(_DEFAULT_HOME / "restore")),
        ltfs_mount_root=os.environ.get("OPENBLADE_LTFS_MOUNT_ROOT", str(_DEFAULT_HOME / "ltfs")),
        hardware_dry_run=os.environ.get("OPENBLADE_HARDWARE_DRY_RUN", "false").lower() == "true",
        changer_device=os.environ.get("OPENBLADE_CHANGER_DEVICE") or None,
        drive_devices=drive_devices,
        drive_serial_map=parse_drive_serial_map(os.environ.get("OPENBLADE_DRIVE_SERIAL_MAP", "")),
        robotics_transport=os.environ.get("OPENBLADE_ROBOTICS_TRANSPORT", "scsi").strip().lower(),
        scalar_url=os.environ.get("OPENBLADE_SCALAR_URL") or None,
        scalar_user=os.environ.get("OPENBLADE_SCALAR_USER", "admin"),
        scalar_password=os.environ.get("OPENBLADE_SCALAR_PASSWORD", ""),
        scalar_verify_tls=_env_bool("OPENBLADE_SCALAR_VERIFY_TLS", default=True),
        emulator_urls=_load_emulator_urls(),
        emulator_latency_profile=_load_emulator_latency_profile(),
        emulator_latency_enabled=_load_emulator_latency_enabled(),
        scalar_api_only=_env_bool("OPENBLADE_SCALAR_API_ONLY", default=False),
        iblade_compat_mode=_load_iblade_compat_mode(),
    )
