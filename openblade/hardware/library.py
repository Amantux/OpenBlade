from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from openblade.config import OpenBladeConfig
from openblade.domain.models import (
    Barcode,
    CartridgeState,
    ChangerState,
    DriveState,
    DriveStatus,
    LibraryInventory,
    MountState,
    OperationResult,
    SlotState,
)
from openblade.domain.policies import RealHardwareGuard
from openblade.domain.states import validate_mount_transition
from openblade.hardware.correlation import DriveCorrelation, correlate_drives
from openblade.hardware.discovery import LibraryDiscovery, discover_library
from openblade.hardware.mtx import MtxChangerBackend
from openblade.hardware.runner import SafeRunner
from openblade.hardware.safety import require_real_hardware


@dataclass(frozen=True)
class RealLibraryBackend:
    """Guarded adapter for a real tape library changer."""

    changer: MtxChangerBackend
    discovery: LibraryDiscovery
    library_id: str
    correlation: DriveCorrelation

    def __init__(
        self,
        *,
        config: OpenBladeConfig,
        runner: SafeRunner | None = None,
        discovery: LibraryDiscovery | None = None,
        changer: MtxChangerBackend | None = None,
    ) -> None:
        guard = require_real_hardware(config)
        active_runner = runner or SafeRunner(dry_run=config.hardware_dry_run)
        active_discovery = discovery or discover_library(active_runner, guard)
        changer_device = config.changer_device or _resolve_changer_device(active_discovery)
        active_changer = changer or MtxChangerBackend(
            device=changer_device, guard=guard, runner=active_runner
        )
        object.__setattr__(self, "changer", active_changer)
        object.__setattr__(self, "discovery", active_discovery)
        object.__setattr__(
            self, "library_id", changer_device.removeprefix("/dev/").replace("/", "-")
        )
        # Drive correlation runs at construction so a mapping that disagrees with
        # the attached hardware refuses here, before any load/write can target the
        # wrong drive.
        object.__setattr__(
            self,
            "correlation",
            build_drive_correlation(
                config=config,
                runner=active_runner,
                guard=guard,
                discovery=active_discovery,
                element_count=active_changer.inventory().drive_count or None,
            ),
        )
        object.__setattr__(self, "_mount_states", {})

    def inventory(self) -> LibraryInventory:
        status = self.changer.inventory()
        mount_states = cast(dict[int, MountState], self._mount_states)
        return LibraryInventory(
            library_id=self.library_id,
            slots=[
                SlotState(
                    slot_id=slot.slot_id,
                    barcode=Barcode(slot.barcode) if slot.barcode else None,
                    occupied=slot.occupied,
                )
                for slot in status.slots
            ],
            drives=[
                DriveStatus(
                    drive_id=drive.drive_id,
                    barcode=Barcode(drive.barcode) if drive.barcode else None,
                    drive_state=_drive_state(
                        drive.loaded, mount_states.get(drive.drive_id, MountState.UNMOUNTED)
                    ),
                    mount_state=mount_states.get(drive.drive_id, MountState.UNMOUNTED),
                )
                for drive in status.drives
            ],
            changer_state=ChangerState.IDLE,
        )

    def load(self, source_slot: int, drive_id: int) -> OperationResult:
        result = self.changer.load(source_slot, drive_id)
        if result.success:
            self._mount_states[drive_id] = MountState.UNMOUNTED
        return result

    def unload(self, drive_id: int, target_slot: int) -> OperationResult:
        result = self.changer.unload(drive_id, target_slot)
        if result.success:
            self._mount_states[drive_id] = MountState.UNMOUNTED
        return result

    def move(self, source_slot: int, target_slot: int) -> OperationResult:
        return self.changer.move(source_slot, target_slot)

    def get_drive(self, drive_id: int) -> DriveStatus:
        inventory = self.inventory()
        for drive in inventory.drives:
            if drive.drive_id == drive_id:
                return drive
        raise KeyError(f"Unknown drive {drive_id}")

    def get_slot(self, slot_id: int) -> SlotState:
        inventory = self.inventory()
        for slot in inventory.slots:
            if slot.slot_id == slot_id:
                return slot
        raise KeyError(f"Unknown slot {slot_id}")

    def find_slot_by_barcode(self, barcode: str) -> int | None:
        normalized = Barcode(barcode).value
        for slot in self.inventory().slots:
            if slot.barcode is not None and slot.barcode.value == normalized:
                return slot.slot_id
        return None

    def find_drive_by_barcode(self, barcode: str) -> int | None:
        normalized = Barcode(barcode).value
        for drive in self.inventory().drives:
            if drive.barcode is not None and drive.barcode.value == normalized:
                return drive.drive_id
        return None

    def set_drive_mount_state(self, drive_id: int, mount_state: MountState) -> None:
        current = self._mount_states.get(drive_id, MountState.UNMOUNTED)
        validate_mount_transition(current, mount_state)
        self._mount_states[drive_id] = mount_state

    def get_all_barcodes(self) -> list[str]:
        inventory = self.inventory()
        barcodes = [str(slot.barcode) for slot in inventory.slots if slot.barcode is not None]
        barcodes.extend(
            str(drive.barcode) for drive in inventory.drives if drive.barcode is not None
        )
        return sorted(set(barcodes))

    def get_cartridge_state(self, barcode: str) -> CartridgeState | None:
        normalized = Barcode(barcode).value
        for drive in self.inventory().drives:
            if drive.barcode is not None and drive.barcode.value == normalized:
                return (
                    CartridgeState.CLEANING
                    if normalized.startswith("CLN")
                    else CartridgeState.IN_DRIVE
                )
        for slot in self.inventory().slots:
            if slot.barcode is not None and slot.barcode.value == normalized:
                return (
                    CartridgeState.CLEANING
                    if normalized.startswith("CLN")
                    else CartridgeState.IN_SLOT
                )
        return None

    def import_export_slots(self) -> list[SlotState]:
        """Import/export (mailslot) elements as mtx reports them.

        Element numbers round-trip verbatim -- the i3 numbers its I/E station
        after the storage slots (9-12 on the rehearsal rig) and other libraries
        use high element addresses (768+). Nothing here may renumber them.
        """
        return [
            SlotState(
                slot_id=slot.slot_id,
                barcode=Barcode(slot.barcode) if slot.barcode else None,
                occupied=slot.occupied,
            )
            for slot in self.changer.inventory().import_export_slots
        ]

    def import_cartridge(self, ie_slot: int, target_slot: int) -> OperationResult:
        """Move media from an import/export element into a storage slot."""
        return self.changer.move(ie_slot, target_slot)

    def export_cartridge_to_ie(self, source_slot: int, ie_slot: int) -> OperationResult:
        """Move media from a storage slot into an import/export element."""
        return self.changer.move(source_slot, ie_slot)

    def list_tapes(self) -> list[dict[str, str | int]]:
        return [
            {"slotId": slot.slot_id, "barcode": str(slot.barcode)}
            for slot in self.inventory().slots
            if slot.barcode is not None
        ]

    def drive_device(self, drive_id: int) -> str:
        """Host device for a library drive element, via verified correlation."""
        return self.correlation.device_for(drive_id)


