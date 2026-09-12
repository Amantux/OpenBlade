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

So correlation here is **operator-declared, with a machine-checked serial set**:

1. ``OPENBLADE_DRIVE_SERIAL_MAP="<serial>:<dte>,..."`` — the operator reads each
   drive's serial off the i3 web UI (which shows serial *per drive bay/element*)
   and declares the mapping once. **The element index is 0-based** (``mtx``'s
   Data Transfer Element numbering); the i3 UI numbers drive bays from 1.
2. At startup every configured device is probed with ``sg_inq`` and its live
   *Unit serial number* is compared against the declaration. Any disagreement —
   a serial that is not present, a device whose serial is not declared, a
   duplicate, a device with no serial, an element index beyond the changer's
   drive count — raises :class:`DriveCorrelationError` and the backend refuses
   to start. Confirmation is not a licence to guess.
3. With no map declared, correlation falls back to **positional** order and logs
   a loud warning that the ordering is UNVERIFIED. This preserves today's
   behaviour (a single-drive library is unaffected) while making the ≥2-drive
   risk visible in the log instead of silent.

**The limit of the check, stated plainly.** Nothing in this design ever observes
which serial is in which element, so the serial check proves the *set* of
attached drives is the set that was declared — it CANNOT detect a declaration
whose elements are transposed or rotated (e.g. an operator who read 1-based bay
numbers off the UI). ``serials_verified`` says exactly that and nothing more; no
log line or report field claims the element assignment itself was verified.
Proving that requires either READ ELEMENT STATUS with DVCID, or an empirical
check (load a scratch tape into element *k* and confirm only the correlated
device reports a tape online) — both are follow-on work for the bring-up, and
``docs/hardware-setup.md`` tells the operator to do the empirical check by hand.

The serials are captured either way, so ``connect-i3`` output tells an operator
exactly what to paste into ``OPENBLADE_DRIVE_SERIAL_MAP``.

A backend that can *ask the library* which drives it has (the AML Web Services
backend can; ``mtx`` cannot) gets one more check on top of the two above:
:func:`verify_against_library_serials` compares the declared serials with the
serials the library reports, which catches a declaration that matches this
host's drives but belongs to a different library. It still cannot observe which
serial sits in which element.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass

