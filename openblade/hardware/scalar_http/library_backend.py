"""``LibraryBackend`` implemented over the Quantum AML Web Services API.

Milestone 1 scope: the read/control-plane surface (inventory, drive/slot lookup)
mapped from ``GET /aml/physicalLibrary/elements``. Robotic moves (``moveMedium``)
and the ``drive_device`` host-correlation used by the LTFS data path arrive in a
later increment.
"""

from __future__ import annotations

from typing import Any

from openblade.domain.errors import OpenBladeError
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
from openblade.domain.scalar_coordinate import MoveClass
from openblade.hardware.scalar_http.errors import ScalarHttpError
from openblade.hardware.scalar_http.session import ScalarHttpSession

_DRIVE_STATES = {state.value: state for state in DriveState}


def _drive_state(value: Any) -> DriveState:
    return _DRIVE_STATES.get(str(value), DriveState.EMPTY)


def _barcode_or_none(value: Any) -> Barcode | None:
    text = str(value).strip() if value else ""
    return Barcode(text) if text else None


class ScalarHttpLibraryBackend:
    """Reads a real Scalar i3 inventory over ``/aml`` Web Services.

    Satisfies the read half of ``openblade.domain.backends.LibraryBackend``. State
    is fetched live from the library on each call (no local cache) so the view
    always reflects the physical device.
    """

    def __init__(
        self,
        session: ScalarHttpSession,
        *,
        library_id: str = "scalar-i3",
        elements_path: str = "/aml/physicalLibrary/elements",
        move_medium_path: str = "/aml/media/operations/moveMedium",
    ) -> None:
        self._session = session
        self._library_id = library_id
        self._elements_path = elements_path
        self._move_medium_path = move_medium_path
        self._mount_states: dict[int, MountState] = {}

    @property
    def library_id(self) -> str:
        return self._library_id

    def _elements(self) -> list[dict[str, Any]]:
        body = self._session.get_json(self._elements_path)
        element_list = body.get("elementList")
        elements = element_list.get("element") if isinstance(element_list, dict) else None
        return [e for e in elements if isinstance(e, dict)] if isinstance(elements, list) else []

    def inventory(self) -> LibraryInventory:
        slots: list[SlotState] = []
        drives: list[DriveStatus] = []
        for element in self._elements():
            element_type = element.get("type")
            address = element.get("address")
            if address is None:
                continue
            if element_type == "slot":
                barcode = _barcode_or_none(element.get("barcode"))
                slots.append(
                    SlotState(slot_id=int(address), barcode=barcode, occupied=barcode is not None)
                )
            elif element_type == "drive":
                drives.append(
                    DriveStatus(
                        drive_id=int(address),
                        barcode=_barcode_or_none(element.get("barcode")),
                        drive_state=_drive_state(element.get("state")),
                        mount_state=MountState.UNMOUNTED,
                    )
                )
        return LibraryInventory(
            library_id=self._library_id,
            slots=slots,
            drives=drives,
            changer_state=ChangerState.IDLE,
        )

    def get_drive(self, drive_id: int) -> DriveStatus:
        for drive in self.inventory().drives:
            if drive.drive_id == drive_id:
                return drive
        raise OpenBladeError(f"Drive {drive_id} not found on the library")

    def get_slot(self, slot_id: int) -> SlotState:
        for slot in self.inventory().slots:
            if slot.slot_id == slot_id:
                return slot
        raise OpenBladeError(f"Slot {slot_id} not found on the library")

    def find_slot_by_barcode(self, barcode: str) -> int | None:
        target = Barcode(barcode).value
        for slot in self.inventory().slots:
            if slot.barcode is not None and slot.barcode.value == target:
                return slot.slot_id
        return None

    def find_drive_by_barcode(self, barcode: str) -> int | None:
        target = Barcode(barcode).value
        for drive in self.inventory().drives:
            if drive.barcode is not None and drive.barcode.value == target:
                return drive.drive_id
        return None

    def get_all_barcodes(self) -> list[str]:
        inventory = self.inventory()
        barcodes = [slot.barcode.value for slot in inventory.slots if slot.barcode is not None]
        barcodes.extend(
            drive.barcode.value for drive in inventory.drives if drive.barcode is not None
        )
        return barcodes

    # -- robotics (write path) ------------------------------------------------

    @staticmethod
    def _coordinate(element_type: str, address: int) -> dict[str, Any]:
        return {"elementAddress": int(address), "elementType": element_type}

    def _move_medium(
        self,
        source: dict[str, Any],
        destination: dict[str, Any] | None = None,
        *,
        move_class: int = 0,
    ) -> OperationResult:
        body: dict[str, Any] = {"sourceCoordinate": source, "moveClass": move_class}
        if destination is not None:
            body["destinationCoordinate"] = destination
        try:
            result = self._session.post_json(self._move_medium_path, json={"moveMedium": body})
        except ScalarHttpError as error:
            details = {"customCode": error.custom_code} if error.custom_code is not None else {}
            return OperationResult(success=False, message=str(error), details=details)
        message = str(result.get("description") or result.get("summary") or "moveMedium completed")
        return OperationResult(success=True, message=message)

    def load(self, source_slot: int, drive_id: int) -> OperationResult:
        return self._move_medium(
            self._coordinate("slot", source_slot), self._coordinate("drive", drive_id)
        )

    def unload(self, drive_id: int, target_slot: int) -> OperationResult:
        # Real i3 unload uses moveClass=8 (bit field) with the drive source; the
        # target slot is sent as a hint (Web Services manual). See
        # docs/reference/i3-contract-notes.md.
        return self._move_medium(
            self._coordinate("drive", drive_id),
            self._coordinate("slot", target_slot),
            move_class=MoveClass.UNLOAD.to_wire(),
        )

    def move(self, source_slot: int, target_slot: int) -> OperationResult:
        return self._move_medium(
            self._coordinate("slot", source_slot), self._coordinate("slot", target_slot)
        )

    # -- state helpers used by the LTFS data path -----------------------------

    def get_cartridge_state(self, barcode: str) -> CartridgeState:
        target = Barcode(barcode).value
        inventory = self.inventory()
        if any(d.barcode is not None and d.barcode.value == target for d in inventory.drives):
            return CartridgeState.IN_DRIVE
        if any(s.barcode is not None and s.barcode.value == target for s in inventory.slots):
            return CartridgeState.IN_SLOT
        return CartridgeState.MISSING

    def set_drive_mount_state(self, drive_id: int, state: MountState) -> None:
        # LTFS mount state is host-side (the Web Services API has no concept of it).
        # Track it in-memory so the LTFS backend can query/round-trip it.
        self._mount_states[drive_id] = state

    def drive_device(self, drive_id: int) -> str:
        # The host device path for a drive requires correlating the AML drive serial
        # to a local /dev/st device via SCSI discovery. That is the milestone-2
        # data-path hardening step; robotics/inventory (this backend) work without it.
        raise NotImplementedError(
            "drive_device requires AML-serial <-> /dev/st correlation (data-path "
            "milestone); not available for the control-plane Web Services backend yet"
        )
