"""A stateful ``mtx`` fake so the SCSI backend can be exercised without a changer.

``StatefulMtxRunner`` replaces :class:`SafeRunner` and answers ``mtx -f <dev> ...``
argv the way the real tool does: ``status`` renders the text format that
``parse_mtx_status`` consumes (byte-shaped like ``SAMPLE_MTX_THREE_DRIVES``), and
``load`` / ``unload`` / ``transfer`` mutate the element state or fail with an
mtx-like ``Request Sense`` message. Element numbering follows mtx: drives are
0-based Data Transfer Elements, storage slots are 1-based, and the import/export
slots follow the storage slots.
"""

from __future__ import annotations

from openblade.hardware.runner import CommandResult, SafeRunner

_ILLEGAL_REQUEST = "mtx: Request Sense: Sense Key=Illegal Request"


class StatefulMtxRunner(SafeRunner):
    """SafeRunner that simulates one SCSI medium changer driven by ``mtx``."""

    def __init__(
        self,
        device: str,
        *,
        drives: int = 3,
        slots: int = 50,
        ie_slots: int = 2,
        barcodes: dict[int, str],
        drive_serials: dict[str, str] | None = None,
    ) -> None:
        super().__init__(dry_run=False)
        self.device = device
        self.drive_count = drives
        self.slot_count = slots
        self.ie_count = ie_slots
        total = slots + ie_slots
        for slot in barcodes:
            if not 1 <= slot <= total:
                raise ValueError(f"slot {slot} is outside 1..{total}")
        self.slots: dict[int, str | None] = {s: barcodes.get(s) for s in range(1, total + 1)}
        # Per drive: (barcode, source slot) when full, None when empty.
        self.drives: dict[int, tuple[str, int] | None] = dict.fromkeys(range(drives))
        # ``RealLibraryBackend`` correlates drives at construction with
        # ``sg_inq <device>``; answer it for the tape devices named here.
        self.drive_serials = dict(drive_serials or {})
        self.calls: list[list[str]] = []

    def run(
        self,
        args: list[str],
        timeout: int | None = None,
        redact_args: list[int] | None = None,
    ) -> CommandResult:
        self.calls.append(list(args))
        if len(args) == 2 and args[0] == "sg_inq" and args[1] in self.drive_serials:
            return self._result(args, 0, stdout=_sg_inq_output(self.drive_serials[args[1]]))
        if len(args) < 4 or args[:3] != ["mtx", "-f", self.device]:
            return self._result(args, 2, stderr=f"mtx: unsupported invocation {args!r}")
        verb, operands = args[3], args[4:]
        if verb == "status" and not operands:
            return self._result(args, 0, stdout=self.render_status())
        numbers = _ints(operands)
        if numbers is None or len(numbers) != 2:
            return self._result(args, 2, stderr=f"mtx: unsupported invocation {args!r}")
        if verb == "load":
            error = self._load(*numbers)
        elif verb == "unload":
            error = self._unload(*numbers)
        elif verb == "transfer":
            error = self._transfer(*numbers)
        else:
            return self._result(args, 2, stderr=f"mtx: unsupported invocation {args!r}")
        if error is not None:
            return self._result(args, 1, stderr=f"{_ILLEGAL_REQUEST} ({error})")
        return self._result(args, 0)

    def render_status(self) -> str:
        lines = [
            f"Storage Changer {self.device}:{self.drive_count} Drives, "
            f"{self.slot_count} Slots ( {self.ie_count} Import/Export )"
        ]
        for drive_id, content in self.drives.items():
            if content is None:
                lines.append(f"Data Transfer Element {drive_id}:Empty")
            else:
                barcode, source = content
                lines.append(
                    f"Data Transfer Element {drive_id}:Full (Storage Element {source} Loaded)"
                    f":VolumeTag = {barcode}"
                )
        for slot_id, slot_barcode in self.slots.items():
            flag = " IMPORT/EXPORT" if slot_id > self.slot_count else ""
            state = "Empty" if slot_barcode is None else f"Full :VolumeTag={slot_barcode}"
            lines.append(f"      Storage Element {slot_id}{flag}:{state}")
        return "\n" + "\n".join(lines) + "\n"

    def _load(self, slot: int, drive: int) -> str | None:
        if slot not in self.slots or drive not in self.drives:
            return "invalid element address"
        barcode = self.slots[slot]
        if barcode is None:
            return f"source element {slot} is empty"
        if self.drives[drive] is not None:
            return f"drive {drive} is full"
        self.slots[slot] = None
        self.drives[drive] = (barcode, slot)
        return None

    def _unload(self, slot: int, drive: int) -> str | None:
        if slot not in self.slots or drive not in self.drives:
            return "invalid element address"
        content = self.drives[drive]
        if content is None:
            return f"drive {drive} is empty"
        if self.slots[slot] is not None:
            return f"destination element {slot} is full"
        self.slots[slot] = content[0]
        self.drives[drive] = None
        return None

    def _transfer(self, source: int, target: int) -> str | None:
        if source not in self.slots or target not in self.slots:
            return "invalid element address"
        if self.slots[source] is None:
            return f"source element {source} is empty"
        if self.slots[target] is not None:
            return f"destination element {target} is full"
        self.slots[target], self.slots[source] = self.slots[source], None
        return None

    @staticmethod
    def _result(args: list[str], code: int, *, stdout: str = "", stderr: str = "") -> CommandResult:
        return CommandResult(
            args=list(args), returncode=code, stdout=stdout, stderr=stderr, elapsed_seconds=0.0
        )


def _sg_inq_output(serial: str) -> str:
    return (
        "standard INQUIRY:\n"
        "  PQual=0  PDT=1  RMB=1  LU_CONG=0  version=0x06  [SPC-4]\n"
        " Vendor identification: IBM\n"
        " Product identification: ULTRIUM-TD8\n"
        f" Unit serial number: {serial}\n"
    )


def _ints(values: list[str]) -> list[int] | None:
    try:
        return [int(value) for value in values]
    except ValueError:
        return None
