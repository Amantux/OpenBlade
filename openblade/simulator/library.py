from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any

import structlog

from openblade.domain.errors import (
    ChangerBusyError,
    DriveOccupiedError,
    SimulatedRobotTimeout,
    SlotEmptyError,
    SlotOccupiedError,
    TapeMountedError,
)
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
from openblade.domain.states import (
    can_unload_drive,
    validate_cartridge_transition,
    validate_drive_transition,
    validate_mount_transition,
)
from openblade.simulator.changer import MockChanger
from openblade.simulator.drive import MockDrive
from openblade.simulator.faults import FaultConfig, FaultType

logger = structlog.get_logger(__name__)


@dataclass
class _Slot:
    slot_id: int
    barcode: Barcode | None = None


class MockLibraryBackend:
    """In-memory mock of a tape library."""

    def __init__(
        self,
        library_id: str = "mock-i3-001",
        num_slots: int = 20,
        num_drives: int = 1,
        load_delay: float = 0.0,
        fault_config: FaultConfig | None = None,
    ) -> None:
        self.library_id = library_id
        self.load_delay = load_delay
        self.fault_config = fault_config or FaultConfig.no_faults()
        self._lock = threading.RLock()
        self._changer_lock = threading.Lock()
        self._slots: dict[int, _Slot] = {
            slot_id: _Slot(slot_id) for slot_id in range(1, num_slots + 1)
        }
        self._drives: dict[int, MockDrive] = {
            drive_id: MockDrive(drive_id) for drive_id in range(num_drives)
        }
        self._cartridge_states: dict[str, CartridgeState] = {}
        self._changer = MockChanger()

    def reset(self) -> None:
        with self._lock:
            for slot in self._slots.values():
                slot.barcode = None
            for drive in self._drives.values():
                drive.barcode = None
                drive.drive_state = DriveState.EMPTY
                drive.mount_state = MountState.UNMOUNTED
            self._cartridge_states.clear()
            self._changer.state = ChangerState.IDLE

    def seed_slots(self, barcodes: list[str]) -> None:
        self.reset()
        for slot_id, barcode in zip(self._slots, barcodes, strict=False):
            self.add_cartridge(slot_id, barcode)

    def add_cartridge(self, slot_id: int, barcode: str) -> None:
        with self._lock:
            slot = self._slots[slot_id]
            if slot.barcode is not None:
                raise SlotOccupiedError(f"Slot {slot_id} is occupied")
            normalized = Barcode(barcode)
            if self._location_count_locked(normalized.value) > 0:
                raise SlotOccupiedError(f"Cartridge {normalized} already exists in the library")
            previous_state = self._cartridge_states.get(normalized.value)
            if previous_state is not None:
                validate_cartridge_transition(previous_state, CartridgeState.IN_SLOT)
            slot.barcode = normalized
            self._cartridge_states[normalized.value] = CartridgeState.IN_SLOT
            self._validate_invariants_locked()

    def remove_cartridge(self, slot_id: int) -> str:
        with self._lock:
            slot = self._slots[slot_id]
            if slot.barcode is None:
                raise SlotEmptyError(f"Slot {slot_id} is empty")
            barcode = slot.barcode.value
            slot.barcode = None
            self._cartridge_states.pop(barcode, None)
            self._validate_invariants_locked()
            return barcode

    def inventory(self) -> LibraryInventory:
        with self._lock:
            return LibraryInventory(
                library_id=self.library_id,
                slots=[
                    SlotState(
                        slot_id=slot.slot_id,
                        barcode=slot.barcode,
                        occupied=slot.barcode is not None,
                    )
                    for slot in self._slots.values()
                ],
                drives=[
                    DriveStatus(
                        drive_id=drive.drive_id,
                        barcode=drive.barcode,
                        drive_state=drive.drive_state,
                        mount_state=drive.mount_state,
                    )
                    for drive in self._drives.values()
                ],
                changer_state=self._changer.state,
            )

    def list_tapes(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {"slotId": slot.slot_id, "barcode": slot.barcode.value}
                for slot in self._slots.values()
                if slot.barcode is not None
            ]

    def to_json(self) -> dict[str, Any]:
        with self._lock:
            return {
                "library_id": self.library_id,
                "load_delay": self.load_delay,
                "fault_config": self.fault_config.to_json(),
                "changer_state": self._changer.state.value,
                "slots": [
                    {
                        "slot_id": slot.slot_id,
                        "barcode": slot.barcode.value if slot.barcode is not None else None,
                    }
                    for slot in self._slots.values()
                ],
                "drives": [
                    {
                        "drive_id": drive.drive_id,
                        "barcode": drive.barcode.value if drive.barcode is not None else None,
                        "drive_state": drive.drive_state.value,
                        "mount_state": drive.mount_state.value,
                    }
                    for drive in self._drives.values()
                ],
                "cartridge_states": {
                    barcode: state.value for barcode, state in self._cartridge_states.items()
                },
            }

    @classmethod
    def from_json(cls, payload: dict[str, Any] | str) -> MockLibraryBackend:
        raw = json.loads(payload) if isinstance(payload, str) else payload
        backend = cls(
            library_id=str(raw["library_id"]),
            num_slots=len(raw["slots"]),
            num_drives=len(raw["drives"]),
            load_delay=float(raw.get("load_delay", 0.0)),
            fault_config=FaultConfig.from_json(raw.get("fault_config")),
        )
        with backend._lock:
            backend._changer.state = ChangerState(
                str(raw.get("changer_state", ChangerState.IDLE.value))
            )
            backend._slots = {
                int(item["slot_id"]): _Slot(
                    slot_id=int(item["slot_id"]),
                    barcode=Barcode(item["barcode"]) if item.get("barcode") else None,
                )
                for item in raw["slots"]
            }
            backend._drives = {
                int(item["drive_id"]): MockDrive(
                    drive_id=int(item["drive_id"]),
                    barcode=Barcode(item["barcode"]) if item.get("barcode") else None,
                    drive_state=DriveState(str(item["drive_state"])),
                    mount_state=MountState(str(item["mount_state"])),
                )
                for item in raw["drives"]
            }
            backend._cartridge_states = {
                barcode: CartridgeState(state)
                for barcode, state in raw.get("cartridge_states", {}).items()
            }
            for slot in backend._slots.values():
                if slot.barcode is not None:
                    backend._cartridge_states.setdefault(slot.barcode.value, CartridgeState.IN_SLOT)
            for drive in backend._drives.values():
                if drive.barcode is not None:
                    backend._cartridge_states.setdefault(
                        drive.barcode.value, CartridgeState.IN_DRIVE
                    )
            backend._validate_invariants_locked()
        return backend

    def load(self, source_slot: int, drive_id: int) -> OperationResult:
        self._enter_changer()
        try:
            self._maybe_fail(
                FaultType.LOAD_FAILURE,
                FaultType.CHANGER_TIMEOUT,
                FaultType.DRIVE_TIMEOUT,
            )
            time.sleep(self.load_delay)
            with self._lock:
                slot = self._slots[source_slot]
                drive = self._drives[drive_id]
                if slot.barcode is None:
                    raise SlotEmptyError(f"Slot {source_slot} is empty")
                if drive.barcode is not None:
                    raise DriveOccupiedError(f"Drive {drive_id} already contains {drive.barcode}")
                barcode = slot.barcode
                current_state = self._cartridge_states.get(barcode.value, CartridgeState.IN_SLOT)
                validate_cartridge_transition(current_state, CartridgeState.IN_DRIVE)
                validate_drive_transition(drive.drive_state, DriveState.LOADED)
                slot.barcode = None
                drive.barcode = barcode
                drive.drive_state = DriveState.LOADED
                drive.mount_state = MountState.UNMOUNTED
                self._cartridge_states[barcode.value] = CartridgeState.IN_DRIVE
                self._validate_invariants_locked()
                logger.info(
                    "loaded cartridge",
                    slot=source_slot,
                    drive=drive_id,
                    barcode=str(barcode),
                )
                return OperationResult(True, "loaded", {"barcode": str(barcode), "drive": drive_id})
        finally:
            self._leave_changer()

    def unload(self, drive_id: int, target_slot: int) -> OperationResult:
        self._enter_changer()
        try:
            self._maybe_fail(
                FaultType.UNLOAD_FAILURE,
                FaultType.CHANGER_TIMEOUT,
                FaultType.DRIVE_TIMEOUT,
            )
            time.sleep(self.load_delay)
            with self._lock:
                drive = self._drives[drive_id]
                slot = self._slots[target_slot]
                if drive.barcode is None:
                    raise SlotEmptyError(f"Drive {drive_id} is empty")
                if slot.barcode is not None:
                    raise SlotOccupiedError(f"Slot {target_slot} is occupied")
                if not can_unload_drive(drive.drive_state, drive.mount_state):
                    raise TapeMountedError(
                        f"Drive {drive_id} cannot be unloaded while {drive.mount_state.value}"
                    )
                barcode = drive.barcode
                current_state = self._cartridge_states.get(barcode.value, CartridgeState.IN_DRIVE)
                validate_cartridge_transition(current_state, CartridgeState.IN_SLOT)
                validate_drive_transition(drive.drive_state, DriveState.EMPTY)
                drive.barcode = None
                drive.drive_state = DriveState.EMPTY
                drive.mount_state = MountState.UNMOUNTED
                slot.barcode = barcode
                self._cartridge_states[barcode.value] = CartridgeState.IN_SLOT
                self._validate_invariants_locked()
                return OperationResult(
                    True,
                    "unloaded",
                    {"barcode": str(barcode), "slot": target_slot},
                )
        finally:
            self._leave_changer()

    def move(self, source_slot: int, target_slot: int) -> OperationResult:
        self._enter_changer()
        try:
            if self.fault_config.should_fail(FaultType.MOVE_FAILURE):
                return OperationResult(
                    False,
                    "Injected move failure",
                    {"source": source_slot, "target": target_slot},
                )
            self._maybe_fail(FaultType.CHANGER_TIMEOUT)
            time.sleep(self.load_delay)
            with self._lock:
                source = self._slots[source_slot]
                target = self._slots[target_slot]
                if source.barcode is None:
                    raise SlotEmptyError(f"Slot {source_slot} is empty")
                if target.barcode is not None:
                    raise SlotOccupiedError(f"Slot {target_slot} is occupied")
                barcode = source.barcode
                current_state = self._cartridge_states.get(barcode.value, CartridgeState.IN_SLOT)
                validate_cartridge_transition(current_state, CartridgeState.IN_SLOT)
                target.barcode = barcode
                source.barcode = None
                self._validate_invariants_locked()
                return OperationResult(
                    True,
                    "moved",
                    {"source": source_slot, "target": target_slot, "barcode": str(barcode)},
                )
        finally:
            self._leave_changer()

    def get_drive(self, drive_id: int) -> DriveStatus:
        with self._lock:
            drive = self._drives[drive_id]
            return DriveStatus(
                drive_id=drive.drive_id,
                barcode=drive.barcode,
                drive_state=drive.drive_state,
                mount_state=drive.mount_state,
            )

    def get_slot(self, slot_id: int) -> SlotState:
        with self._lock:
            slot = self._slots[slot_id]
            return SlotState(
                slot_id=slot.slot_id,
                barcode=slot.barcode,
                occupied=slot.barcode is not None,
            )

    def get_cartridge_state(self, barcode: str) -> CartridgeState | None:
        with self._lock:
            return self._cartridge_states.get(Barcode(barcode).value)

    def find_slot_by_barcode(self, barcode: str) -> int | None:
        normalized = Barcode(barcode).value
        with self._lock:
            for slot_id, slot in self._slots.items():
                if slot.barcode is not None and slot.barcode.value == normalized:
                    return slot_id
        return None

    def find_drive_by_barcode(self, barcode: str) -> int | None:
        normalized = Barcode(barcode).value
        with self._lock:
            for drive_id, drive in self._drives.items():
                if drive.barcode is not None and drive.barcode.value == normalized:
                    return drive_id
        return None

    def set_drive_mount_state(self, drive_id: int, mount_state: MountState) -> None:
        with self._lock:
            drive = self._drives[drive_id]
            validate_mount_transition(drive.mount_state, mount_state)
            if mount_state in {MountState.MOUNTED_RO, MountState.MOUNTED_RW, MountState.DIRTY}:
                validate_drive_transition(drive.drive_state, DriveState.BUSY)
                drive.drive_state = DriveState.BUSY
            elif drive.barcode is None:
                validate_drive_transition(drive.drive_state, DriveState.EMPTY)
                drive.drive_state = DriveState.EMPTY
            else:
                validate_drive_transition(drive.drive_state, DriveState.LOADED)
                drive.drive_state = DriveState.LOADED
            drive.mount_state = mount_state
            self._validate_invariants_locked()

    def export_cartridge(self, barcode: str) -> None:
        with self._lock:
            for slot in self._slots.values():
                if slot.barcode is not None and slot.barcode.value == barcode:
                    slot.barcode = None
                    self._cartridge_states[barcode] = CartridgeState.EXPORTED
                    return
            for drive in self._drives.values():
                if drive.barcode is not None and drive.barcode.value == barcode:
                    drive.barcode = None
                    drive.drive_state = DriveState.EMPTY
                    drive.mount_state = MountState.UNMOUNTED
                    self._cartridge_states[barcode] = CartridgeState.EXPORTED
                    return
            if barcode in self._cartridge_states:
                self._cartridge_states[barcode] = CartridgeState.EXPORTED

    def get_all_barcodes(self) -> list[str]:
        with self._lock:
            return sorted(self._cartridge_states)

    def _enter_changer(self) -> None:
        if self.fault_config.should_fail(FaultType.ROBOT_LOCK_CONFLICT):
            raise ChangerBusyError("Injected changer lock conflict")
        if not self._changer_lock.acquire(blocking=False):
            raise ChangerBusyError("Changer is already moving media")
        self._changer.state = ChangerState.MOVING

    def _leave_changer(self) -> None:
        self._changer.state = ChangerState.IDLE
        self._changer_lock.release()

    def _maybe_fail(self, *faults: FaultType) -> None:
        if any(self.fault_config.should_fail(fault) for fault in faults):
            raise SimulatedRobotTimeout(
                f"Injected simulator fault: {', '.join(fault.value for fault in faults)}"
            )

    def _location_count_locked(self, barcode: str) -> int:
        return sum(
            1
            for slot in self._slots.values()
            if slot.barcode is not None and slot.barcode.value == barcode
        ) + sum(
            1
            for drive in self._drives.values()
            if drive.barcode is not None and drive.barcode.value == barcode
        )

    def _validate_invariants_locked(self) -> None:
        locations: dict[str, list[str]] = {}
        for slot_id, slot in self._slots.items():
            if slot.barcode is not None:
                locations.setdefault(slot.barcode.value, []).append(f"slot:{slot_id}")
        for drive_id, drive in self._drives.items():
            if drive.barcode is not None:
                locations.setdefault(drive.barcode.value, []).append(f"drive:{drive_id}")
        for barcode, barcode_locations in locations.items():
            if len(barcode_locations) != 1:
                raise ValueError(f"Cartridge {barcode} has invalid locations: {barcode_locations}")
        for barcode, state in self._cartridge_states.items():
            location_count = len(locations.get(barcode, []))
            if (
                state
                in {
                    CartridgeState.IN_SLOT,
                    CartridgeState.IN_DRIVE,
                    CartridgeState.CLEANING,
                }
                and location_count != 1
            ):
                raise ValueError(
                    f"Cartridge {barcode} in state {state.value} must exist in exactly one location"
                )
            if state in {CartridgeState.EXPORTED, CartridgeState.MISSING} and location_count != 0:
                raise ValueError(
                    f"Cartridge {barcode} in state {state.value} must not exist in library locations"
                )
