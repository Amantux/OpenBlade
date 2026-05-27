from __future__ import annotations

"""Safe LTFS command helpers for real hardware."""

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from uuid import uuid4

from openblade.domain.errors import FileNotFoundError as OpenBladeFileNotFoundError
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
from openblade.domain.policies import DryRunPlan, FormatConfirmation, RealHardwareGuard
from openblade.hardware.library import RealLibraryBackend
from openblade.hardware.runner import SafeRunner

SAMPLE_LTFS_DEVICE_LIST = """
[2026-05-19 12:00:00] LTFS14000I Device list:
[2026-05-19 12:00:00] LTFS14001I  0: /dev/st0 (IBM ULTRIUM-TD8)
[2026-05-19 12:00:00] LTFS14001I  1: /dev/st1 (IBM ULTRIUM-TD8)
"""

SAMPLE_LTFS_FORMAT_DRY_RUN = """
Tape barcode: PHO001L8
Tape capacity: 12000000000 bytes
WORM: No
Format would write: LTFS label, index partition, data partition
"""

_DEVICE_RE = re.compile(
    r"LTFS14001I\s+(?P<index>\d+):\s+(?P<device>/dev/\S+)\s+\((?P<description>.+)\)"
)


@dataclass(frozen=True)
class LTFSDevice:
    index: int
    device: str
    description: str


@dataclass
class RealTapeContents:
    barcode: str
    capacity_bytes: int = 12_000_000_000
    used_bytes: int = 0
    formatted: bool = False
    mount_state: MountState = MountState.UNMOUNTED


def parse_ltfs_device_list(output: str) -> list[LTFSDevice]:
    """Parse device-list output from LTFS tools."""
    devices: list[LTFSDevice] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _DEVICE_RE.search(line)
        if match is None:
            continue
        devices.append(
            LTFSDevice(
                index=int(match.group("index")),
                device=match.group("device"),
                description=match.group("description"),
            )
        )
    return devices


class LTFSCommandBackend:
    """Minimal LTFS command backend with explicit safety gates."""

    @staticmethod
    def device_list(runner: SafeRunner, guard: RealHardwareGuard) -> list[LTFSDevice]:
        guard.validate()
        if runner.dry_run:
            return parse_ltfs_device_list(SAMPLE_LTFS_DEVICE_LIST)
        result = runner.run(["ltfs", "-o", "device_list"], timeout=60)
        result.raise_on_error()
        return parse_ltfs_device_list(result.stdout)

    @staticmethod
    def format_dry_run_plan(barcode: str, device: str) -> DryRunPlan:
        return DryRunPlan(
            operation="format",
            target=f"format {barcode} on {device}",
            affected_barcodes=[barcode],
            warnings=[
                "Destructive operation.",
                "Writes LTFS metadata and rewrites tape structure.",
                SAMPLE_LTFS_FORMAT_DRY_RUN.strip(),
            ],
            is_destructive=True,
            estimated_duration_seconds=120,
        )

    @staticmethod
    def format_tape(
        barcode: str,
        device: str,
        confirmation: FormatConfirmation,
        guard: RealHardwareGuard,
        runner: SafeRunner,
    ) -> OperationResult:
        guard.validate()
        confirmation.validate(barcode)
        args = ["mkltfs", "-d", device, "-n", barcode, "--force"]
        if runner.dry_run:
            return OperationResult(
                True,
                "dry-run format",
                {"barcode": barcode, "device": device, "args": args},
            )
        result = runner.run(args, timeout=300)
        return OperationResult(
            result.success,
            "formatted" if result.success else "format failed",
            {"stdout": result.stdout, "stderr": result.stderr, "args": args},
        )

    @staticmethod
    def mount_readonly(
        device: str,
        mount_point: str,
        guard: RealHardwareGuard,
        runner: SafeRunner,
    ) -> OperationResult:
        guard.validate()
        args = ["ltfs", mount_point, "-o", f"devname={device},ro"]
        if runner.dry_run:
            return OperationResult(
                True,
                "dry-run mount readonly",
                {"device": device, "mount_point": mount_point, "args": args},
            )
        result = runner.run(args, timeout=300)
        return OperationResult(
            result.success,
            "mounted readonly" if result.success else "readonly mount failed",
            {"stdout": result.stdout, "stderr": result.stderr, "args": args},
        )

    @staticmethod
    def mount_readwrite(
        device: str,
        mount_point: str,
        guard: RealHardwareGuard,
        runner: SafeRunner,
    ) -> OperationResult:
        guard.validate()
        args = ["ltfs", mount_point, "-o", f"devname={device},rw"]
        if runner.dry_run:
            return OperationResult(
                True,
                "dry-run mount readwrite",
                {"device": device, "mount_point": mount_point, "args": args},
            )
        result = runner.run(args, timeout=300)
        return OperationResult(
            result.success,
            "mounted readwrite" if result.success else "readwrite mount failed",
            {"stdout": result.stdout, "stderr": result.stderr, "args": args},
        )

    @staticmethod
    def unmount(
        mount_point: str,
        guard: RealHardwareGuard,
        runner: SafeRunner,
    ) -> OperationResult:
        guard.validate()
        args = ["umount", mount_point]
        if runner.dry_run:
            return OperationResult(
                True,
                "dry-run unmount",
                {"mount_point": mount_point, "args": args},
            )
        result = runner.run(args, timeout=120)
        return OperationResult(
            result.success,
            "unmounted" if result.success else "unmount failed",
            {"stdout": result.stdout, "stderr": result.stderr, "args": args},
        )


