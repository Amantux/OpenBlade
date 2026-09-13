from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from openblade.config import OpenBladeConfig
from openblade.domain.errors import DriveCorrelationError
from openblade.domain.policies import DryRunPlan
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
    guard = require_real_hardware(config)
    active_runner = runner or SafeRunner(dry_run=config.hardware_dry_run)
    discovery = discover_library(active_runner, guard)
    library = RealLibraryBackend(config=config, runner=active_runner, discovery=discovery)
    inventory = library.inventory()
    return QuantumI3ConnectionReport(
        library_id=inventory.library_id,
        changer_device=library.changer.device,
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
        drive_correlation=library.correlation.to_payload(),
        drive_correlation_source=library.correlation.source,
        drive_correlation_serials_verified=library.correlation.serials_verified,
        drive_correlation_warnings=list(library.correlation.warnings),
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


def _drive_device_or_blank(library: RealLibraryBackend, drive_id: int) -> str:
    try:
        return library.drive_device(drive_id)
    except DriveCorrelationError:
        return ""


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
    discovery: LibraryDiscovery, runner: SafeRunner, guard
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
