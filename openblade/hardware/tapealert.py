from __future__ import annotations

"""TapeAlert (SCSI LOG SENSE page 0x2E) parsing on top of ``sg_logs``.

Same shape as :mod:`openblade.hardware.sg`: sample-output constants captured
from a real drive, a pure parser over that text, and one guarded entry point
that takes a :class:`SafeRunner` and a :class:`RealHardwareGuard`.

Flag numbers, names and severities come from the T10 TapeAlert specification
(T10/02-142r0, "TapeAlert Diagnostic Specification" v3.0), section *Tape Drive
Flag Definitions* -- the table of flags 1..54 with a ``Type`` column of
C(ritical) / W(arning) / I(nformation). Flags 55..64 are **not** in that table;
SSC-3 later assigned 55..60 (loading/unload/automation/firmware/WORM) and
``sg_logs`` names them, but no severity source was available here, so they are
reported as :data:`TapeAlertSeverity.UNKNOWN` rather than guessed.

Output-format verification (this repo's rig, sg3_utils 1.46):

* ``sg_logs -p 0x2e /dev/sg1`` against the mhvtl-backed ULT3580-TD8 returns the
  page and decodes all 64 flags -- so mhvtl *does* serve page 0x2E, it simply
  reports every flag as 0 and never sets one. That is why the live-rig test
  only asserts graceful behaviour and the set-flag cases are fixtures.
* Set-flag fixtures were produced by hand-encoding the page and decoding it
  with the real tool (``sg_logs --in=<ascii-hex-file>``), so the fixture text is
  sg3_utils' own formatting, not a guess at it.
* A device that does not implement the page yields either
  ``Unable to decode page = 0x2e`` (rc=0, observed on the changer) or
  ``log_sense: field in cdb illegal`` (rc=5, observed for an unsupported page
  number). Both are reported as "not supported", never as an error.
"""

import logging
import re
from dataclasses import dataclass
from enum import StrEnum

from openblade.domain.policies import RealHardwareGuard
from openblade.hardware.discovery import resolve_sg_device
from openblade.hardware.runner import SafeRunner

logger = logging.getLogger(__name__)

TAPEALERT_LOG_PAGE = "0x2e"


class TapeAlertSeverity(StrEnum):
    """Severity classes from the TapeAlert spec's ``Type`` column."""

    CRITICAL = "critical"
    WARNING = "warning"
    INFORMATION = "information"
    #: The flag exists on the wire but the spec table consulted here does not
    #: classify it. Never rendered as "fine" -- an operator still sees it set.
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TapeAlertFlagSpec:
    number: int
    name: str
    severity: TapeAlertSeverity


_C = TapeAlertSeverity.CRITICAL
_W = TapeAlertSeverity.WARNING
_I = TapeAlertSeverity.INFORMATION
_U = TapeAlertSeverity.UNKNOWN

