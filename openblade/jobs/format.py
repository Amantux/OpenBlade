"""Tape formatting workflow with explicit confirmation."""

from __future__ import annotations

from openblade.catalog.repository import CatalogRepository
from openblade.domain.backends import LibraryBackend, LTFSBackend
from openblade.domain.errors import BarcodeMismatchError, FormatRequiresConfirmationError
from openblade.domain.models import OperationResult
from openblade.domain.policies import DryRunPlan, FormatConfirmation, SafetyToken
from openblade.nas.tape_orchestrator import execute_tape_request
from openblade.nas.types import TapeOpRequest, TapeOpType


class FormatService:
    def __init__(
        self,
        catalog: CatalogRepository,
        library: LibraryBackend,
        ltfs: LTFSBackend,
    ) -> None:
        self.catalog = catalog
        self.library = library
        self.ltfs = ltfs

    def dry_run(self, barcode: str) -> tuple[DryRunPlan, SafetyToken]:
        inventory = self.library.inventory()
        known = {str(slot.barcode) for slot in inventory.slots if slot.barcode is not None} | {
            str(drive.barcode) for drive in inventory.drives if drive.barcode is not None
        }
        if barcode not in known:
            raise BarcodeMismatchError(f"Unknown barcode {barcode}")
        token = SafetyToken.generate("format", barcode)
        self.catalog.save_safety_token(token)
        plan = DryRunPlan(
            operation="format",
            target=barcode,
            affected_barcodes=[barcode],
            warnings=["Destructive operation", "Inventory barcode must match confirmation"],
            is_destructive=True,
            estimated_duration_seconds=120,
        )
        return plan, token

    def confirm(self, barcode: str, token_value: str) -> OperationResult:
        token = self.catalog.get_safety_token(token_value)
        if token is None:
            raise FormatRequiresConfirmationError("Unknown or missing safety token")
        if token.operation != "format" or token.target_barcode != barcode:
            raise FormatRequiresConfirmationError("Safety token does not authorize this barcode")
        result = run_format_job(
            barcode,
            FormatConfirmation(expected_barcode=barcode, safety_token=token),
            self.library,
            self.ltfs,
            self.catalog,
        )
        self.catalog.delete_safety_token(token_value)
        cartridge = self.catalog.add_cartridge(barcode)
        cartridge.formatted = True
        cartridge.used_bytes = 0
        self.catalog.session.commit()
        return result


def run_format_job(
    barcode: str,
    confirmation: FormatConfirmation,
    library: LibraryBackend,
    ltfs: LTFSBackend,
    catalog: CatalogRepository | None = None,
) -> OperationResult:
    """Format tape. Requires FormatConfirmation with correct barcode."""
    confirmation.validate(barcode)
    record = execute_tape_request(
        catalog,
        library,
        ltfs,
        TapeOpRequest(
            op_type=TapeOpType.FORMAT,
            barcode=barcode,
            requested_by="format-service",
            extras={"confirmed_format": True, "format_confirmation": confirmation},
        ),
        raise_on_failed=True,
    )
    return OperationResult(
        success=bool(record.result.get("success", True)),
        message=str(record.result.get("message", "Tape formatted")),
        details=record.result,
    )