def build_drive_correlation(
    *,
    config: OpenBladeConfig,
    runner: SafeRunner,
    guard: RealHardwareGuard,
    discovery: LibraryDiscovery,
    element_count: int | None = None,
) -> DriveCorrelation:
    """Correlate library drive elements with host tape devices for ``config``.

    One place where "which devices, probed through which nodes" is decided, shared
    by every real backend.

    ``element_count`` is what lets ``correlate_drives`` refuse a declared element
    outside the changer's range, and refuse a changer element with no host device
    (which would strand a cartridge). The SCSI backend passes the changer's count.
    The AML Web Services backend passes nothing here — it cannot, because the
    correlation is built lazily without a session — and instead makes a strictly
    stronger check of its own once it has one: it requires the declared element
    ids to equal the library's actual element ADDRESSES, not merely to be the
    right count. See ``ScalarHttpLibraryBackend._refuse_on_element_address_mismatch``.
    """
    drive_devices = _configured_drive_devices(config, discovery)
    return correlate_drives(
        devices=drive_devices,
        serial_map=config.drive_serial_map,
        runner=runner,
        guard=guard,
        element_count=element_count,
        probe_devices=_sg_probe_devices(drive_devices, discovery),
    )


def _resolve_changer_device(discovery: LibraryDiscovery) -> str:
    if not discovery.changers:
        raise RuntimeError("No tape changer was discovered")
    changer = discovery.changers[0]
    for candidate in (changer.sg_device, changer.block_device):
        if candidate:
            return candidate
    raise RuntimeError("Discovered changer does not expose a usable device path")


def _configured_drive_devices(config: OpenBladeConfig, discovery: LibraryDiscovery) -> list[str]:
    """Devices to drive, preferring the operator's explicit list over discovery.

    ``OPENBLADE_DRIVE_DEVICES`` is authoritative when set (the bring-up runbook
    requires setting it explicitly on first contact); otherwise fall back to
    SCSI-address-ordered auto-discovery, whose order is an assumption, not a fact.
    """
    if config.drive_devices:
        return list(config.drive_devices)
    return _ordered_drive_devices(discovery)


def _sg_probe_devices(devices: list[str], discovery: LibraryDiscovery) -> dict[str, str]:
    """Map each drive device to the generic ``/dev/sgN`` node to inquire against.

    The ``st`` driver allows a single open, so running ``sg_inq`` on ``/dev/nstN``
    while LTFS holds that drive fails with EBUSY; the ``sg`` node always answers.
    Devices discovery cannot place map to themselves (inquiry then uses the node
    the operator gave us, which is still better than not checking at all).
    """
    probes: dict[str, str] = {}
    for device in devices:
        rewinding = (
            device.replace("/dev/nst", "/dev/st") if device.startswith("/dev/nst") else device
        )
        for drive in discovery.drives:
            known = {value for value in (drive.block_device, drive.sg_device) if value}
            if (device in known or rewinding in known) and drive.sg_device:
                probes[device] = drive.sg_device
                break
    return probes


def _ordered_drive_devices(discovery: LibraryDiscovery) -> list[str]:
    devices: list[str] = []
    for drive in sorted(
        discovery.drives, key=lambda item: (item.host, item.bus, item.target, item.lun)
    ):
        for candidate in (drive.block_device, drive.sg_device):
            if candidate:
                devices.append(candidate)
                break
    return devices


def _drive_state(loaded: bool, mount_state: MountState) -> DriveState:
    if not loaded:
        return DriveState.EMPTY
    if mount_state in {MountState.MOUNTED_RO, MountState.MOUNTED_RW, MountState.DIRTY}:
        return DriveState.BUSY
    return DriveState.LOADED
