from __future__ import annotations

import hashlib
import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import structlog

from openblade.domain.errors import (
    CartridgeNotFoundError,
    FormatRequiresConfirmationError,
    SimulatedMountFailure,
    SimulatedWriteFailure,
    TapeFullError,
)
from openblade.domain.errors import (
    FileNotFoundError as OpenBladeFileNotFoundError,
)
from openblade.domain.models import (
    Barcode,
    FileInstance,
    FileInstanceState,
    LTFSFileStat,
    MountHandle,
    MountMode,
    MountState,
    OperationResult,
)
from openblade.domain.policies import FormatConfirmation
from openblade.simulator.fault_injection import (
    FaultInjector,
    FaultType as InjectedFaultType,
    SimulatorFaultError,
)
from openblade.simulator.faults import FaultConfig, FaultType
from openblade.simulator.library import MockLibraryBackend

logger = structlog.get_logger(__name__)


@dataclass
class MockFileRecord:
    tape_path: str
    size_bytes: int
    checksum_sha256: str
    content: bytes
    modified_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_json(self) -> dict[str, Any]:
        return {
            "tape_path": self.tape_path,
            "size_bytes": self.size_bytes,
            "checksum_sha256": self.checksum_sha256,
            "content_hex": self.content.hex(),
            "modified_at": self.modified_at.isoformat(),
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> MockFileRecord:
        return cls(
            tape_path=str(payload["tape_path"]),
            size_bytes=int(payload["size_bytes"]),
            checksum_sha256=str(payload["checksum_sha256"]),
            content=bytes.fromhex(str(payload["content_hex"])),
            modified_at=datetime.fromisoformat(str(payload["modified_at"])),
        )


@dataclass
class MockTapeContents:
    barcode: str
    capacity_bytes: int = 12_000_000_000
    used_bytes: int = 0
    files: dict[str, MockFileRecord] = field(default_factory=dict)
    formatted: bool = False
    mount_state: MountState = MountState.UNMOUNTED

    def to_json(self) -> dict[str, Any]:
        return {
            "barcode": self.barcode,
            "capacity_bytes": self.capacity_bytes,
            "used_bytes": self.used_bytes,
            "formatted": self.formatted,
            "mount_state": self.mount_state.value,
            "files": {path: record.to_json() for path, record in self.files.items()},
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> MockTapeContents:
        return cls(
            barcode=str(payload["barcode"]),
            capacity_bytes=int(payload["capacity_bytes"]),
            used_bytes=int(payload.get("used_bytes", 0)),
            files={
                path: MockFileRecord.from_json(record)
                for path, record in dict(payload.get("files", {})).items()
            },
            formatted=bool(payload.get("formatted", False)),
            mount_state=MountState(str(payload.get("mount_state", MountState.UNMOUNTED.value))),
        )


@dataclass
class _ActiveMount:
    handle: MountHandle
    barcode: str
    drive_id: int
    mode: MountMode
    active: bool = True
    dirty: bool = False
    write_in_progress: bool = False


class MockLTFSBackend:
    def __init__(
        self,
        library: MockLibraryBackend,
        capacity_bytes: int = 12_000_000_000,
        fault_config: FaultConfig | None = None,
        fault_injector: FaultInjector | None = None,
    ) -> None:
        self.library = library
        self.capacity_bytes = capacity_bytes
        self.fault_config = fault_config or FaultConfig.no_faults()
        self._fault_injector = fault_injector
        self._lock = threading.RLock()
        self._tapes: dict[str, MockTapeContents] = {}
        self._active_mounts: dict[str, _ActiveMount] = {}
        for barcode in library.get_all_barcodes():
            self._tapes[barcode] = MockTapeContents(barcode=barcode, capacity_bytes=capacity_bytes)

    def ensure_tape(self, barcode: str) -> MockTapeContents:
        normalized = Barcode(barcode).value
        self._maybe_raise_injected_fault(InjectedFaultType.TAPE_NOT_FOUND, normalized)
        with self._lock:
            if normalized not in self._tapes:
                if normalized not in self.library.get_all_barcodes():
                    raise CartridgeNotFoundError(f"Unknown cartridge {barcode}")
                self._tapes[normalized] = MockTapeContents(
                    barcode=normalized,
                    capacity_bytes=self.capacity_bytes,
                )
            return self._tapes[normalized]

    def to_json(self) -> dict[str, Any]:
        with self._lock:
            return {
                "capacity_bytes": self.capacity_bytes,
                "fault_config": self.fault_config.to_json(),
                "tapes": {barcode: tape.to_json() for barcode, tape in self._tapes.items()},
            }

    @classmethod
    def from_json(
        cls,
        library: MockLibraryBackend,
        payload: dict[str, Any] | str,
    ) -> MockLTFSBackend:
        raw = json.loads(payload) if isinstance(payload, str) else payload
        backend = cls(
            library,
            capacity_bytes=int(raw.get("capacity_bytes", 12_000_000_000)),
            fault_config=FaultConfig.from_json(raw.get("fault_config")),
        )
        with backend._lock:
            backend._tapes = {
                barcode: MockTapeContents.from_json(tape)
                for barcode, tape in dict(raw.get("tapes", {})).items()
            }
            for barcode in library.get_all_barcodes():
                backend._tapes.setdefault(
                    barcode,
                    MockTapeContents(barcode=barcode, capacity_bytes=backend.capacity_bytes),
                )
            backend._active_mounts = {}
        return backend

    def format(
        self,
        barcode: str,
        confirmation: FormatConfirmation | None = None,
    ) -> OperationResult:
        if not isinstance(confirmation, FormatConfirmation):
            raise FormatRequiresConfirmationError(
                "Formatting requires an explicit FormatConfirmation"
            )
        confirmation.validate(barcode)
        tape = self.ensure_tape(barcode)
        with self._lock:
            tape.files.clear()
            tape.used_bytes = 0
            tape.formatted = True
            tape.mount_state = MountState.UNMOUNTED
            self._active_mounts = {
                handle_id: mount
                for handle_id, mount in self._active_mounts.items()
                if mount.barcode != tape.barcode
            }
        drive_id = self.library.find_drive_by_barcode(barcode)
        if drive_id is not None:
            self.library.set_drive_mount_state(drive_id, MountState.UNMOUNTED)
        logger.info("formatted tape", barcode=barcode)
        return OperationResult(True, "formatted", {"barcode": tape.barcode})

    def mount(self, barcode: str, mode: MountMode) -> MountHandle:
        normalized = Barcode(barcode).value
        self._maybe_raise_injected_fault(InjectedFaultType.MOUNT_TIMEOUT, normalized)
        self._maybe_raise_injected_fault(InjectedFaultType.DRIVE_UNAVAILABLE, normalized)
        if self.fault_config.should_fail(FaultType.MOUNT_FAILURE):
            raise SimulatedMountFailure("Injected mount failure")
        drive_id = self.library.find_drive_by_barcode(barcode)
        if drive_id is None:
            raise CartridgeNotFoundError(f"Cartridge {barcode} is not loaded in a drive")
        tape = self.ensure_tape(barcode)
        with self._lock:
            if not tape.formatted:
                raise FormatRequiresConfirmationError(f"Tape {barcode} must be formatted first")
            active_for_tape = [
                mount for mount in self._active_mounts.values() if mount.barcode == barcode
            ]
            if active_for_tape and (
                mode == MountMode.READ_WRITE
                or any(mount.mode == MountMode.READ_WRITE for mount in active_for_tape)
            ):
                raise SimulatedMountFailure(f"Tape {barcode} is already mounted")
            target_state = (
                MountState.MOUNTED_RO if mode == MountMode.READ_ONLY else MountState.MOUNTED_RW
            )
            if not active_for_tape:
                self.library.set_drive_mount_state(drive_id, target_state)
                tape.mount_state = target_state
            handle = MountHandle(
                handle_id=str(uuid.uuid4()),
                barcode=Barcode(barcode),
                drive_id=drive_id,
                mode=mode,
                mount_path=Path("mock-mount") / barcode,
            )
            self._active_mounts[handle.handle_id] = _ActiveMount(
                handle=handle,
                barcode=tape.barcode,
                drive_id=drive_id,
                mode=mode,
            )
            return handle

    def unmount(self, handle: MountHandle) -> OperationResult:
        with self._lock:
            active_mount = self._active_mounts.get(handle.handle_id)
            if active_mount is None or not active_mount.active:
                raise CartridgeNotFoundError(f"Mount handle {handle.handle_id} is not active")
            active_mount.active = False
            self._active_mounts.pop(handle.handle_id)
            tape = self.ensure_tape(str(handle.barcode))
            remaining = [
                mount for mount in self._active_mounts.values() if mount.barcode == tape.barcode
            ]
            if active_mount.write_in_progress or active_mount.dirty:
                tape.mount_state = MountState.DIRTY
            elif any(mount.mode == MountMode.READ_WRITE for mount in remaining):
                tape.mount_state = MountState.MOUNTED_RW
            elif remaining:
                tape.mount_state = MountState.MOUNTED_RO
            else:
                tape.mount_state = MountState.UNMOUNTED
        self.library.set_drive_mount_state(handle.drive_id, tape.mount_state)
        return OperationResult(
            True,
            "unmounted",
            {"barcode": str(handle.barcode), "drive": handle.drive_id},
        )

    def write_file(self, handle: MountHandle, source: Path, dest: PurePosixPath) -> FileInstance:
        return self.write_bytes(handle, dest, source.read_bytes())

    def write_bytes(
        self,
        handle_or_barcode: MountHandle | str,
        dest: PurePosixPath | str,
        content: bytes,
        *,
        size_bytes: int | None = None,
        checksum_sha256: str | None = None,
    ) -> FileInstance | None:
        if isinstance(handle_or_barcode, MountHandle):
            return self._write_mounted_bytes(
                handle_or_barcode,
                dest,
                content,
                size_bytes=size_bytes,
                checksum_sha256=checksum_sha256,
            )
        self._write_metadata_bytes(str(handle_or_barcode), str(dest), content)
        return None

    def _write_mounted_bytes(
        self,
        handle: MountHandle,
        dest: PurePosixPath | str,
        content: bytes,
        *,
        size_bytes: int | None = None,
        checksum_sha256: str | None = None,
    ) -> FileInstance:
        self._maybe_raise_injected_fault(InjectedFaultType.WRITE_ERROR, str(handle.barcode))
        self._maybe_raise_injected_fault(InjectedFaultType.PARTIAL_WRITE, str(handle.barcode))
        active_mount = self._require_active_mount(handle)
        if handle.mode != MountMode.READ_WRITE:
            raise PermissionError("Cannot write to a read-only mount")
        tape = self.ensure_tape(str(handle.barcode))
        path_key = str(dest)
        stored_size = len(content) if size_bytes is None else size_bytes
        with self._lock:
            active_mount.write_in_progress = True
            if self.fault_config.should_fail(FaultType.NO_FREE_SPACE):
                active_mount.write_in_progress = False
                raise TapeFullError(f"Tape {tape.barcode} is out of space")
            if self.fault_config.should_fail(FaultType.WRITE_FAILURE):
                active_mount.write_in_progress = False
                active_mount.dirty = True
                raise SimulatedWriteFailure("Injected write failure")
            if self.fault_config.write_fail_after_bytes is not None:
                failure_point = self.fault_config.write_fail_after_bytes
                if failure_point < len(content):
                    active_mount.write_in_progress = False
                    active_mount.dirty = True
                    raise SimulatedWriteFailure(
                        f"Injected partial write failure after {failure_point} bytes"
                    )
            existing = tape.files.get(path_key)
            previous_size = existing.size_bytes if existing is not None else 0
            projected_used_bytes = tape.used_bytes - previous_size + stored_size
            if projected_used_bytes > tape.capacity_bytes:
                active_mount.write_in_progress = False
                raise TapeFullError(f"Tape {tape.barcode} is out of space")
            checksum = checksum_sha256 or hashlib.sha256(content).hexdigest()
            tape.files[path_key] = MockFileRecord(
                tape_path=path_key,
                size_bytes=stored_size,
                checksum_sha256=checksum,
                content=content,
            )
            tape.used_bytes = projected_used_bytes
            tape.mount_state = MountState.MOUNTED_RW
            active_mount.write_in_progress = False
        self.library.set_drive_mount_state(handle.drive_id, MountState.MOUNTED_RW)
        return FileInstance(
            file_record_id=checksum,
            barcode=Barcode(str(handle.barcode)),
            tape_path=PurePosixPath(path_key),
            state=FileInstanceState.ARCHIVED,
            archived_at=datetime.now(timezone.utc),
        )

    def _write_metadata_bytes(self, barcode: str, dest: str, content: bytes) -> None:
        self._maybe_raise_injected_fault(InjectedFaultType.WRITE_ERROR, Barcode(barcode).value)
        self._maybe_raise_injected_fault(InjectedFaultType.PARTIAL_WRITE, Barcode(barcode).value)
        tape = self.ensure_tape(barcode)
        path_key = str(dest)
        stored_size = len(content)
        with self._lock:
            existing = tape.files.get(path_key)
            previous_size = existing.size_bytes if existing is not None else 0
            projected_used_bytes = tape.used_bytes - previous_size + stored_size
            if projected_used_bytes > tape.capacity_bytes:
                raise TapeFullError(f"Tape {tape.barcode} is out of space")
            tape.files[path_key] = MockFileRecord(
                tape_path=path_key,
                size_bytes=stored_size,
                checksum_sha256=hashlib.sha256(content).hexdigest(),
                content=content,
            )
            tape.used_bytes = projected_used_bytes

    def read_bytes(self, barcode_or_path: str, path: PurePosixPath | str | None = None) -> bytes | None:
        if path is None:
            self._maybe_raise_injected_fault(InjectedFaultType.READ_ERROR, "")
            target_path = str(barcode_or_path)
            with self._lock:
                for tape in self._tapes.values():
                    record = tape.files.get(target_path)
                    if record is not None:
                        return record.content
            return None
        self._maybe_raise_injected_fault(InjectedFaultType.READ_ERROR, Barcode(str(barcode_or_path)).value)
        tape = self.ensure_tape(str(barcode_or_path))
        with self._lock:
            record = tape.files.get(str(path))
            return None if record is None else record.content

    def read_file(self, handle: MountHandle, source: PurePosixPath, dest: Path) -> OperationResult:
        self._maybe_raise_injected_fault(InjectedFaultType.READ_ERROR, str(handle.barcode))
        self._require_active_mount(handle)
        tape = self.ensure_tape(str(handle.barcode))
        with self._lock:
            record = tape.files.get(str(source))
            if record is None:
                raise OpenBladeFileNotFoundError(f"Tape path {source} not found")
            payload = record.content
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
        checksum = hashlib.sha256(payload).hexdigest()
        if self._should_flip_checksum(str(handle.barcode)):
            checksum = self._flip_checksum(checksum)
        return OperationResult(True, "read", {"path": str(source), "checksum": checksum})

    def stat(self, handle: MountHandle, path: PurePosixPath) -> LTFSFileStat:
        self._require_active_mount(handle)
        tape = self.ensure_tape(str(handle.barcode))
        with self._lock:
            record = tape.files.get(str(path))
            if record is None:
                raise OpenBladeFileNotFoundError(f"Tape path {path} not found")
            checksum = record.checksum_sha256
            if self._should_flip_checksum(str(handle.barcode)):
                checksum = self._flip_checksum(checksum)
            return LTFSFileStat(
                path=path,
                size_bytes=record.size_bytes,
                checksum_sha256=checksum,
                modified_at=record.modified_at,
            )

    def remaining_capacity(self, barcode: str) -> int:
        tape = self.ensure_tape(barcode)
        return max(0, tape.capacity_bytes - tape.used_bytes)

    def _require_active_mount(self, handle: MountHandle) -> _ActiveMount:
        with self._lock:
            active_mount = self._active_mounts.get(handle.handle_id)
            if active_mount is None or not active_mount.active:
                raise CartridgeNotFoundError(f"Mount handle {handle.handle_id} is not active")
            return active_mount

    def _maybe_raise_injected_fault(self, fault_type: InjectedFaultType, target: str) -> None:
        if self._fault_injector is None:
            return
        if self._fault_injector.should_fault(fault_type, target):
            raise SimulatorFaultError(self._fault_injector.get_error_message(fault_type, target))

    def _should_flip_checksum(self, target: str) -> bool:
        if self.fault_config.should_fail(FaultType.CHECKSUM_MISMATCH):
            return True
        if self._fault_injector is None:
            return False
        return self._fault_injector.should_fault(InjectedFaultType.CHECKSUM_MISMATCH, target)

    @staticmethod
    def _flip_checksum(checksum: str) -> str:
        if not checksum:
            return checksum
        return ("0" if checksum[0] != "0" else "1") + checksum[1:]
