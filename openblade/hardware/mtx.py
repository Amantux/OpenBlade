from __future__ import annotations

"""Safe wrapper around the mtx tape library changer tool."""

import logging
import re
from dataclasses import dataclass, field

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

# A three-drive Scalar i3 partition (the shipped scalar-i3-50-3 shape): two drives
# loaded from different slots, one empty, plus the i3's Import/Export station.
SAMPLE_MTX_THREE_DRIVES = """
Storage Changer /dev/sg3:3 Drives, 50 Slots ( 2 Import/Export )
Data Transfer Element 0:Full (Storage Element 1 Loaded):VolumeTag=VOL001L8
Data Transfer Element 1:Empty
Data Transfer Element 2:Full (Storage Element 4 Loaded):VolumeTag=VOL004L8
      Storage Element 1:Empty
      Storage Element 2:Full :VolumeTag=VOL002L8
      Storage Element 3:Full :VolumeTag=CLN001L1
      Storage Element 4:Empty
      Storage Element 5:Full :VolumeTag=VOL005L8
      Storage Element 51 IMPORT/EXPORT:Empty
      Storage Element 52 IMPORT/EXPORT:Full :VolumeTag=VOL052L8
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

# Captured verbatim from `mtx -f /dev/sgN status` against the mhvtl rehearsal
# rig (scripts/mhvtl/), 3 drives + 8 storage slots + 4 I/E slots, with a tape
# loaded into a drive. Two details here are NOT reproduced by the hand-written
# samples above, and both used to break the parser:
#
#   * mtx prints "VolumeTag = X" (spaces around '=') for a Data Transfer
#     Element, but "VolumeTag=X" (no spaces) for a Storage Element.
#   * Import/export slots carry an " IMPORT/EXPORT" infix before the colon.
#
# Keep this sample byte-accurate; it is the regression anchor for both.
SAMPLE_MTX_REAL_SCALAR = """
  Storage Changer /dev/sg3:3 Drives, 12 Slots ( 4 Import/Export )
Data Transfer Element 0:Full (Storage Element 6 Loaded):VolumeTag = OB0007L8
Data Transfer Element 1:Empty
Data Transfer Element 2:Empty
      Storage Element 1:Full :VolumeTag=OB0001L8
      Storage Element 2:Full :VolumeTag=OB0002L8
      Storage Element 3:Full :VolumeTag=OB0003L8
      Storage Element 4:Full :VolumeTag=OB0004L8
      Storage Element 5:Full :VolumeTag=CLN001L8
      Storage Element 6:Empty
      Storage Element 7:Full :VolumeTag=OB0008L8
      Storage Element 8:Empty
      Storage Element 9 IMPORT/EXPORT:Empty
      Storage Element 10 IMPORT/EXPORT:Empty
      Storage Element 11 IMPORT/EXPORT:Full :VolumeTag=OB0009L8
      Storage Element 12 IMPORT/EXPORT:Empty
"""

_HEADER_RE = re.compile(
    r"^Storage Changer (?P<device>\S+):(?P<drive_count>\d+) Drives, "
    r"(?P<slot_count>\d+) Slots"
)
_DRIVE_RE = re.compile(r"^Data Transfer Element (?P<drive_id>\d+):(?P<details>.+)$")
# The element number may be followed by flags before the colon; the only one
# mtx emits today is " IMPORT/EXPORT" for an import/export (mailslot) element.
# Matching "\d+:" alone silently dropped every I/E slot from the inventory.
_SLOT_RE = re.compile(r"^Storage Element (?P<slot_id>\d+)(?P<flags>[^:]*):(?P<details>.+)$")
_LOADED_FROM_RE = re.compile(r"Storage Element (?P<slot_id>\d+) Loaded")
# mtx is inconsistent about whitespace around '=': Data Transfer Element lines
# use "VolumeTag = X" while Storage Element lines use "VolumeTag=X". Requiring
# the tight form meant a tape loaded in a drive always parsed as barcode=None.
_BARCODE_RE = re.compile(r"VolumeTag\s*=\s*(?P<barcode>\S+)")


@dataclass(frozen=True)
class MtxSlotInfo:
    slot_id: int
    occupied: bool
    barcode: str | None
    is_import_export: bool = False

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
    # Data storage slots ONLY. Import/export (mailslot) elements are kept
    # separately: every consumer of `slots` treats it as "somewhere a tape may
    # be parked or unloaded to", and an I/E slot is not that. Folding them in
    # would, on a full library, make the first *empty* slot the operator
    # mailslot - so an unload would eject the cartridge to the front panel.
    slots: list[MtxSlotInfo]
    drive_count: int = 0
    # From the mtx header, which counts storage AND import/export elements -
    # so this is len(slots) + len(import_export_slots), not len(slots).
    slot_count: int = 0
    import_export_slots: list[MtxSlotInfo] = field(default_factory=list)

    @property
    def all_slots(self) -> list[MtxSlotInfo]:
        """Storage and import/export elements together, in element order."""
        return sorted(self.slots + self.import_export_slots, key=lambda slot: slot.slot_id)


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
    import_export_slots: list[MtxSlotInfo] = []

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
            is_import_export = "IMPORT/EXPORT" in slot_match.group("flags").upper()
            slot = MtxSlotInfo(
                slot_id=int(slot_match.group("slot_id")),
                occupied="Full" in details,
                barcode=_parse_barcode(details),
                is_import_export=is_import_export,
            )
            (import_export_slots if is_import_export else slots).append(slot)

    return MtxStatus(
        device=device,
        drives=drives,
        slots=slots,
        import_export_slots=import_export_slots,
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
