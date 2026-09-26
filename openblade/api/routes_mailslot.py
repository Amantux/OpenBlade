"""Import/export (mailslot) API endpoints.

The mailslot flows have existed only as CLI commands (``openblade mailslot
list|import|export``) on top of :class:`openblade.nas.mailslot.MailslotService`.
This module is the HTTP half so the web UI can drive the same station; every
decision -- slot picking, the export refusal, the catalog state write -- stays in
the service and the tape orchestrator. Nothing here reimplements policy.

The export refusal is the one thing worth reading twice: ``POST /mailslot/export``
answers **409** with the orchestrator's own message, which names the cartridge,
its volume group and sample paths. A typed ``confirmBarcode`` (matching the
cartridge exactly) is the only way past it, exactly as ``--confirm-barcode`` is on
the CLI, and callers are expected to have shown the operator the
``GET /mailslot/export-preview/{barcode}`` assessment first.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi import Path as PathParam
from pydantic import BaseModel, Field

from openblade.bootstrap import AppContext, get_context
from openblade.domain.errors import (
    CartridgeNotFoundError,
    ExportRefusedError,
    ImportExportSlotError,
    MailslotUnsupportedError,
)
from openblade.nas.mailslot import MailslotMoveResult, MailslotService
from openblade.nas.tape_orchestrator import TapeOperationFailedError

router = APIRouter(prefix="/mailslot", tags=["mailslot"])


class MailslotSlotResponse(BaseModel):
    slotId: int
    occupied: bool
    barcode: str | None


class MailslotListingResponse(BaseModel):
    importExportSlots: list[MailslotSlotResponse]
    slotCount: int
    occupiedCount: int
    #: False when the active backend has no import/export station at all. The UI
    #: has to tell "this library has no mailslot" apart from "the mailslot is
    #: empty" -- they look identical in the slot list and mean opposite things.
    supported: bool


class ExportAssessmentResponse(BaseModel):
    barcode: str
    volumeGroup: str | None
    archivedFilesOnCartridge: int
    pendingFilesOnCartridge: int
    bytesOnCartridge: int
    samplePaths: list[str]
    volumeGroupBarcodesWithData: list[str]
    carriesData: bool


class MailslotImportRequest(BaseModel):
    ie_slot: int = Field(ge=0, description="Import/export element holding the cartridge")
    #: None means "pick the first empty storage slot"; the response says which.
    to_slot: int | None = Field(default=None, ge=0)


class MailslotExportRequest(BaseModel):
    barcode: str = Field(min_length=1, max_length=64)
    ie_slot: int | None = Field(default=None, ge=0)
    #: Export a cartridge that still carries archived data. The refusal names what
    #: would leave; this is the operator saying they meant it.
    # Typed confirmation for a data-carrying export: must equal `barcode`
    # exactly. Replaces the earlier boolean `force` — a bare true is too easy
    # to send, and native API auth is OFF by default, so this route can be an
    # unauthenticated POST; the typed barcode is the deliberate-action proof.
    confirm_barcode: str | None = Field(default=None, alias="confirmBarcode")


class MailslotMoveResponse(BaseModel):
    opId: str
    barcode: str
    source: str
    sourceSlot: int
    destination: str
    destinationSlot: int
    destinationSlotChosen: bool
    exported: ExportAssessmentResponse | None = None


def _service(context: AppContext) -> MailslotService:
    return MailslotService(context.catalog, context.library, context.ltfs)


def _move_response(result: MailslotMoveResult) -> MailslotMoveResponse:
    return MailslotMoveResponse.model_validate(result.to_dict())


@router.get("/slots", response_model=MailslotListingResponse)
async def list_mailslot_slots(
    context: AppContext = Depends(get_context),
) -> MailslotListingResponse:
    """Every import/export element and what is in it."""
    try:
        listing = _service(context).list_slots()
    except MailslotUnsupportedError:
        # Not an error the operator can act on: this backend simply has no I/E
        # station. Reported as an empty, unsupported listing so the UI can say so.
        return MailslotListingResponse(
            importExportSlots=[], slotCount=0, occupiedCount=0, supported=False
        )
    return MailslotListingResponse(
        importExportSlots=[
            MailslotSlotResponse.model_validate(slot.to_dict()) for slot in listing.slots
        ],
        slotCount=listing.slot_count,
        occupiedCount=len(listing.occupied),
        supported=True,
    )


@router.get("/export-preview/{barcode}", response_model=ExportAssessmentResponse)
async def preview_mailslot_export(
    barcode: str = PathParam(min_length=1, max_length=64),
    context: AppContext = Depends(get_context),
) -> ExportAssessmentResponse:
    """What would leave with ``barcode``. Moves nothing.

    404s for a barcode this library has never heard of. ``assess_export`` answers
    "nothing on it" for an unknown barcode, which is true and useless: rendered in
    a UI next to an Export button, a typo reads as "safe to export".
    """
    # "Known" must cover everywhere a cartridge can BE, not just storage:
    # one sitting in the mailslot or loaded in a drive with no catalog row
    # would otherwise 404 as "not in this library's inventory" — false.
    inventory = context.library.inventory()
    ie_barcodes: set[str] = set()
    mailslot_backend = getattr(context.library, "import_export_slots", None)
    if callable(mailslot_backend):
        ie_barcodes = {str(slot.barcode) for slot in mailslot_backend() if slot.barcode is not None}
    known = (
        any(slot.barcode is not None and str(slot.barcode) == barcode for slot in inventory.slots)
        or any(
            drive.barcode is not None and str(drive.barcode) == barcode
            for drive in inventory.drives
        )
        or barcode in ie_barcodes
        or context.catalog.get_cartridge(barcode) is not None
    )
    if not known:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Cartridge {barcode} is not in this library's inventory or catalog; "
                "there is nothing to assess."
            ),
        )
    assessment = _service(context).preview_export(barcode)
    return ExportAssessmentResponse.model_validate(assessment.to_dict())


@router.post("/import", response_model=MailslotMoveResponse)
async def import_from_mailslot(
    payload: MailslotImportRequest,
    context: AppContext = Depends(get_context),
) -> MailslotMoveResponse:
    """Move a cartridge out of an I/E element into a storage slot."""
    try:
        result = _service(context).import_cartridge(payload.ie_slot, payload.to_slot)
    except MailslotUnsupportedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except ImportExportSlotError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except CartridgeNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except TapeOperationFailedError as exc:
        # Curated at the orchestrator's raise site (`_safe_error_message`).
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from None
    return _move_response(result)


@router.post("/export", response_model=MailslotMoveResponse)
async def export_through_mailslot(
    payload: MailslotExportRequest,
    context: AppContext = Depends(get_context),
) -> MailslotMoveResponse:
    """Move a cartridge out of storage into the I/E station.

    Refuses with 409 while the cartridge still carries archived or in-flight file
    instances; the detail is the refusal message naming them.
    ``confirmBarcode`` equal to the cartridge barcode overrides — the HTTP
    spelling of the CLI's ``--confirm-barcode`` typed confirmation.
    """
    try:
        result = _service(context).export_cartridge(
            payload.barcode, ie_slot=payload.ie_slot, confirm_barcode=payload.confirm_barcode
        )
    except ExportRefusedError as exc:
        # 409, not 400: the request is well-formed and the refusal is about the
        # state of the cartridge. The message is operator-facing text that exists
        # precisely to be read -- it is passed through verbatim.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except MailslotUnsupportedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except ImportExportSlotError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except CartridgeNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except TapeOperationFailedError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from None
    return _move_response(result)
