"""OpenBlade configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class BackendMode(str, Enum):
    MOCK = "mock"
    REAL = "real"


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
    drive_devices: tuple[str, ...] = ()
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
        robotics_transport=os.environ.get("OPENBLADE_ROBOTICS_TRANSPORT", "scsi").strip().lower(),
        scalar_url=os.environ.get("OPENBLADE_SCALAR_URL") or None,
        scalar_user=os.environ.get("OPENBLADE_SCALAR_USER", "admin"),
        scalar_password=os.environ.get("OPENBLADE_SCALAR_PASSWORD", ""),
        scalar_verify_tls=_env_bool("OPENBLADE_SCALAR_VERIFY_TLS", default=True),
        emulator_urls=_load_emulator_urls(),
        emulator_latency_profile=_load_emulator_latency_profile(),
        emulator_latency_enabled=_load_emulator_latency_enabled(),
        scalar_api_only=_env_bool("OPENBLADE_SCALAR_API_ONLY", default=False),
    )