from openblade.domain.errors import DriveCorrelationError, RealHardwareDisabledError
from openblade.domain.policies import RealHardwareGuard
from openblade.hardware.runner import CommandError, SafeRunner
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

    serials_verified: bool = False
    """True when every device was probed and its serial matched the declaration.

    This is deliberately NOT called "verified": it proves the SET of attached
    drives is the set the operator declared, not that element 0 really is the
    drive they assigned to element 0. See the module docstring.
    """

    warnings: tuple[str, ...] = ()

    def device_for(self, drive_id: int) -> str:
        for entry in self.entries:
            if entry.drive_id == drive_id:
                return entry.device
        raise DriveCorrelationError(
            f"Drive element {drive_id} has no correlated host device "
            f"(correlated elements: {sorted(entry.drive_id for entry in self.entries)}). "
            "List every drive in OPENBLADE_DRIVE_DEVICES."
        )

    def serial_for(self, drive_id: int) -> str:
        for entry in self.entries:
            if entry.drive_id == drive_id:
                return entry.serial
        raise DriveCorrelationError(f"Drive element {drive_id} has no correlated host device")

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
    probe_devices: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return ``{device: unit_serial_number}`` from a live ``sg_inq`` per device.

    ``probe_devices`` maps a target device (the node the writer opens, e.g.
    ``/dev/nst1``) to the node to run ``sg_inq`` against. Prefer the generic
    ``/dev/sgN`` node: the ``st`` driver allows a single open, so inquiring on
    ``nst`` while LTFS holds the drive fails with EBUSY.

    Any failure to run ``sg_inq`` is re-raised as a typed
    :class:`DriveCorrelationError` — an un-probed drive must not be silently
    treated as correlated.
    """
    serials: dict[str, str] = {}
    for device in devices:
        probe = (probe_devices or {}).get(device, device)
        try:
            serials[device] = sg_inq(probe, runner, guard).serial
        except (RealHardwareDisabledError, DriveCorrelationError):
            raise
        except FileNotFoundError as exc:
            raise DriveCorrelationError(
                "sg_inq is not installed, so drive serials cannot be read. Install "
                "sg3_utils on the host, or unset OPENBLADE_DRIVE_SERIAL_MAP to run "
                "with unverified positional drive order."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise DriveCorrelationError(
                f"sg_inq timed out on {probe}; the drive did not answer an INQUIRY."
            ) from exc
        except CommandError as exc:
            raise DriveCorrelationError(
                f"sg_inq failed on {probe} (rc={exc.returncode}). Check the device path, "
                "permissions (the 'tape' group), and that the drive is not held by "
                "another process."
            ) from exc
    return serials


def correlate_drives(
    *,
    devices: Sequence[str],
    serial_map: Mapping[str, int] | Iterable[tuple[str, int]],
    runner: SafeRunner,
    guard: RealHardwareGuard,
    element_count: int | None = None,
    probe_devices: Mapping[str, str] | None = None,
) -> DriveCorrelation:
    """Bind each library drive element to a host device.

    ``element_count`` is the number of Data Transfer Elements the changer
    reports, when known; a mismatch against the configured device count is
    reported as a warning (a drive may legitimately not be cabled to this host).

    What the serial check does and does NOT prove is spelled out in the module
    docstring: it proves the SET of attached drives is the set the operator
    declared; it cannot detect a declaration whose elements are transposed.
    """
    guard.validate()
    declared = dict(serial_map)
    warnings: list[str] = []

    if not devices:
        # Not fatal: `connect-i3` is the diagnostic an operator runs *because*
        # no drives are showing up, so it must still produce a report. Any
        # attempt to use a drive then fails with a curated error.
        logger.warning(
            "No tape devices configured or discovered. Set OPENBLADE_DRIVE_DEVICES "
            "explicitly (e.g. /dev/nst0,/dev/nst1,/dev/nst2)."
        )
        return DriveCorrelation(
            entries=(),
            source=SOURCE_POSITIONAL,
            serials_verified=False,
            warnings=("no tape devices configured or discovered",),
        )

    duplicate_devices = sorted({device for device in devices if list(devices).count(device) > 1})
    if duplicate_devices:
        raise DriveCorrelationError(
            f"OPENBLADE_DRIVE_DEVICES lists {duplicate_devices} more than once. Two drive "
            "elements would map to one physical drive."
        )

    if element_count is not None and element_count != len(devices):
        if declared and element_count > len(devices):
            # The scheduler sizes itself from the CHANGER's element count, so an
            # element with no device gets a cartridge loaded into it and then
            # fails at mount, stranding the tape. An operator who declared a map
            # meant it to be complete: refuse at startup instead.
            raise DriveCorrelationError(
                f"The changer reports {element_count} drive element(s) but only "
                f"{len(devices)} device(s) are configured. Every element must have a "
                "host device: list them all in OPENBLADE_DRIVE_DEVICES, or a job "
                "scheduled onto the missing element would strand a cartridge in it."
            )
        warnings.append(
            f"library reports {element_count} drive element(s) but "
            f"{len(devices)} device(s) are configured"
        )

    if runner.dry_run:
        # A declared map binds SERIALS to elements, and serials can only come
        # from a live probe — so a dry run cannot apply it, and must say so
        # rather than present a positional guess as if it were the plan.
        if declared:
            _refuse_on_declared_element_range(declared, element_count)
            warnings.append(
                "dry-run: drive serials were not read, so OPENBLADE_DRIVE_SERIAL_MAP "
                "could not be applied; the devices named here are positional and a "
                "live run may use a different one per element"
            )
            logger.warning("drive correlation: %s", warnings[-1])
        else:
            warnings.append("dry-run: drive serials were not read")
        return DriveCorrelation(
            entries=tuple(
                DriveCorrelationEntry(drive_id=index, device=device, serial="")
                for index, device in enumerate(devices)
            ),
            source=SOURCE_DRY_RUN,
            serials_verified=False,
            warnings=tuple(warnings),
        )

    live = read_drive_serials(devices, runner, guard, probe_devices)

    # Two devices reporting one serial is never a correct configuration, with or
    # without a declared map — it means two elements would target one drive.
    _refuse_on_duplicate_serials(live)

    if not declared:
        blank = sorted(device for device in devices if not live.get(device))
        if blank:
            warnings.append(f"devices reporting no unit serial number: {blank}")
        for warning in warnings:
            logger.warning("drive correlation: %s", warning)
        logger.warning(
            "DRIVE ORDER UNVERIFIED: mapping Data Transfer Element N -> %s by position. "
            "With more than one drive this can write to the WRONG drive. Set "
            "OPENBLADE_DRIVE_SERIAL_MAP to check it; observed serials: %s",
            list(devices),
            _format_serials(live),
        )
        return DriveCorrelation(
            entries=tuple(
                DriveCorrelationEntry(drive_id=index, device=device, serial=live.get(device, ""))
                for index, device in enumerate(devices)
            ),
            source=SOURCE_POSITIONAL,
            serials_verified=False,
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
        "drive correlation applied from OPENBLADE_DRIVE_SERIAL_MAP (declared serials match "
        "the attached drives; element assignment is operator-declared and cannot be "
        "machine-checked): %s",
        ", ".join(f"DTE {entry.drive_id} -> {entry.device} ({entry.serial})" for entry in entries),
    )
    return DriveCorrelation(
        entries=entries,
        source=SOURCE_SERIAL_MAP,
        serials_verified=True,
        warnings=tuple(warnings),
    )


def _normalize_serial(serial: str) -> str:
    """Fold a serial for comparison across sources (SCSI INQUIRY vs a library UI)."""
    return serial.strip().casefold()


def verify_against_library_serials(
    *,
    correlation: DriveCorrelation,
    library_serials: Collection[str] | None,
    source: str = "the library",
) -> tuple[str, ...]:
    """Cross-check operator-declared serials against the serials a library reports.

    This is the third check available to a backend that can ask the library which
    drives it has (the AML Web Services backend can; ``mtx`` cannot). It catches
    the configuration error the local ``sg_inq`` check cannot see: a declaration
    that matches the drives cabled to this host but belongs to a *different*
    library than the one the controller is talking to.

    What it can and cannot conclude — and why a disjoint result is not a refusal:

    * **Partial overlap → refuse.** If some declared serials appear in the
      library's list and others do not, the two sides demonstrably report serials
      in the same form, so a missing one is a real disagreement.
    * **No overlap → warn.** A library UI and a SCSI ``Unit serial number`` do not
      always spell the same drive the same way (vendor prefixes, padding). With
      zero overlap we cannot distinguish "different library" from "different
      spelling", and refusing on a formatting difference would break a correct
      installation. The binding check remains the live ``sg_inq`` comparison.
    * **Nothing reported / unreadable → warn.** Absence of evidence only.

    Returns the warnings to log; raises :class:`DriveCorrelationError` on refusal.
    """
    declared = {
        _normalize_serial(entry.serial) for entry in correlation.entries if entry.serial.strip()
    }
    if not declared:
        return (f"no drive serials to cross-check against {source}",)
    if library_serials is None:
        return (f"{source} drive list could not be read, so serials were not cross-checked",)

    reported = {_normalize_serial(serial) for serial in library_serials if serial.strip()}
    if not reported:
        return (f"{source} reported no drive serial numbers, so serials were not cross-checked",)

    missing = sorted(declared - reported)
    if missing and len(missing) == len(declared):
        return (
            f"NOT cross-checked: none of the declared drive serials appear in {source}'s "
            f"drive list (declared {sorted(declared)}, reported {sorted(reported)}). Either "
            "the two sides spell serials differently — harmless — or this controller is "
            "driving a different library than the drives attached to this host, which would "
            "load cartridges into elements whose drives are not here. Nothing distinguishes "
            "the two from here, so the declaration stands unchecked: confirm the serials on "
            "the library's drive page match `openblade hardware connect-i3`.",
        )
    if missing:
        raise DriveCorrelationError(
            "OPENBLADE_DRIVE_SERIAL_MAP declares drive serial(s) that "
            f"{source} does not report: {missing}. "
            f"Declared: {sorted(declared)}. Reported by {source}: {sorted(reported)}. "
            "Other declared serials do match, so this is a real disagreement and not a "
            "formatting difference. Refusing to start rather than guess which drive is which."
        )
    return ()


def _refuse_on_duplicate_serials(live: Mapping[str, str]) -> None:
    values = [serial for serial in live.values() if serial]
    duplicates = sorted({serial for serial in values if values.count(serial) > 1})
    if duplicates:
        raise DriveCorrelationError(
            f"Two or more configured devices report the same unit serial number {duplicates}; "
            f"they are the same physical drive. Observed: {_format_serials(live)}."
        )


def _refuse_on_declared_element_range(
    declared: Mapping[str, int], element_count: int | None
) -> None:
    if element_count is None:
        return
    out_of_range = sorted(drive_id for drive_id in declared.values() if drive_id >= element_count)
    if out_of_range:
        raise DriveCorrelationError(
            f"OPENBLADE_DRIVE_SERIAL_MAP declares drive element(s) {out_of_range} but the "
            f"changer reports only {element_count} Data Transfer Element(s) "
            f"(0..{element_count - 1}). Note the map uses 0-based element indices, while "
            "the i3 web UI numbers drive bays from 1."
        )


def _refuse_on_mismatch(
    *,
    devices: Sequence[str],
    live: Mapping[str, str],
    declared: Mapping[str, int],
    element_count: int | None,
) -> None:
    """Raise DriveCorrelationError unless the declared serials match the attached ones."""
    blank = sorted(device for device in devices if not live.get(device))
    if blank:
        raise DriveCorrelationError(
            "OPENBLADE_DRIVE_SERIAL_MAP is set but these devices report no unit serial "
            f"number, so the mapping cannot be checked: {blank}. Check `sg_inq <device>` "
            "and drive permissions."
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

    _refuse_on_declared_element_range(declared, element_count)


def _format_serials(live: Mapping[str, str]) -> str:
    return ", ".join(f"{device}={serial or '<none>'}" for device, serial in sorted(live.items()))
