"""Import/export (mailslot) flows.

``MtxStatus.import_export_slots`` has been parsed since the mtx wrapper was
written and had zero consumers (docs/runbooks/real-data-campaign.md, "Does not
exist"). This is the service half: list what is in the I/E station, import a
cartridge into storage, and export one out -- each as a single audited tape
operation through ``TapeOperationOrchestrator``, which is the only thing in this
codebase allowed to move media.

Slot-picking policy lives here; the move and the guard live in the orchestrator.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from openblade.catalog.export_policy import ExportAssessment, assess_export
from openblade.catalog.repository import CatalogRepository
from openblade.domain.backends import MailslotBackend
from openblade.domain.errors import (
    CartridgeNotFoundError,
    ImportExportSlotError,
    MailslotUnsupportedError,
)
from openblade.domain.models import SlotState
from openblade.nas.tape_orchestrator import TapeOperationOrchestrator
from openblade.nas.types import TapeOpRequest, TapeOpStatus, TapeOpType

# Catalog cartridge states. "exported" is the one every restore path already
# checks; importing puts the cartridge back on the normal footing.
_STATE_EXPORTED = "exported"
_STATE_IN_SLOT = "in_slot"


@dataclass
class MailslotSlot:
    slot_id: int
    occupied: bool
    barcode: str | None

    def to_dict(self) -> dict[str, object]:
        return {"slotId": self.slot_id, "occupied": self.occupied, "barcode": self.barcode}


@dataclass
class MailslotListing:
    slots: list[MailslotSlot] = field(default_factory=list)

    @property
    def occupied(self) -> list[MailslotSlot]:
        return [slot for slot in self.slots if slot.occupied]

    def to_dict(self) -> dict[str, object]:
        return {
            "importExportSlots": [slot.to_dict() for slot in self.slots],
            "occupied": [slot.to_dict() for slot in self.occupied],
            "slotCount": len(self.slots),
            "occupiedCount": len(self.occupied),
        }


@dataclass
class MailslotMoveResult:
    op_id: str
    barcode: str
    source: str
    source_slot: int
    destination: str
    destination_slot: int
    slot_was_chosen: bool
    assessment: ExportAssessment | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "opId": self.op_id,
            "barcode": self.barcode,
            "source": self.source,
            "sourceSlot": self.source_slot,
            "destination": self.destination,
            "destinationSlot": self.destination_slot,
            # Named explicitly so the operator can see WHICH slot was picked for
            # them; "moved to a free slot" is not an answer you can act on.
            "destinationSlotChosen": self.slot_was_chosen,
        }
        if self.assessment is not None:
            payload["exported"] = self.assessment.to_dict()
        return payload


class MailslotService:
    """Operator surface over the library's import/export station."""

    def __init__(self, catalog: CatalogRepository, library: object, ltfs: object) -> None:
        self.catalog = catalog
        self.library = library
        self.ltfs = ltfs

    # -- reads ----------------------------------------------------------------

    def _mailslot_library(self) -> MailslotBackend:
        if not isinstance(self.library, MailslotBackend):
            raise MailslotUnsupportedError(
                f"{type(self.library).__name__} has no import/export station; "
                "mailslot operations are not available on this backend"
            )
        return self.library

    def list_slots(self) -> MailslotListing:
        """Every import/export element and what is in it."""
        slots = self._mailslot_library().import_export_slots()
        return MailslotListing(
            slots=[
                MailslotSlot(
                    slot_id=slot.slot_id,
                    occupied=slot.occupied,
                    barcode=None if slot.barcode is None else str(slot.barcode),
                )
                for slot in slots
            ]
        )

    def preview_export(self, barcode: str) -> ExportAssessment:
        """What would leave with ``barcode``, without moving anything."""
        return assess_export(self.catalog, barcode)

    # -- writes ---------------------------------------------------------------

    def import_cartridge(self, ie_slot: int, to_slot: int | None = None) -> MailslotMoveResult:
        """Move a cartridge out of an I/E element into a storage slot."""
        source = self._require_ie_slot(ie_slot, must_be_occupied=True)
        barcode = str(source.barcode)
        chosen = to_slot is None
        target_slot = self._first_empty_storage_slot() if to_slot is None else to_slot

        record = self._orchestrator().execute(
            TapeOpRequest(
                op_type=TapeOpType.IMPORT,
                barcode=barcode,
                slot_id=target_slot,
                requested_by="mailslot",
                extras={"ie_slot": ie_slot},
            )
        )
        self._raise_if_failed(record, "import")
        # The cartridge is back in the library: whatever is catalogued on it is
        # restorable again.
        self.catalog.set_cartridge_state(barcode, _STATE_IN_SLOT)
        return MailslotMoveResult(
            op_id=record.op_id,
            barcode=barcode,
            source="import_export_slot",
            source_slot=ie_slot,
            destination="storage_slot",
            destination_slot=target_slot,
            slot_was_chosen=chosen,
        )

    def export_cartridge(
        self, barcode: str, *, ie_slot: int | None = None, force: bool = False
    ) -> MailslotMoveResult:
        """Move a cartridge from storage into the first empty I/E element."""
        assessment = self.preview_export(barcode)
        source_slot = self.library.find_slot_by_barcode(barcode)  # type: ignore[attr-defined]
        if source_slot is None:
            raise CartridgeNotFoundError(
                f"Cartridge {barcode} is not in a storage slot; "
                "unload it from its drive before exporting"
            )
        target_ie = (
            self._first_empty_ie_slot()
            if ie_slot is None
            else self._require_ie_slot(ie_slot, must_be_occupied=False).slot_id
        )

        record = self._orchestrator().execute(
            TapeOpRequest(
                op_type=TapeOpType.EXPORT,
                barcode=barcode,
                slot_id=source_slot,
                requested_by="mailslot",
                extras={"ie_slot": target_ie, "force": force},
            )
        )
        self._raise_if_failed(record, "export")
        # Every restore path reads this flag. Data that is sitting in a mailslot
        # waiting for a human to take it is not online data.
        self.catalog.set_cartridge_state(barcode, _STATE_EXPORTED)
        return MailslotMoveResult(
            op_id=record.op_id,
            barcode=barcode,
            source="storage_slot",
            source_slot=source_slot,
            destination="import_export_slot",
            destination_slot=target_ie,
            slot_was_chosen=ie_slot is None,
            assessment=assessment,
        )

    # -- internals ------------------------------------------------------------

    def _orchestrator(self) -> TapeOperationOrchestrator:
        return TapeOperationOrchestrator(self.catalog, self.library, self.ltfs)

    @staticmethod
    def _raise_if_failed(record: object, op_name: str) -> None:
        status = getattr(record, "status", None)
        if status is TapeOpStatus.FAILED:
            from openblade.nas.tape_orchestrator import TapeOperationFailedError

            raise TapeOperationFailedError(
                getattr(record, "error", None) or f"Tape {op_name} operation failed"
            )

    def _require_ie_slot(self, ie_slot: int, *, must_be_occupied: bool) -> SlotState:
        for slot in self._mailslot_library().import_export_slots():
            if slot.slot_id != ie_slot:
                continue
            if must_be_occupied and not slot.occupied:
                raise ImportExportSlotError(f"Import/export slot {ie_slot} is empty")
            if not must_be_occupied and slot.occupied:
                raise ImportExportSlotError(
                    f"Import/export slot {ie_slot} already holds {slot.barcode}"
                )
            return slot
        known = [slot.slot_id for slot in self._mailslot_library().import_export_slots()]
        raise ImportExportSlotError(
            f"Import/export slot {ie_slot} does not exist "
            f"(this library has {known or 'none'})"
        )

    def _first_empty_ie_slot(self) -> int:
        for slot in self._mailslot_library().import_export_slots():
            if not slot.occupied:
                return slot.slot_id
        raise ImportExportSlotError(
            "Every import/export slot is occupied; empty the mailslot before exporting"
        )

    def _first_empty_storage_slot(self) -> int:
        for slot in self.library.inventory().slots:  # type: ignore[attr-defined]
            if slot.barcode is None:
                return slot.slot_id
        raise ImportExportSlotError(
            "No empty storage slot is available to import into"
        )
