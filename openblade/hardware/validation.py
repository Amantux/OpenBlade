from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from openblade.config import OpenBladeConfig
from openblade.domain.backends import LibraryBackend
from openblade.domain.errors import DriveCorrelationError
from openblade.domain.models import LibraryInventory
from openblade.domain.policies import DryRunPlan, RealHardwareGuard
from openblade.hardware.discovery import LibraryDiscovery, discover_library, resolve_sg_device
from openblade.hardware.library import RealLibraryBackend
from openblade.hardware.ltfs import LTFSCommandBackend, LTFSDevice
from openblade.hardware.runner import SafeRunner
from openblade.hardware.safety import require_real_hardware
from openblade.hardware.sg import SgDeviceInfo, sg_inq


@dataclass(frozen=True)
class QuantumI3ConnectionReport:
    library_id: str
    changer_device: str
    discovered_changers: list[str]
    discovered_drives: list[str]
    slot_count: int
    drive_count: int
    occupied_slot_count: int
    loaded_drive_count: int
    drive_devices: list[str]
    sg_inquiry: list[dict[str, str]]
    # Drive-element -> device binding and how it was established. Copy the serials
    # into OPENBLADE_DRIVE_SERIAL_MAP to turn an unverified positional order into a
    # verified one (see openblade/hardware/correlation.py).
    drive_correlation: list[dict[str, str | int]]
    drive_correlation_source: str
    # True only when every device was probed and its serial matched the declared
    # map. It does NOT mean the element assignment itself was machine-verified —
    # see openblade/hardware/correlation.py.
    drive_correlation_serials_verified: bool
    drive_correlation_warnings: list[str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class LTFSValidationReport:
    requested_device: str
    discovered_devices: list[dict[str, object]]
    device_list_ok: bool
    format_plan: dict[str, object]
    readonly_mount_ok: bool | None = None
    readwrite_mount_ok: bool | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def connect_quantum_i3(
    config: OpenBladeConfig,
    *,
    runner: SafeRunner | None = None,
) -> QuantumI3ConnectionReport:
    """Diagnose the real robotics backend ``config`` selects.

    Transport-aware: builds the SCSI (``mtx``) or Web Services backend the same
    way ``openblade.bootstrap`` does for a running app, so this command
    diagnoses whichever transport is actually configured -- previously it
    always built ``RealLibraryBackend`` regardless of
    ``OPENBLADE_ROBOTICS_TRANSPORT``, which crashed (or silently probed the
    wrong thing) against a Scalar reached only over webservices. Fields that
    genuinely do not exist on the Web Services backend (a local changer
    device, an up-front correlation summary -- see
    ``ScalarHttpLibraryBackend``'s docstring) are reported as not applicable
    rather than raising ``AttributeError``.
    """
    guard = require_real_hardware(config)
    active_runner = runner or SafeRunner(dry_run=config.hardware_dry_run)
    discovery = discover_library(active_runner, guard)
    library: LibraryBackend
    if config.robotics_transport == "webservices":
        from openblade.bootstrap import _create_scalar_http_library

        library = _create_scalar_http_library(config, active_runner, guard)
    else:
        library = RealLibraryBackend(config=config, runner=active_runner, discovery=discovery)
    inventory = library.inventory()
    (
        drive_correlation,
        drive_correlation_source,
        drive_correlation_serials_verified,
        drive_correlation_warnings,
    ) = _drive_correlation_report(library, inventory)
    return QuantumI3ConnectionReport(
        library_id=inventory.library_id,
        changer_device=_changer_device_or_not_applicable(library),
        discovered_changers=_changer_devices(discovery),
        discovered_drives=_drive_devices(discovery),
        slot_count=len(inventory.slots),
        drive_count=len(inventory.drives),
        occupied_slot_count=sum(1 for slot in inventory.slots if slot.occupied),
        loaded_drive_count=sum(1 for drive in inventory.drives if drive.barcode is not None),
        # An element the host has no device for is reported as "" rather than
        # raising: connect-i3 is the diagnostic you run *because* a drive is missing.
        drive_devices=[
            _drive_device_or_blank(library, drive.drive_id) for drive in inventory.drives
        ],
        sg_inquiry=_inquiry_payloads(discovery, active_runner, guard),
        drive_correlation=drive_correlation,
        drive_correlation_source=drive_correlation_source,
        drive_correlation_serials_verified=drive_correlation_serials_verified,
        drive_correlation_warnings=drive_correlation_warnings,
    )


def validate_ltfs_capabilities(
    config: OpenBladeConfig,
    *,
    device: str,
    barcode: str,
    mount_point: Path | None = None,
    exercise_mounts: bool = False,
    runner: SafeRunner | None = None,
) -> LTFSValidationReport:
    guard = require_real_hardware(config)
    active_runner = runner or SafeRunner(dry_run=config.hardware_dry_run)
    devices = LTFSCommandBackend.device_list(active_runner, guard)
    format_plan = LTFSCommandBackend.format_dry_run_plan(barcode, device)
    readonly_mount_ok: bool | None = None
    readwrite_mount_ok: bool | None = None
    if exercise_mounts:
        if mount_point is None:
            raise ValueError("mount_point is required when exercise_mounts is enabled")
        mount_point.mkdir(parents=True, exist_ok=True)
        readonly_mount = LTFSCommandBackend.mount_readonly(
            device, str(mount_point), guard, active_runner
        )
        readonly_mount_ok = readonly_mount.success
        if readonly_mount.success:
            LTFSCommandBackend.unmount(str(mount_point), guard, active_runner)
        readwrite_mount = LTFSCommandBackend.mount_readwrite(
            device, str(mount_point), guard, active_runner
        )
        readwrite_mount_ok = readwrite_mount.success
        if readwrite_mount.success:
            LTFSCommandBackend.unmount(str(mount_point), guard, active_runner)
    return LTFSValidationReport(
        requested_device=device,
        discovered_devices=[_ltfs_device_payload(current) for current in devices],
        # Compare on the SCSI generic node. LTFS only ever lists /dev/sgN, but
        # callers pass a tape node - the CLI help for this command literally
        # suggests "/dev/st0", and Phase 4.1 of the bring-up plan passes
        # /dev/nst0 - so a raw equality check reported device_list_ok=false on
        # a perfectly healthy setup, at the first gate of the bring-up.
        device_list_ok=_device_in_list(device, devices),
        format_plan=_plan_payload(format_plan),
        readonly_mount_ok=readonly_mount_ok,
        readwrite_mount_ok=readwrite_mount_ok,
    )


def _drive_device_or_blank(library: LibraryBackend, drive_id: int) -> str:
    """``library.drive_device`` if this backend has one, else "".

    Both real backends (``RealLibraryBackend``, ``ScalarHttpLibraryBackend``)
    implement ``drive_device``; the ``LibraryBackend`` protocol does not
    declare it (the simulator has no host devices to correlate), so this is a
    duck-typed capability check rather than an isinstance branch on a
    specific backend class.
    """
    drive_device = getattr(library, "drive_device", None)
    if drive_device is None:
        return ""
    try:
        return str(drive_device(drive_id))
    except DriveCorrelationError:
        return ""


def _changer_device_or_not_applicable(library: LibraryBackend) -> str:
    """``library.changer.device`` if this backend has a local changer.

    The Web Services backend (``OPENBLADE_ROBOTICS_TRANSPORT=webservices``)
    drives the changer over HTTP and has no local device to report -- see
    ``ScalarHttpLibraryBackend``, which has no ``changer`` attribute at all.
    """
    changer = getattr(library, "changer", None)
    if changer is not None:
        return str(changer.device)
    return (
        "not applicable on webservices transport (the changer is driven over "
        "AML Web Services; there is no local changer device)"
    )


def _drive_correlation_report(
    library: LibraryBackend, inventory: LibraryInventory
) -> tuple[list[dict[str, str | int]], str, bool, list[str]]:
    """(drive_correlation, source, serials_verified, warnings) for the report.

    ``RealLibraryBackend`` builds and caches a ``DriveCorrelation`` up front
    (``library.correlation``); ``ScalarHttpLibraryBackend`` has no such
    aggregate object by design (its class docstring: correlation is resolved
    and verified lazily, per drive, the first time ``drive_device()`` is
    called) -- so that summary is genuinely not applicable for that
    transport, not merely unimplemented. Per-drive devices are still reported
    via ``drive_devices``/``_drive_device_or_blank``, which already exercises
    -- and reports the refusal from -- that per-drive verification.
    """
    correlation = getattr(library, "correlation", None)
    if correlation is not None:
        return (
            correlation.to_payload(),
            correlation.source,
            correlation.serials_verified,
            list(correlation.warnings),
        )
    payload: list[dict[str, str | int]] = [
        {"driveId": drive.drive_id, "device": device, "serial": ""}
        for drive in inventory.drives
        if (device := _drive_device_or_blank(library, drive.drive_id))
    ]
    warnings = [
        "drive correlation summary (source/serials_verified) is not applicable "
        "on the webservices transport; each drive_devices entry already went "
        "through ScalarHttpLibraryBackend.drive_device()'s own "
        "OPENBLADE_DRIVE_SERIAL_MAP verification, and refuses per-drive on a "
        "mismatch rather than reporting one here."
    ]
    return payload, "not_applicable", False, warnings


def _changer_devices(discovery: LibraryDiscovery) -> list[str]:
    devices: list[str] = []
    for changer in discovery.changers:
        for candidate in (changer.sg_device, changer.block_device):
            if candidate:
                devices.append(candidate)
                break
    return devices


def _drive_devices(discovery: LibraryDiscovery) -> list[str]:
    devices: list[str] = []
    for drive in discovery.drives:
        for candidate in (drive.block_device, drive.sg_device):
            if candidate:
                devices.append(candidate)
                break
    return devices


def _inquiry_payloads(
    discovery: LibraryDiscovery, runner: SafeRunner, guard: RealHardwareGuard
) -> list[dict[str, str]]:
    payloads: list[dict[str, str]] = []
    for device in _inquiry_devices(discovery):
        inquiry = sg_inq(device, runner, guard)
        payloads.append(
            asdict(
                SgDeviceInfo(
                    device=device,
                    inquiry=inquiry,
                )
            )
        )
    return payloads


def _inquiry_devices(discovery: LibraryDiscovery) -> list[str]:
    devices: list[str] = []
    for element in [*discovery.changers, *discovery.drives]:
        if element.sg_device:
            devices.append(element.sg_device)
    return devices


def _device_in_list(device: str, devices: list[LTFSDevice]) -> bool:
    """Is ``device`` one of the drives LTFS enumerated?

    LTFS lists SCSI generic nodes (/dev/sgN); callers name a tape node
    (/dev/stN or /dev/nstN). Resolve both sides before comparing, and keep the
    literal comparison too so an already-sg argument still matches on a host
    where the sysfs mapping is unreadable.
    """
    resolved = resolve_sg_device(device)
    return any(
        current.device == device or resolve_sg_device(current.device) == resolved
        for current in devices
    )


def _ltfs_device_payload(device: LTFSDevice) -> dict[str, object]:
    return asdict(device)


def _plan_payload(plan: DryRunPlan) -> dict[str, object]:
    return {
        "operation": plan.operation,
        "target": plan.target,
        "affected_barcodes": plan.affected_barcodes,
        "warnings": plan.warnings,
        "is_destructive": plan.is_destructive,
        "estimated_duration_seconds": plan.estimated_duration_seconds,
    }
