from __future__ import annotations

"""Pydantic-friendly domain models for OpenBlade."""

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any


class CartridgeState(str, Enum):
    IN_SLOT = "in_slot"
    IN_DRIVE = "in_drive"
    EXPORTED = "exported"
    MISSING = "missing"
    CLEANING = "cleaning"


class DriveState(str, Enum):
    EMPTY = "empty"
    LOADED = "loaded"
    BUSY = "busy"
    FAILED = "failed"


class MountState(str, Enum):
    UNMOUNTED = "unmounted"
    MOUNTED_RO = "mounted_ro"
    MOUNTED_RW = "mounted_rw"
    DIRTY = "dirty"


class ChangerState(str, Enum):
    IDLE = "idle"
    MOVING = "moving"
    ERROR = "error"


class JobState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    FAILED_RECOVERABLE = "failed_recoverable"
    CANCELLED = "cancelled"


class JobType(str, Enum):
    ARCHIVE = "archive"
    RESTORE = "restore"
    FORMAT = "format"
    VERIFY = "verify"
    INVENTORY = "inventory"
    IMPORT = "import"
    EXPORT = "export"


class MountMode(str, Enum):
    READ_ONLY = "read_only"
    READ_WRITE = "read_write"


class FileInstanceState(str, Enum):
    PENDING = "pending"
    ARCHIVED = "archived"
    VERIFIED = "verified"
    FAILED = "failed"
    QUARANTINED = "quarantined"


_BARCODE_RE = re.compile(r"^[A-Z0-9]{8}$")


@dataclass(frozen=True)
class Barcode:
    value: str

    def __post_init__(self) -> None:
        normalized = self.value.upper()
        if not _BARCODE_RE.fullmatch(normalized):
            raise ValueError("Barcode must be exactly 8 alphanumeric characters")
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class SlotState:
    slot_id: int
    barcode: Barcode | None = None
    occupied: bool = False

    def __post_init__(self) -> None:
        if self.occupied != (self.barcode is not None):
            raise ValueError("occupied must match whether barcode is present")


@dataclass(frozen=True)
class DriveStatus:
    drive_id: int
    barcode: Barcode | None
    drive_state: DriveState
    mount_state: MountState


@dataclass(frozen=True)
class LibraryInventory:
    library_id: str
    slots: list[SlotState]
    drives: list[DriveStatus]
    changer_state: ChangerState


@dataclass(frozen=True)
class VolumeGroup:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "default"
    barcodes: list[Barcode] = field(default_factory=list)


@dataclass(frozen=True)
class FileRecord:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    path: PurePosixPath = PurePosixPath("/")
    size_bytes: int = 0
    checksum_sha256: str = ""
    volume_group_id: str = ""


@dataclass(frozen=True)
class FileInstance:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    file_record_id: str = ""
    barcode: Barcode = field(default_factory=lambda: Barcode("UNSET001"))
    tape_path: PurePosixPath = PurePosixPath("/")
    state: FileInstanceState = FileInstanceState.PENDING
    archived_at: datetime | None = None


@dataclass(frozen=True)
class Job:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    job_type: JobType = JobType.INVENTORY
    state: JobState = JobState.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LTFSFileStat:
    path: PurePosixPath
    size_bytes: int
    checksum_sha256: str
    modified_at: datetime


@dataclass(frozen=True)
class MountHandle:
    handle_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    barcode: Barcode = field(default_factory=lambda: Barcode("UNSET001"))
    drive_id: int = 0
    mode: MountMode = MountMode.READ_ONLY
    mount_path: Path = Path(".")


@dataclass(frozen=True)
class OperationResult:
    success: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)
