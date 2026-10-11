"""Tape formatting workflow with explicit confirmation."""

from __future__ import annotations

from collections.abc import Sequence

from openblade.catalog.repository import CatalogRepository
from openblade.config import BackendMode, load_config
from openblade.domain.backends import LibraryBackend, LTFSBackend
from openblade.domain.errors import BarcodeMismatchError, FormatRequiresConfirmationError
from openblade.domain.media_allowlist import (
    SCRATCH_BARCODES_ENV,
    is_scratch_barcode,
    scratch_barcodes_from_env,
)
from openblade.domain.models import OperationResult
from openblade.domain.policies import DryRunPlan, FormatConfirmation, SafetyToken
from openblade.nas.tape_orchestrator import execute_tape_request
from openblade.nas.types import TapeOpRequest, TapeOpType


def real_backend_from_config() -> bool:
    """True when the configured backend is REAL, ignoring the enable flag (fail closed).

    The single detection used by ``FormatService`` and by NAS ingest's refusal to
    auto-format on real hardware.
    """
    return load_config().backend == BackendMode.REAL


class FormatService:
    def __init__(
        self,
        catalog: CatalogRepository,
        library: LibraryBackend,
        ltfs: LTFSBackend,
        *,
        real_backend: bool | None = None,
        scratch_barcodes: Sequence[str] | None = None,
    ) -> None:
        self.catalog = catalog
        self.library = library
        self.ltfs = ltfs
        # Real media gate: on a REAL backend only allowlisted scratch tapes may be
        # formatted. Defaults come from the environment so every construction
        # site is covered; the backend check ignores the enable flag (fail closed).
        self._real_backend = real_backend_from_config() if real_backend is None else real_backend
        self._scratch_barcodes = (
            scratch_barcodes_from_env() if scratch_barcodes is None else list(scratch_barcodes)
        )

    def _require_scratch_media(self, barcode: str) -> None:
        if self._real_backend and not is_scratch_barcode(barcode, self._scratch_barcodes):
            raise FormatRequiresConfirmationError(
                f"Barcode {barcode} is not in {SCRATCH_BARCODES_ENV}; real-hardware format refused"
            )

    def dry_run(self, barcode: str) -> tuple[DryRunPlan, SafetyToken]:
        self._require_scratch_media(barcode)
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
        self._require_scratch_media(barcode)
        token = self.catalog.get_safety_token(token_value)
        if token is None:
            raise FormatRequiresConfirmationError("Unknown or missing safety token")
        if token.operation != "format" or token.target_barcode != barcode:
            raise FormatRequiresConfirmationError("Safety token does not authorize this barcode")
        # Consume BEFORE the job: single-use even under concurrent confirms, and
        # a failed job does not re-arm the token (operator re-runs dry-run).
        if not self.catalog.delete_safety_token(token_value):
            raise FormatRequiresConfirmationError("Safety token already used or expired")
        result = run_format_job(
            barcode,
            FormatConfirmation(expected_barcode=barcode, safety_token=token),
            self.library,
            self.ltfs,
            self.catalog,
        )
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