# T10/02-142r0 "Tape Drive Flag Definitions" (flags 1-54), then SSC-3's later
# assignments (55-60) with severity UNKNOWN, then reserved codes 61-64.
_FLAG_TABLE: tuple[tuple[int, str, TapeAlertSeverity], ...] = (
    (1, "Read Warning", _W),
    (2, "Write Warning", _W),
    (3, "Hard Error", _W),
    (4, "Media", _C),
    (5, "Read Failure", _C),
    (6, "Write Failure", _C),
    (7, "Media Life", _W),
    (8, "Not Data Grade", _W),
    (9, "Write Protect", _C),
    (10, "No Removal", _I),
    (11, "Cleaning Media", _I),
    (12, "Unsupported Format", _I),
    (13, "Recoverable Snapped Tape", _C),
    (14, "Unrecoverable Snapped Tape", _C),
    (15, "Memory Chip in Cartridge Failure", _W),
    (16, "Forced Eject", _C),
    (17, "Read Only Format", _W),
    (18, "Tape Directory Corrupted on Load", _W),
    (19, "Nearing Media Life", _I),
    (20, "Clean Now", _C),
    (21, "Clean Periodic", _W),
    (22, "Expired Cleaning Media", _C),
    (23, "Invalid Cleaning Tape", _C),
    (24, "Retention Requested", _W),
    (25, "Dual-Port Interface Error", _W),
    (26, "Cooling Fan Failure", _W),
    (27, "Power Supply", _W),
    (28, "Power Consumption", _W),
    (29, "Drive Maintenance", _W),
    (30, "Hardware A", _C),
    (31, "Hardware B", _C),
    (32, "Interface", _W),
    (33, "Eject Media", _C),
    (34, "Download Fail", _W),
    (35, "Drive Humidity", _W),
    (36, "Drive Temperature", _W),
    (37, "Drive Voltage", _W),
    (38, "Predictive Failure", _C),
    (39, "Diagnostics Required", _W),
    # 40-46 are the loader flags; SSC-3 marks them obsolete for drives and
    # sg_logs prints them as "Obsolete (28h)".."Obsolete (2Eh)".
    (40, "Loader Hardware A", _C),
    (41, "Loader Stray Tape", _C),
    (42, "Loader Hardware B", _W),
    (43, "Loader Door", _C),
    (44, "Loader Hardware C", _C),
    (45, "Loader Magazine", _C),
    (46, "Loader Predictive Failure", _W),
    (47, "Reserved (2Fh)", _U),
    (48, "Reserved (30h)", _U),
    (49, "Reserved (31h)", _U),
    (50, "Lost Statistics", _W),
    (51, "Tape Directory Invalid at Unload", _W),
    (52, "Tape System Area Write Failure", _C),
    (53, "Tape System Area Read Failure", _C),
    (54, "No Start of Data", _C),
    (55, "Loading Failure", _U),
    (56, "Unrecoverable Unload Failure", _U),
    (57, "Automation Interface Failure", _U),
    (58, "Firmware Failure", _U),
    (59, "WORM Medium - Integrity Check Failed", _U),
    (60, "WORM Medium - Overwrite Attempted", _U),
    (61, "Reserved (3Dh)", _U),
    (62, "Reserved (3Eh)", _U),
    (63, "Reserved (3Fh)", _U),
    (64, "Reserved (40h)", _U),
)

TAPEALERT_FLAGS: dict[int, TapeAlertFlagSpec] = {
    number: TapeAlertFlagSpec(number=number, name=name, severity=severity)
    for number, name, severity in _FLAG_TABLE
}

# The names sg3_utils 1.46 prints, in parameter-code order 0x01..0x40. Captured
# verbatim from `sg_logs -p 0x2e` on the rig -- they differ from the spec names
# often enough (e.g. flag 20 is "Clean Now" in the spec and "Cleaning required"
# in sg_logs) that matching on the spec name alone would silently drop flags.
SG_LOGS_FLAG_NAMES: tuple[str, ...] = (
    "Read warning",
    "Write warning",
    "Hard error",
    "Media",
    "Read failure",
    "Write failure",
    "Media life",
    "Not data grade",
    "Write protect",
    "No removal",
    "Cleaning media",
    "Unsupported format",
    "Recoverable mechanical cartridge failure",
    "Unrecoverable mechanical cartridge failure",
    "Memory chip in cartridge failure",
    "Forced eject",
    "Read only format",
    "Tape directory corrupted on load",
    "Nearing media life",
    "Cleaning required",
    "Cleaning requested",
    "Expired cleaning media",
    "Invalid cleaning tape",
    "Retension requested",
    "Dual port interface error",
    "Cooling fan failing",
    "Power supply failure",
    "Power consumption",
    "Drive maintenance",
    "Hardware A",
    "Hardware B",
    "Interface",
    "Eject media",
    "Microcode update fail",
    "Drive humidity",
    "Drive temperature",
    "Drive voltage",
    "Predictive failure",
    "Diagnostics required",
    "Obsolete (28h)",
    "Obsolete (29h)",
    "Obsolete (2Ah)",
    "Obsolete (2Bh)",
    "Obsolete (2Ch)",
    "Obsolete (2Dh)",
    "Obsolete (2Eh)",
    "Reserved (2Fh)",
    "Reserved (30h)",
    "Reserved (31h)",
    "Lost statistics",
    "Tape directory invalid at unload",
    "Tape system area write failure",
    "Tape system area read failure",
    "No start of data",
    "Loading failure",
    "Unrecoverable unload failure",
    "Automation interface failure",
    "Firmware failure",
    "WORM medium - integrity check failed",
    "WORM medium - overwrite attempted",
    "Reserved parameter code 0x3d, flag",
    "Reserved parameter code 0x3e, flag",
    "Reserved parameter code 0x3f, flag",
    "Reserved parameter code 0x40, flag",
)


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


_NAME_TO_NUMBER: dict[str, int] = {}
for _index, _sg_name in enumerate(SG_LOGS_FLAG_NAMES, start=1):
    _NAME_TO_NUMBER.setdefault(_normalize(_sg_name), _index)
