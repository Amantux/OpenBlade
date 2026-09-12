from __future__ import annotations

"""Safe LTFS command helpers for real hardware."""

import hashlib
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from time import monotonic, sleep
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
from openblade.hardware.discovery import resolve_sg_device
from openblade.hardware.library import RealLibraryBackend
from openblade.hardware.runner import CommandError, SafeRunner

logger = logging.getLogger(__name__)

SAMPLE_LTFS_DEVICE_LIST = """
[2026-05-19 12:00:00] LTFS14000I Device list:
[2026-05-19 12:00:00] LTFS14001I  0: /dev/st0 (IBM ULTRIUM-TD8)
[2026-05-19 12:00:00] LTFS14001I  1: /dev/st1 (IBM ULTRIUM-TD8)
"""

# Captured verbatim from `ltfs -o device_list` (LTFS 2.4.8.4, `sg` backend)
# against the mhvtl rehearsal rig. The hand-written sample above is fictional -
# no shipping LTFS emits "LTFS14001I <n>: <dev>". Three things about the real
# behaviour break naive callers, and all three are handled by
# parse_ltfs_device_list() and LTFSCommandBackend.device_list():
#
#   1. Every line, including the device list itself, goes to STDERR.
#   2. `ltfs -o device_list` exits with status 1 even when it succeeds.
#   3. Devices are SCSI generic nodes (/dev/sgN), not /dev/stN.
SAMPLE_LTFS_DEVICE_LIST_REAL = """
10aec0 LTFS14000I LTFS starting, LTFS version 2.4.8.4 (Prelim), log level 2.
10aec0 LTFS17085I Plugin: Loading "sg" tape backend.
Tape Device list:.
Device Name = /dev/sg4 (14.0.3.0), Vendor ID = IBM     , \
Product ID = ULT3580-TD8     , Serial Number = OBLADE_D03, \
Product Name =[ULT3580-TD8].
Device Name = /dev/sg2 (14.0.2.0), Vendor ID = IBM     , \
Product ID = ULT3580-TD8     , Serial Number = OBLADE_D02, \
Product Name =[ULT3580-TD8].
Device Name = /dev/sg1 (14.0.1.0), Vendor ID = IBM     , \
Product ID = ULT3580-TD8     , Serial Number = OBLADE_D01, \
Product Name =[ULT3580-TD8].
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
# Real `ltfs -o device_list` line, e.g.
#   Device Name = /dev/sg1 (14.0.1.0), Vendor ID = IBM     , Product ID = \
#   ULT3580-TD8     , Serial Number = OBLADE_D01, Product Name =[ULT3580-TD8].
_DEVICE_REAL_RE = re.compile(
    r"Device Name\s*=\s*(?P<device>/dev/\S+)"
    r"(?:\s*\((?P<address>[^)]*)\))?"
    r"(?:.*?Vendor ID\s*=\s*(?P<vendor>[^,]*))?"
    r"(?:.*?Product ID\s*=\s*(?P<product>[^,]*))?"
    r"(?:.*?Serial Number\s*=\s*(?P<serial>[^,]*))?"
)


@dataclass(frozen=True)
class LTFSDevice:
    index: int
    device: str
    description: str
    # Serial number, when the LTFS build reports one. Correlating drives by
    # SERIAL rather than by device ordering is the documented mitigation for
    # the "wrote to the wrong drive" trap - /dev/stN, /dev/sgN and the
    # changer's Data Transfer Element order are independently assigned.
    serial: str | None = None


@dataclass
class RealTapeContents:
    barcode: str
    capacity_bytes: int = 12_000_000_000
    used_bytes: int = 0
    formatted: bool = False
    mount_state: MountState = MountState.UNMOUNTED


def parse_ltfs_device_list(output: str) -> list[LTFSDevice]:
    """Parse device-list output from LTFS tools.

    Accepts both the ``LTFS14001I <n>: <dev> (<desc>)`` shape and the
    ``Device Name = <dev> (...), Vendor ID = ..., Serial Number = ...`` shape
    that shipping LTFS (2.4.x, ``sg`` backend) actually emits. Real LTFS does
    not number its devices, so positional order supplies the index.
    """
    devices: list[LTFSDevice] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        match = _DEVICE_RE.search(line)
        if match is not None:
            devices.append(
                LTFSDevice(
                    index=int(match.group("index")),
                    device=match.group("device"),
                    description=match.group("description"),
                )
            )
            continue

        real_match = _DEVICE_REAL_RE.search(line)
        if real_match is None:
            continue
        # LTFS pads these fields to the SCSI INQUIRY widths and terminates the
        # line with a '.', so strip aggressively.
        parts = [
            (real_match.group(name) or "").strip().rstrip(".").strip()
            for name in ("vendor", "product")
        ]
        serial = (real_match.group("serial") or "").strip().rstrip(".").strip()
        devices.append(
            LTFSDevice(
                index=len(devices),
                device=real_match.group("device"),
                description=" ".join(part for part in parts if part),
                serial=serial or None,
            )
        )
    return devices


def _ltfs_processes_holding(mount_point: str) -> list[int] | None:
    """PIDs of running ``ltfs`` processes whose argv mentions ``mount_point``.

    Read straight from procfs so this needs no extra dependency and no
    subprocess. A process that exits mid-scan simply disappears.

    Returns ``None`` when procfs cannot be enumerated at all — under
    ``hidepid``, in a restricted container, or as an unprivileged uid. That is
    *unknown*, not *released*: this feeds a data-integrity gate, so "I could
    not look" must never read as "safe to yank the cartridge".
    """
    holders: list[int] = []
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return None
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            argv = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            # A single unreadable process is normal (it may have just exited,
            # or belong to another user). Only total failure is "unknown".
            continue
        if not argv or not argv[0]:
            continue
        # Best-effort argv match: a wrapper or a differently-spelled mount
        # point would be missed. It is a backstop, not a lock.
        if PurePosixPath(argv[0].decode(errors="replace")).name != "ltfs":
            continue
        if any(arg.decode(errors="replace") == mount_point for arg in argv[1:]):
            holders.append(int(entry.name))
    return holders


# Observed release on the mhvtl rig is well under a second. This bound exists
# for a wedged process, and it is deliberately not generous: callers include an
# async request handler, so every second here is a second of blocked event loop.
LTFS_RELEASE_TIMEOUT_SECONDS = 20.0


def wait_for_ltfs_release(
    mount_point: str, timeout_seconds: float = LTFS_RELEASE_TIMEOUT_SECONDS
) -> bool:
    """Block until the LTFS process for ``mount_point`` has exited.

    Returns True only if we positively observed the release. False means
    "still held, or we could not tell" — both of which mean the drive may not
    be safe to unload yet.
    """
    deadline = monotonic() + timeout_seconds
    while True:
        holders = _ltfs_processes_holding(mount_point)
        if holders is None:
            logger.warning(
                "cannot enumerate /proc to confirm LTFS released %s; "
                "treating the drive as still held",
                mount_point,
            )
            return False
        if not holders:
            return True
        if monotonic() >= deadline:
            logger.error(
                "LTFS still holds %s after %.0fs (pids %s); the drive is not "
                "safe to unload",
                mount_point,
                timeout_seconds,
                holders,
            )
            return False
        sleep(0.1)


class LTFSCommandBackend:
    """Minimal LTFS command backend with explicit safety gates."""

    @staticmethod
    def device_list(runner: SafeRunner, guard: RealHardwareGuard) -> list[LTFSDevice]:
        guard.validate()
        if runner.dry_run:
            return parse_ltfs_device_list(SAMPLE_LTFS_DEVICE_LIST)
        result = runner.run(["ltfs", "-o", "device_list"], timeout=60)
        # `ltfs -o device_list` writes its entire output - the device list
        # included - to stderr, and exits 1 even on success. Calling
        # raise_on_error() here made this method raise every single time, and
        # parsing only stdout would have found nothing anyway. Judge success by
        # whether we parsed any devices, not by the exit status.
        devices = parse_ltfs_device_list(f"{result.stdout}\n{result.stderr}")
        if not devices:
            raise CommandError(result.args, result.returncode, result.stderr)
        return devices

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
        # LTFS addresses drives through their SCSI generic node. Given
        # /dev/st0 or /dev/nst0 the sg backend reads the wrong device and
        # reports "No index found in the medium" - a wrong-device error that
        # is indistinguishable from blank media. Resolve before we format.
        device = resolve_sg_device(device)
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
        device = resolve_sg_device(device)  # see format_tape()
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
        device = resolve_sg_device(device)  # see format_tape()
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

        # `umount` returns as soon as the kernel detaches the filesystem, but
        # the LTFS FUSE process lives on for a moment to flush its index and
        # close the drive. Until it exits the drive's sg node is still held:
        # the next mount fails with "Cannot open device: failed to open
        # /dev/sgN (16)" (EBUSY), and - far worse - an unload at that moment
        # pulls the cartridge out from under an index that has not been
        # written. "Never unload while LTFS is mounted or dirty" is a project
        # non-negotiable, so the drive being FREE is what this operation
        # promises, not merely that umount exited 0.
        #
        # Judging on release rather than on the exit status also makes retries
        # work: a second call whose `umount` fails with "not mounted" still
        # reports success once LTFS has actually gone.
        released = wait_for_ltfs_release(mount_point)

        if released:
            message = "unmounted"
        elif result.success:
            message = "unmount incomplete: LTFS still holds the drive"
        else:
            message = "unmount failed"

        return OperationResult(
            released,
            message,
            {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "args": args,
                "umount_exit_ok": result.success,
                "device_released": released,
            },
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
        known_tapes: Mapping[str, tuple[int, int]] | None = None,
    ) -> None:
        self.library = library
        self.guard = guard
        self.runner = runner
        self.mount_root = mount_root
        self.capacity_bytes = capacity_bytes
        self._active_mounts: dict[str, MountHandle] = {}
        self._tapes: dict[str, RealTapeContents] = {}
        self._hydrate(known_tapes or {})

    def _hydrate(self, known_tapes: Mapping[str, tuple[int, int]]) -> None:
        """Seed ``_tapes`` from previously measured capacity/usage.

        ``_tapes`` is per-process memory populated by ``_refresh_tape_usage``
        while a tape is mounted, so before this existed every tape reported the
        fictional ``capacity_bytes`` default until this process had mounted it
        once. After any restart ``jobs/archive.py::_choose_tape`` therefore
        believed a nearly-full 6.57 GB cartridge still had 12 GB free and kept
        routing files at it instead of spilling -- the campaign recorded this as
        "a tape this process has never mounted still assumes 12 GB"
        (docs/runbooks/real-data-campaign.md §5).

        The catalog's ``cartridges`` rows are the authority: ``archive.py`` writes
        ``tape.capacity_bytes``/``used_bytes`` onto the row at the end of every
        tape's turn, and those are the values ``statvfs`` measured on the real
        medium. ``bootstrap`` reads them and passes them here.

        Nonsense rows are ignored rather than trusted: a non-positive capacity
        tells us nothing, and ``used`` is clamped into range so a stale row can
        never make a tape look more full than it is.
        """
        for barcode, (capacity_bytes, used_bytes) in known_tapes.items():
            if capacity_bytes <= 0:
                logger.debug(
                    "ignoring non-positive catalog capacity for %s: %d",
                    barcode,
                    capacity_bytes,
                )
                continue
            normalized = Barcode(barcode).value
            self._tapes[normalized] = RealTapeContents(
                barcode=normalized,
                capacity_bytes=capacity_bytes,
                used_bytes=min(max(0, used_bytes), capacity_bytes),
            )

    def ensure_tape(self, barcode: str) -> RealTapeContents:
        normalized = Barcode(barcode).value
        tape = self._tapes.get(normalized)
        if tape is None:
            # Not hydrated and not yet mounted in this process: nothing has ever
            # measured this medium, so the configured default is the only answer
            # available. Never fatal -- a brand-new scratch tape legitimately has
            # no catalog row -- but log it, because it is also what an
            # over-estimated spill decision looks like from the inside.
            logger.debug(
                "no measured capacity for %s; assuming the %d byte default",
                normalized,
                self.capacity_bytes,
            )
            tape = RealTapeContents(barcode=normalized, capacity_bytes=self.capacity_bytes)
            self._tapes[normalized] = tape
        return tape

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
        tape = self.ensure_tape(str(active_handle.barcode))
        # Take the final reading while the filesystem is still there. After the
        # unmount there is nothing left to measure.
        self._refresh_tape_usage(tape, active_handle.mount_path, mounted=True)
        result = LTFSCommandBackend.unmount(str(active_handle.mount_path), self.guard, self.runner)
        if result.success:
            self._active_mounts.pop(active_handle.handle_id, None)
            tape.mount_state = MountState.UNMOUNTED
            self.library.set_drive_mount_state(active_handle.drive_id, MountState.UNMOUNTED)
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
        self._refresh_tape_usage(tape, active_handle.mount_path, mounted=True)
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

    def _refresh_tape_usage(
        self, tape: RealTapeContents, mount_path: Path, *, mounted: bool
    ) -> None:
        """Refresh capacity and usage from the live LTFS filesystem.

        Both numbers are only observable while LTFS is actually mounted. Once it
        is gone the mount point is an ordinary empty directory on the host disk,
        so a directory walk reports zero and ``statvfs`` would report the HOST
        filesystem. The previous implementation walked the directory and ran
        *after* the unmount, which meant every tape's ``used_bytes`` was reset to
        0 at exactly the moment ``jobs/archive.py`` wrote it back to the
        cartridge row -- so OpenBlade believed every tape was empty after every
        archive job and ``_choose_tape`` could never spill to the next tape.

        ``statvfs`` rather than a directory walk: it is one syscall instead of an
        O(files) stat storm on every single write, and it reports the medium's
        real geometry. Measured on the mhvtl rig: an 8000 MB cartridge reports
        6,569,328,640 bytes after LTFS partitioning, against the 12,000,000,000
        this class assumes for a tape it has never mounted.
        """
        if not mounted:
            # Keep the last good observation rather than inventing a new one.
            return
        # `mounted` is the caller's BELIEF, not a fact, and being wrong is
        # expensive: an unmounted mount point is a plain directory on the host
        # disk, so statvfs would report the host filesystem and we would persist
        # a ~2 TB "LTO-8 cartridge" into the catalog (jobs/archive.py copies
        # capacity_bytes onto the cartridge row). Two ways to be wrong that are
        # not hypothetical:
        #   * OPENBLADE_HARDWARE_DRY_RUN=true with BackendMode.REAL -- a
        #     supported, tested config in which mount() mkdir's the mount point
        #     and LTFSCommandBackend never actually mounts anything;
        #   * the documented unmount retry, where `released=False` leaves the
        #     handle active after the filesystem is already gone.
        # So verify it really is a separate filesystem before believing it.
        if not _is_distinct_mount(mount_path):
            return
        try:
            stats = os.statvfs(mount_path)
        except OSError:
            return
        if stats.f_frsize <= 0 or stats.f_blocks <= 0:
            return
        tape.capacity_bytes = stats.f_frsize * stats.f_blocks
        tape.used_bytes = max(0, tape.capacity_bytes - stats.f_frsize * stats.f_bavail)


def _is_distinct_mount(mount_path: Path) -> bool:
    """True when ``mount_path`` is a mount point, not a directory on its parent's fs.

    The classic st_dev comparison. Fails closed: if either stat fails we report
    False, because for a check guarding what we persist as a cartridge's capacity,
    "I could not look" must not mean "believe the number".
    """
    try:
        here = os.stat(mount_path)
        parent = os.stat(mount_path.parent)
    except OSError:
        return False
    return here.st_dev != parent.st_dev


def _relative_tape_path(path: PurePosixPath | str) -> Path:
    relative = str(path).lstrip("/")
    return Path(relative)
