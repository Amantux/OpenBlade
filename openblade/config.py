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
    job_timeout_seconds: int = 3600
    changer_timeout_seconds: int = 60
    drive_timeout_seconds: int = 300


def load_config() -> OpenBladeConfig:
    backend_str = os.environ.get("OPENBLADE_BACKEND", "mock").lower()
    try:
        backend = BackendMode(backend_str)
    except ValueError:
        backend = BackendMode.MOCK

    real_hw = os.environ.get("OPENBLADE_REAL_HARDWARE_ENABLED", "false").lower() == "true"

    return OpenBladeConfig(
        backend=backend,
        real_hardware_enabled=real_hw,
        db_url=os.environ.get("OPENBLADE_DB_URL", f"sqlite:///{_DEFAULT_HOME / 'openblade.db'}"),
        log_level=os.environ.get("OPENBLADE_LOG_LEVEL", "INFO"),
        cache_dir=os.environ.get("OPENBLADE_CACHE_DIR", str(_DEFAULT_HOME / "cache")),
        staging_dir=os.environ.get("OPENBLADE_STAGING_DIR", str(_DEFAULT_HOME / "staging")),
        restore_dir=os.environ.get("OPENBLADE_RESTORE_DIR", str(_DEFAULT_HOME / "restore")),
    )