for _spec in TAPEALERT_FLAGS.values():
    _NAME_TO_NUMBER.setdefault(_normalize(_spec.name), _spec.number)


# Captured from the rig: `sg_logs -p 0x2e /dev/sg1` on an IBM ULT3580-TD8 with
# no alerts set. sg_logs prints an inquiry banner line first when it was given a
# device; the parser tolerates it, and every other non-flag line.
SAMPLE_SG_LOGS_TAPEALERT_CLEAN = """    IBM       ULT3580-TD8       HB81
Tape alert page (ssc-3) [0x2e]
  Read warning: 0
  Write warning: 0
  Hard error: 0
  Media: 0
  Read failure: 0
  Write failure: 0
  Media life: 0
  Not data grade: 0
  Write protect: 0
  No removal: 0
  Cleaning media: 0
  Unsupported format: 0
  Recoverable mechanical cartridge failure: 0
  Unrecoverable mechanical cartridge failure: 0
  Memory chip in cartridge failure: 0
  Forced eject: 0
  Read only format: 0
  Tape directory corrupted on load: 0
  Nearing media life: 0
  Cleaning required: 0
  Cleaning requested: 0
  Expired cleaning media: 0
  Invalid cleaning tape: 0
  Retension requested: 0
  Dual port interface error: 0
  Cooling fan failing: 0
  Power supply failure: 0
  Power consumption: 0
  Drive maintenance: 0
  Hardware A: 0
  Hardware B: 0
  Interface: 0
  Eject media: 0
  Microcode update fail: 0
  Drive humidity: 0
  Drive temperature: 0
  Drive voltage: 0
  Predictive failure: 0
  Diagnostics required: 0
  Obsolete (28h): 0
  Obsolete (29h): 0
  Obsolete (2Ah): 0
  Obsolete (2Bh): 0
  Obsolete (2Ch): 0
  Obsolete (2Dh): 0
  Obsolete (2Eh): 0
  Reserved (2Fh): 0
  Reserved (30h): 0
  Reserved (31h): 0
  Lost statistics: 0
  Tape directory invalid at unload: 0
  Tape system area write failure: 0
  Tape system area read failure: 0
  No start of data: 0
  Loading failure: 0
  Unrecoverable unload failure: 0
  Automation interface failure: 0
  Firmware failure: 0
  WORM medium - integrity check failed: 0
  WORM medium - overwrite attempted: 0
  Reserved parameter code 0x3d, flag: 0
  Reserved parameter code 0x3e, flag: 0
  Reserved parameter code 0x3f, flag: 0
  Reserved parameter code 0x40, flag: 0
"""

# Captured from the rig: `sg_logs -p 0x2e /dev/sg2` (the QUANTUM changer). The
# device answers LOG SENSE but the payload is not a tape-alert page, so sg_logs
# dumps hex and exits 0. "Page came back but is not TapeAlert" must read as
# unsupported, not as "no flags set".
SAMPLE_SG_LOGS_TAPEALERT_UNDECODABLE = """    QUANTUM   QUANTUM Scalar    0108
Unable to decode page = 0x2e, here is hex:
 00     2e 00 01 40 00 01 c0 01  00 00 02 c0 01 00 00 03
 10     c0 01 00 00 04 c0 01 00  00 05 c0 01 00 00 06 c0
 .....  [truncated after 32 of 324 bytes (use '-H' to see the rest)]
"""

# Captured from the rig: `sg_logs -p 0x2f /dev/nst0`, i.e. a log page the drive
# does not implement. rc=5.
SAMPLE_SG_LOGS_PAGE_UNSUPPORTED = """log_sense: field in cdb illegal
sg_logs failed: Illegal request
    IBM       ULT3580-TD8       HB81
"""

_FLAG_LINE_RE = re.compile(r"^\s{2,}(?P<name>\S.*?):\s*(?P<value>\d+)\s*$")
_TAPEALERT_HEADER_RE = re.compile(r"Tape alert page", re.IGNORECASE)
_UNDECODABLE_RE = re.compile(r"Unable to decode page", re.IGNORECASE)
_ILLEGAL_REQUEST_RE = re.compile(r"field in cdb illegal|Illegal request", re.IGNORECASE)


@dataclass(frozen=True)
class TapeAlertFlag:
    """One decoded flag. ``number`` is None when the name is unrecognised."""

    number: int | None
    name: str
    severity: TapeAlertSeverity
    value: bool


