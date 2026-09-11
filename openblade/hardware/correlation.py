"""Correlate library drive elements (mtx Data Transfer Elements) with tape devices.

The problem
-----------
``mtx -f <changer> status`` numbers drives as *Data Transfer Element 0..N-1*. The
host numbers tape devices ``/dev/nst0..nstN-1``. **Nothing guarantees the two
orders agree.** With one drive the mistake is invisible; with two or three it is
the classic "wrote to the wrong drive" bug — the changer loads a cartridge into
DTE 1 and the writer streams LTFS into whatever cartridge happens to be in
``/dev/nst1``. See ``docs/runbooks/real-i3-bringup-plan.md`` (Phase 2).

The design chosen (and why)
---------------------------
The authoritative SCSI route is READ ELEMENT STATUS with the DVCID bit, which
returns each element's device identifier; the library then tells you which serial
sits in which element. ``mtx status`` does **not** print serials, and no tool in
this repo issues READ ELEMENT STATUS — wiring it up means a new binary dependency
(``sg_read_element_status``/``sg_ses``) plus a hex descriptor parser we could not
validate against any captured real-i3 output. Shipping an unvalidated binary
parser on the write path is worse than shipping no correlation.

So correlation here is **operator-declared and machine-verified**:

1. ``OPENBLADE_DRIVE_SERIAL_MAP="<serial>:<dte>,..."`` — the operator reads each
   drive's serial off the i3 web UI (which shows serial *per drive bay/element*)
   and declares the mapping once.
2. At startup every configured device is probed with ``sg_inq`` and its live
   *Unit serial number* is compared against the declaration. Any disagreement —
   a serial that is not present, a device whose serial is not declared, a
   duplicate — raises :class:`DriveCorrelationError` and the backend refuses to
   start. Confirmation is not a licence to guess.
3. With no map declared, correlation falls back to **positional** order and logs
   a loud warning that the ordering is UNVERIFIED. This preserves today's
   behaviour (a single-drive library is unaffected) while making the ≥2-drive
   risk visible in the log instead of silent.

The serials are captured either way, so ``connect-i3`` output tells an operator
exactly what to paste into ``OPENBLADE_DRIVE_SERIAL_MAP``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from openblade.domain.errors import DriveCorrelationError
from openblade.domain.policies import RealHardwareGuard
from openblade.hardware.runner import SafeRunner
from openblade.hardware.sg import sg_inq

logger = logging.getLogger(__name__)

#: Correlation established from a verified ``OPENBLADE_DRIVE_SERIAL_MAP``.
SOURCE_SERIAL_MAP = "serial_map"
#: Correlation assumed from device order — ordering is NOT verified.
SOURCE_POSITIONAL = "positional"
#: Dry-run: no device was probed, so nothing could be verified.
SOURCE_DRY_RUN = "dry_run"


@dataclass(frozen=True)
class DriveCorrelationEntry:
    """One library drive element bound to one host tape device."""

    drive_id: int
    """mtx Data Transfer Element index."""

    device: str
    """Host device the writer will open (e.g. ``/dev/nst1``)."""

    serial: str
    """Unit serial number read live from ``device`` ("" when not probed/reported)."""


@dataclass(frozen=True)
class DriveCorrelation:
    """Result of correlating drive elements with devices."""

    entries: tuple[DriveCorrelationEntry, ...]
    source: str
    verified: bool
    warnings: tuple[str, ...] = ()

    def device_for(self, drive_id: int) -> str:
        for entry in self.entries:
            if entry.drive_id == drive_id:
                return entry.device
        raise KeyError(f"No tape device correlated with drive {drive_id}")

    def serial_for(self, drive_id: int) -> str:
        for entry in self.entries:
            if entry.drive_id == drive_id:
                return entry.serial
        raise KeyError(f"No tape device correlated with drive {drive_id}")

    def devices_in_drive_order(self) -> list[str]:
        return [entry.device for entry in sorted(self.entries, key=lambda item: item.drive_id)]

    def to_payload(self) -> list[dict[str, str | int]]:
        """Serializable view for reports (``openblade hardware connect-i3``)."""
        return [
            {"driveId": entry.drive_id, "device": entry.device, "serial": entry.serial}
            for entry in sorted(self.entries, key=lambda item: item.drive_id)
        ]


def read_drive_serials(
    devices: Sequence[str],
    runner: SafeRunner,
    guard: RealHardwareGuard,
) -> dict[str, str]:
    """Return ``{device: unit_serial_number}`` from a live ``sg_inq`` per device."""
    serials: dict[str, str] = {}
    for device in devices:
        serials[device] = sg_inq(device, runner, guard).serial
    return serials


def correlate_drives(
    *,
    devices: Sequence[str],
    serial_map: Mapping[str, int] | Iterable[tuple[str, int]],
    runner: SafeRunner,
    guard: RealHardwareGuard,
    element_count: int | None = None,
) -> DriveCorrelation:
    """Bind each library drive element to a host device.

    ``element_count`` is the number of Data Transfer Elements the changer
    reports, when known; a mismatch against the configured device count is
    reported as a warning (the library may legitimately have an unpopulated bay).
    """
    guard.validate()
    declared = dict(serial_map)
    warnings: list[str] = []

    if not devices:
        raise DriveCorrelationError(
            "No tape devices configured. Set OPENBLADE_DRIVE_DEVICES explicitly "
            "(e.g. /dev/nst0,/dev/nst1,/dev/nst2)."
        )

    if element_count is not None and element_count != len(devices):
        warnings.append(
            f"library reports {element_count} drive element(s) but "
            f"{len(devices)} device(s) are configured"
        )

    if runner.dry_run:
        # Nothing was probed, so nothing may be claimed as verified.
        warnings.append("dry-run: drive serials were not read, correlation is positional")
        return DriveCorrelation(
            entries=tuple(
                DriveCorrelationEntry(drive_id=index, device=device, serial="")
                for index, device in enumerate(devices)
            ),
            source=SOURCE_DRY_RUN,
            verified=False,
            warnings=tuple(warnings),
        )

    live = read_drive_serials(devices, runner, guard)

    if not declared:
        for warning in warnings:
            logger.warning("drive correlation: %s", warning)
        logger.warning(
            "DRIVE ORDER UNVERIFIED: mapping Data Transfer Element N -> %s by position. "
            "With more than one drive this can write to the WRONG drive. Set "
            "OPENBLADE_DRIVE_SERIAL_MAP to verify it; observed serials: %s",
            list(devices),
            _format_serials(live),
        )
        return DriveCorrelation(
            entries=tuple(
                DriveCorrelationEntry(drive_id=index, device=device, serial=live.get(device, ""))
                for index, device in enumerate(devices)
            ),
            source=SOURCE_POSITIONAL,
            verified=False,
            warnings=(*warnings, "drive order is unverified (no OPENBLADE_DRIVE_SERIAL_MAP)"),
        )

    _refuse_on_mismatch(devices=devices, live=live, declared=declared, element_count=element_count)

    serial_to_device = {serial: device for device, serial in live.items()}
    entries = tuple(
        DriveCorrelationEntry(drive_id=drive_id, device=serial_to_device[serial], serial=serial)
        for serial, drive_id in sorted(declared.items(), key=lambda item: item[1])
    )
    for warning in warnings:
        logger.warning("drive correlation: %s", warning)
    logger.info(
        "drive correlation verified from OPENBLADE_DRIVE_SERIAL_MAP: %s",
        ", ".join(f"DTE {entry.drive_id} -> {entry.device} ({entry.serial})" for entry in entries),
    )
    return DriveCorrelation(
        entries=entries,
        source=SOURCE_SERIAL_MAP,
        verified=True,
        warnings=tuple(warnings),
    )


def _refuse_on_mismatch(
    *,
    devices: Sequence[str],
    live: Mapping[str, str],
    declared: Mapping[str, int],
    element_count: int | None,
) -> None:
    """Raise DriveCorrelationError unless the declared map exactly matches reality."""
    blank = sorted(device for device in devices if not live.get(device))
    if blank:
        raise DriveCorrelationError(
            "OPENBLADE_DRIVE_SERIAL_MAP is set but these devices report no unit serial "
            f"number, so the mapping cannot be verified: {blank}. Check `sg_inq <device>` "
            "and drive permissions."
        )

    duplicates = sorted(
        {serial for serial in live.values() if list(live.values()).count(serial) > 1}
    )
    if duplicates:
        raise DriveCorrelationError(
            f"Two or more configured devices report the same unit serial number {duplicates}; "
            f"drive correlation is ambiguous. Observed: {_format_serials(live)}."
        )

    live_serials = set(live.values())
    declared_serials = set(declared)
    missing = sorted(declared_serials - live_serials)
    unexpected = sorted(live_serials - declared_serials)
    if missing or unexpected:
        raise DriveCorrelationError(
            "OPENBLADE_DRIVE_SERIAL_MAP does not match the drives that are attached. "
            f"Declared but not found: {missing or 'none'}. "
            f"Attached but not declared: {unexpected or 'none'}. "
            f"Observed: {_format_serials(live)}. Refusing to start rather than guess "
            "which drive is which."
        )

    if element_count is not None:
        out_of_range = sorted(
            drive_id for drive_id in declared.values() if drive_id >= element_count
        )
        if out_of_range:
            raise DriveCorrelationError(
                f"OPENBLADE_DRIVE_SERIAL_MAP declares drive element(s) {out_of_range} but the "
                f"changer reports only {element_count} Data Transfer Element(s) (0..{element_count - 1})."
            )


def _format_serials(live: Mapping[str, str]) -> str:
    return ", ".join(f"{device}={serial or '<none>'}" for device, serial in sorted(live.items()))
