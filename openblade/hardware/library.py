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
        object.__setattr__(self, "library_id", changer_device.removeprefix("/dev/").replace("/", "-"))
        # Drive correlation runs at construction so a mapping that disagrees with
        # the attached hardware refuses here, before any load/write can target the
        # wrong drive.
        object.__setattr__(
            self,
            "correlation",
            correlate_drives(
                devices=_configured_drive_devices(config, active_discovery),
                serial_map=config.drive_serial_map,
                runner=active_runner,
                guard=guard,
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
                    drive_state=_drive_state(drive.loaded, mount_states.get(drive.drive_id, MountState.UNMOUNTED)),
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
        barcodes = [
            str(slot.barcode)
            for slot in inventory.slots
            if slot.barcode is not None
        ]
        barcodes.extend(str(drive.barcode) for drive in inventory.drives if drive.barcode is not None)
        return sorted(set(barcodes))

    def get_cartridge_state(self, barcode: str) -> CartridgeState | None:
        normalized = Barcode(barcode).value
        for drive in self.inventory().drives:
            if drive.barcode is not None and drive.barcode.value == normalized:
                return CartridgeState.CLEANING if normalized.startswith("CLN") else CartridgeState.IN_DRIVE
        for slot in self.inventory().slots:
            if slot.barcode is not None and slot.barcode.value == normalized:
                return CartridgeState.CLEANING if normalized.startswith("CLN") else CartridgeState.IN_SLOT
        return None

    def list_tapes(self) -> list[dict[str, str | int]]:
        return [
            {"slotId": slot.slot_id, "barcode": str(slot.barcode)}
            for slot in self.inventory().slots
            if slot.barcode is not None
        ]

    def drive_device(self, drive_id: int) -> str:
        """Host device for a library drive element, via verified correlation."""
        return self.correlation.device_for(drive_id)


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


def _ordered_drive_devices(discovery: LibraryDiscovery) -> list[str]:
    devices: list[str] = []
    for drive in sorted(discovery.drives, key=lambda item: (item.host, item.bus, item.target, item.lun)):
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