@dataclass(frozen=True)
class TapeAlertReport:
    device: str
    supported: bool
    flags: tuple[TapeAlertFlag, ...]
    #: Curated, operator-facing reason when ``supported`` is False.
    reason: str | None = None

    @property
    def active(self) -> tuple[TapeAlertFlag, ...]:
        return tuple(flag for flag in self.flags if flag.value)

    @property
    def worst_severity(self) -> TapeAlertSeverity | None:
        order = (
            TapeAlertSeverity.CRITICAL,
            TapeAlertSeverity.WARNING,
            TapeAlertSeverity.UNKNOWN,
            TapeAlertSeverity.INFORMATION,
        )
        active = {flag.severity for flag in self.active}
        for severity in order:
            if severity in active:
                return severity
        return None

    def to_dict(self) -> dict[str, object]:
        return {
            "device": self.device,
            "supported": self.supported,
            "reason": self.reason,
            "worst_severity": None if self.worst_severity is None else str(self.worst_severity),
            "active_flags": [
                {
                    "number": flag.number,
                    "name": flag.name,
                    "severity": str(flag.severity),
                }
                for flag in self.active
            ],
        }


def parse_sg_logs_tapealert(output: str, device: str = "") -> TapeAlertReport:
    """Parse ``sg_logs -p 0x2e`` output into a :class:`TapeAlertReport`.

    Never raises on shape: a drive that does not implement the page, a page that
    sg_logs cannot decode, and truncated output all come back as
    ``supported=False`` with a reason. Only a well-formed tape-alert page
    produces flags.
    """
    saw_header = _TAPEALERT_HEADER_RE.search(output) is not None
    if _UNDECODABLE_RE.search(output) is not None:
        return TapeAlertReport(
            device=device,
            supported=False,
            flags=(),
            reason="log page 0x2e came back but is not a TapeAlert page",
        )
    if not saw_header and _ILLEGAL_REQUEST_RE.search(output) is not None:
        return TapeAlertReport(
            device=device,
            supported=False,
            flags=(),
            reason="drive rejected LOG SENSE for page 0x2e (illegal request)",
        )

    flags: list[TapeAlertFlag] = []
    for raw_line in output.splitlines():
        match = _FLAG_LINE_RE.match(raw_line)
        if match is None:
            continue
        name = match.group("name").strip()
        number = _NAME_TO_NUMBER.get(_normalize(name))
        spec = TAPEALERT_FLAGS.get(number) if number is not None else None
        flags.append(
            TapeAlertFlag(
                number=number,
                name=name,
                severity=spec.severity if spec is not None else TapeAlertSeverity.UNKNOWN,
                value=match.group("value") != "0",
            )
        )

    if not saw_header or not flags:
        return TapeAlertReport(
            device=device,
            supported=False,
            flags=(),
            reason="no TapeAlert page in sg_logs output",
        )
    return TapeAlertReport(device=device, supported=True, flags=tuple(flags))


def read_tape_alerts(
    device: str,
    runner: SafeRunner,
    guard: RealHardwareGuard,
) -> TapeAlertReport:
    """Read TapeAlert flags from ``device`` via ``sg_logs``.

    ``device`` may be a tape node (``/dev/nst0``); it is resolved onto its SCSI
    generic node first, because LOG SENSE on a rewinding tape node has side
    effects (see :func:`resolve_sg_device`).

    A drive with no TapeAlert support is **not** an error: the report comes back
    with ``supported=False`` and a reason.
    """
    guard.validate()
    sg_device = resolve_sg_device(device)
    if runner.dry_run:
        return parse_sg_logs_tapealert(SAMPLE_SG_LOGS_TAPEALERT_CLEAN, device=sg_device)
    result = runner.run(["sg_logs", "-p", TAPEALERT_LOG_PAGE, sg_device], timeout=30)
    report = parse_sg_logs_tapealert(result.stdout + "\n" + result.stderr, device=sg_device)
    if not report.supported and not result.success and report.reason is not None:
        # Distinguish "drive has no TapeAlert page" from "the command itself
        # failed" -- otherwise a missing sg_logs or a bad device path reads as a
        # healthy-but-silent drive.
        detail = (result.stderr.strip() or result.stdout.strip()).splitlines()
        report = TapeAlertReport(
            device=sg_device,
            supported=False,
            flags=(),
            reason=f"{report.reason} (sg_logs exited {result.returncode}"
            + (f": {detail[0].strip()})" if detail else ")"),
        )
    if not report.supported:
        logger.info(
            "TapeAlert unavailable on %s (rc=%d): %s", sg_device, result.returncode, report.reason
        )
    return report
