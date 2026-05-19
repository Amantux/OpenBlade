from __future__ import annotations

"""Safe wrapper around the mtx tape library changer tool."""

import logging
import re
from dataclasses import dataclass

from openblade.domain.models import OperationResult
from openblade.domain.policies import RealHardwareGuard
from openblade.hardware.runner import SafeRunner

logger = logging.getLogger(__name__)

SAMPLE_MTX_EMPTY = """
Storage Changer /dev/sg0:1 Drives, 20 Slots ( 0 Import/Export )
Data Transfer Element 0:Empty
      Storage Element 1:Empty
      Storage Element 2:Empty
"""

SAMPLE_MTX_LOADED = """
Storage Changer /dev/sg0:2 Drives, 20 Slots ( 0 Import/Export )
Data Transfer Element 0:Full (Storage Element 1 Loaded):VolumeTag=PHO001L8
Data Transfer Element 1:Empty
      Storage Element 1:Empty
      Storage Element 2:Full :VolumeTag=PHO002L8
      Storage Element 3:Full :VolumeTag=PHO003L8
      Storage Element 4:Empty
"""

SAMPLE_MTX_CLEANING = """
Storage Changer /dev/sg0:1 Drives, 20 Slots ( 0 Import/Export )
Data Transfer Element 0:Empty
      Storage Element 1:Full :VolumeTag=CLN001L1
      Storage Element 2:Full :VolumeTag=PHO001L8
"""

SAMPLE_MTX_BARCODE_MISSING = """
Storage Changer /dev/sg0:1 Drives, 4 Slots ( 0 Import/Export )
Data Transfer Element 0:Empty
      Storage Element 1:Full
      Storage Element 2:Empty
"""

SAMPLE_MTX_TIMEOUT_STDERR = "SCSI error: Request Timeout"

_HEADER_RE = re.compile(
    r"^Storage Changer (?P<device>\S+):(?P<drive_count>\d+) Drives, "
    r"(?P<slot_count>\d+) Slots"
)
_DRIVE_RE = re.compile(r"^Data Transfer Element (?P<drive_id>\d+):(?P<details>.+)$")
_SLOT_RE = re.compile(r"^Storage Element (?P<slot_id>\d+):(?P<details>.+)$")
_LOADED_FROM_RE = re.compile(r"Storage Element (?P<slot_id>\d+) Loaded")
_BARCODE_RE = re.compile(r"VolumeTag=(?P<barcode>[^\s]+)")


@dataclass(frozen=True)
class MtxSlotInfo:
    slot_id: int
    occupied: bool
    barcode: str | None

    @property
    def is_cleaning(self) -> bool:
        return bool(self.barcode and self.barcode.startswith("CLN"))


@dataclass(frozen=True)
class MtxDriveInfo:
    drive_id: int
    loaded: bool
    barcode: str | None
    source_slot: int | None = None

    @property
    def is_cleaning(self) -> bool:
        return bool(self.barcode and self.barcode.startswith("CLN"))


@dataclass(frozen=True)
class MtxStatus:
    device: str
    drives: list[MtxDriveInfo]
    slots: list[MtxSlotInfo]
    drive_count: int = 0
    slot_count: int = 0


def _parse_barcode(details: str) -> str | None:
    match = _BARCODE_RE.search(details)
    if match is None:
        return None
    return match.group("barcode").strip()


def parse_mtx_status(output: str) -> MtxStatus:
    """Parse output from `mtx -f <device> status`."""
    device = "unknown"
    drive_count = 0
    slot_count = 0
    drives: list[MtxDriveInfo] = []
    slots: list[MtxSlotInfo] = []

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        header_match = _HEADER_RE.match(line)
        if header_match is not None:
            device = header_match.group("device")
            drive_count = int(header_match.group("drive_count"))
            slot_count = int(header_match.group("slot_count"))
            continue

        drive_match = _DRIVE_RE.match(line)
        if drive_match is not None:
            details = drive_match.group("details")
            source_slot: int | None = None
            loaded_from_match = _LOADED_FROM_RE.search(details)
            if loaded_from_match is not None:
                source_slot = int(loaded_from_match.group("slot_id"))
            drives.append(
                MtxDriveInfo(
                    drive_id=int(drive_match.group("drive_id")),
                    loaded="Full" in details,
                    barcode=_parse_barcode(details),
                    source_slot=source_slot,
                )
            )
            continue

        slot_match = _SLOT_RE.match(line)
        if slot_match is not None:
            details = slot_match.group("details")
            slots.append(
                MtxSlotInfo(
                    slot_id=int(slot_match.group("slot_id")),
                    occupied="Full" in details,
                    barcode=_parse_barcode(details),
                )
            )

    return MtxStatus(
        device=device,
        drives=drives,
        slots=slots,
        drive_count=drive_count,
        slot_count=slot_count,
    )


class MtxChangerBackend:
    """Real-hardware mtx backend with explicit guard checks and dry-run support."""

    def __init__(
        self,
        device: str,
        guard: RealHardwareGuard,
        runner: SafeRunner | None = None,
        sample_status_output: str = SAMPLE_MTX_EMPTY,
    ) -> None:
        guard.validate()
        self.device = device
        self.guard = guard
        self.runner = runner or SafeRunner()
        self.sample_status_output = sample_status_output

    def inventory(self) -> MtxStatus:
        self.guard.validate()
        if self.runner.dry_run:
            return parse_mtx_status(self.sample_status_output)
        result = self.runner.run(["mtx", "-f", self.device, "status"], timeout=60)
        result.raise_on_error()
        return parse_mtx_status(result.stdout)

    def load(self, slot: int, drive: int) -> OperationResult:
        self.guard.validate()
        args = ["mtx", "-f", self.device, "load", str(slot), str(drive)]
        if self.runner.dry_run:
            return OperationResult(
                True,
                "dry-run load",
                {"device": self.device, "slot": slot, "drive": drive, "args": args},
            )
        result = self.runner.run(args, timeout=120)
        return OperationResult(
            result.success,
            "loaded" if result.success else "load failed",
            {"stdout": result.stdout, "stderr": result.stderr, "args": args},
        )

    def unload(self, drive: int, slot: int) -> OperationResult:
        self.guard.validate()
        args = ["mtx", "-f", self.device, "unload", str(slot), str(drive)]
        if self.runner.dry_run:
            return OperationResult(
                True,
                "dry-run unload",
                {"device": self.device, "slot": slot, "drive": drive, "args": args},
            )
        result = self.runner.run(args, timeout=120)
        return OperationResult(
            result.success,
            "unloaded" if result.success else "unload failed",
            {"stdout": result.stdout, "stderr": result.stderr, "args": args},
        )

    def move(self, source_slot: int, target_slot: int) -> OperationResult:
        self.guard.validate()
        args = ["mtx", "-f", self.device, "transfer", str(source_slot), str(target_slot)]
        if self.runner.dry_run:
            return OperationResult(
                True,
                "dry-run move",
                {
                    "device": self.device,
                    "source_slot": source_slot,
                    "target_slot": target_slot,
                    "args": args,
                },
            )
        result = self.runner.run(args, timeout=120)
        return OperationResult(
            result.success,
            "moved" if result.success else "move failed",
            {"stdout": result.stdout, "stderr": result.stderr, "args": args},
        )