class RealLTFSBackend:
    """Guarded LTFS backend that mounts real media through LTFS CLI tools."""

    def __init__(
        self,
        *,
        library: RealLibraryBackend,
        guard: RealHardwareGuard,
        runner: SafeRunner,
        mount_root: Path,
        capacity_bytes: int = 12_000_000_000,
    ) -> None:
        self.library = library
        self.guard = guard
        self.runner = runner
        self.mount_root = mount_root
        self.capacity_bytes = capacity_bytes
        self._active_mounts: dict[str, MountHandle] = {}
        self._tapes: dict[str, RealTapeContents] = {}

    def ensure_tape(self, barcode: str) -> RealTapeContents:
        normalized = Barcode(barcode).value
        return self._tapes.setdefault(
            normalized,
            RealTapeContents(barcode=normalized, capacity_bytes=self.capacity_bytes),
        )

    def remaining_capacity(self, barcode: str) -> int:
        tape = self.ensure_tape(barcode)
        return max(0, tape.capacity_bytes - tape.used_bytes)

    def format(self, barcode: str, confirmation: FormatConfirmation) -> OperationResult:
        device = self._drive_device_for_barcode(barcode)
        result = LTFSCommandBackend.format_tape(
            barcode,
            device,
            confirmation,
            self.guard,
            self.runner,
        )
        if result.success:
            tape = self.ensure_tape(barcode)
            tape.used_bytes = 0
            tape.formatted = True
            tape.mount_state = MountState.UNMOUNTED
        return result

    def mount(self, barcode: str, mode: MountMode) -> MountHandle:
        drive_id = self.library.find_drive_by_barcode(barcode)
        if drive_id is None:
            raise ValueError(f"Barcode {barcode} is not loaded in a drive")
        mount_path = self.mount_root / Barcode(barcode).value
        mount_path.mkdir(parents=True, exist_ok=True)
        device = self.library.drive_device(drive_id)
        if mode == MountMode.READ_ONLY:
            result = LTFSCommandBackend.mount_readonly(device, str(mount_path), self.guard, self.runner)
            target_state = MountState.MOUNTED_RO
        else:
            result = LTFSCommandBackend.mount_readwrite(device, str(mount_path), self.guard, self.runner)
            target_state = MountState.MOUNTED_RW
        if not result.success:
            raise RuntimeError(result.message)
        handle = MountHandle(
            handle_id=str(uuid4()),
            barcode=Barcode(barcode),
            drive_id=drive_id,
            mode=mode,
            mount_path=mount_path,
        )
        self._active_mounts[handle.handle_id] = handle
        tape = self.ensure_tape(barcode)
        tape.formatted = True
        tape.mount_state = target_state
        self.library.set_drive_mount_state(drive_id, target_state)
        return handle

    def unmount(self, handle: MountHandle) -> OperationResult:
        active_handle = self._require_active_mount(handle)
        result = LTFSCommandBackend.unmount(str(active_handle.mount_path), self.guard, self.runner)
        if result.success:
            self._active_mounts.pop(active_handle.handle_id, None)
            tape = self.ensure_tape(str(active_handle.barcode))
            tape.mount_state = MountState.UNMOUNTED
            self.library.set_drive_mount_state(active_handle.drive_id, MountState.UNMOUNTED)
            self._refresh_tape_usage(tape, active_handle.mount_path)
        return result

    def write_file(self, handle: MountHandle, source: Path, dest: PurePosixPath) -> FileInstance:
        return self.write_bytes(handle, dest, source.read_bytes())

    def write_bytes(
        self,
        handle: MountHandle,
        dest: PurePosixPath | str,
        content: bytes,
        *,
        size_bytes: int | None = None,
        checksum_sha256: str | None = None,
    ) -> FileInstance:
        active_handle = self._require_active_mount(handle)
        if active_handle.mode != MountMode.READ_WRITE:
            raise PermissionError("Cannot write to a read-only LTFS mount")
        target = active_handle.mount_path / _relative_tape_path(dest)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        tape = self.ensure_tape(str(active_handle.barcode))
        tape.formatted = True
        tape.mount_state = MountState.MOUNTED_RW
        self._refresh_tape_usage(tape, active_handle.mount_path)
        checksum = checksum_sha256 or hashlib.sha256(content).hexdigest()
        return FileInstance(
            file_record_id=checksum,
            barcode=Barcode(str(active_handle.barcode)),
            tape_path=PurePosixPath(str(dest)),
            state=FileInstanceState.ARCHIVED,
            archived_at=datetime.now(timezone.utc),
        )

    def read_bytes(self, barcode_or_path: str, path: PurePosixPath | str | None = None) -> bytes | None:
        if path is None:
            target = PurePosixPath(str(barcode_or_path))
            for handle in self._active_mounts.values():
                candidate = handle.mount_path / _relative_tape_path(target)
                if candidate.exists():
                    return candidate.read_bytes()
            return None
        handle = self.mount(str(barcode_or_path), MountMode.READ_ONLY)
        try:
            target = handle.mount_path / _relative_tape_path(path)
            if not target.exists():
                return None
            return target.read_bytes()
        finally:
            self.unmount(handle)

    def read_file(self, handle: MountHandle, source: PurePosixPath, dest: Path) -> OperationResult:
        active_handle = self._require_active_mount(handle)
        origin = active_handle.mount_path / _relative_tape_path(source)
        if not origin.exists():
            raise OpenBladeFileNotFoundError(f"Tape path {source} not found")
        payload = origin.read_bytes()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
        return OperationResult(
            True,
            "read",
            {"path": str(source), "checksum": hashlib.sha256(payload).hexdigest()},
        )

    def stat(self, handle: MountHandle, path: PurePosixPath) -> LTFSFileStat:
        active_handle = self._require_active_mount(handle)
        target = active_handle.mount_path / _relative_tape_path(path)
        if not target.exists():
            raise OpenBladeFileNotFoundError(f"Tape path {path} not found")
        payload = target.read_bytes()
        return LTFSFileStat(
            path=path,
            size_bytes=target.stat().st_size,
            checksum_sha256=hashlib.sha256(payload).hexdigest(),
            modified_at=datetime.fromtimestamp(target.stat().st_mtime, tz=timezone.utc),
        )

    def _drive_device_for_barcode(self, barcode: str) -> str:
        drive_id = self.library.find_drive_by_barcode(barcode)
        if drive_id is None:
            raise ValueError(f"Barcode {barcode} is not loaded in a drive")
        return self.library.drive_device(drive_id)

    def _require_active_mount(self, handle: MountHandle) -> MountHandle:
        active_handle = self._active_mounts.get(handle.handle_id)
        if active_handle is None:
            raise ValueError(f"Mount handle {handle.handle_id} is not active")
        return active_handle

    def _refresh_tape_usage(self, tape: RealTapeContents, mount_path: Path) -> None:
        if not mount_path.exists():
            tape.used_bytes = 0
            return
        tape.used_bytes = sum(path.stat().st_size for path in mount_path.rglob("*") if path.is_file())


def _relative_tape_path(path: PurePosixPath | str) -> Path:
    relative = str(path).lstrip("/")
    return Path(relative)
