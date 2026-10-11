"""On a REAL backend FormatService formats only OPENBLADE_SCRATCH_BARCODES tapes.

Fakes only: ``real_backend=True`` is a constructor flag; no device, no enable flag.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from openblade.domain.errors import FormatRequiresConfirmationError
from openblade.domain.media_allowlist import is_scratch_barcode, parse_barcodes
from openblade.jobs.format import FormatService

SCRATCH = "SCR001L9"
DATA = "DATA01L9"


def _service(*, real: bool | None, allowlist: list[str] | None) -> FormatService:
    inventory = SimpleNamespace(
        slots=[SimpleNamespace(barcode=SCRATCH), SimpleNamespace(barcode=DATA)], drives=[]
    )
    library = SimpleNamespace(inventory=lambda: inventory)
    catalog = SimpleNamespace(save_safety_token=lambda token: None)
    fake: Any = (catalog, library, SimpleNamespace())
    return FormatService(*fake, real_backend=real, scratch_barcodes=allowlist)


def test_real_backend_dry_run_on_unlisted_tape_is_refused() -> None:
    with pytest.raises(FormatRequiresConfirmationError, match="OPENBLADE_SCRATCH_BARCODES"):
        _service(real=True, allowlist=[SCRATCH]).dry_run(DATA)


def test_real_backend_confirm_on_unlisted_tape_is_refused_before_token_lookup() -> None:
    # The fake catalog has no get_safety_token: reaching it would AttributeError.
    with pytest.raises(FormatRequiresConfirmationError, match="OPENBLADE_SCRATCH_BARCODES"):
        _service(real=True, allowlist=[SCRATCH]).confirm(DATA, "any-token")


def test_real_backend_empty_allowlist_refuses_everything() -> None:
    with pytest.raises(FormatRequiresConfirmationError):
        _service(real=True, allowlist=[]).dry_run(SCRATCH)


def test_real_backend_allowlisted_tape_dry_run_is_allowed() -> None:
    plan, token = _service(real=True, allowlist=[SCRATCH]).dry_run(SCRATCH)
    assert plan.target == SCRATCH
    assert token.target_barcode == SCRATCH


def test_simulator_backend_is_not_allowlist_gated() -> None:
    plan, _ = _service(real=False, allowlist=[]).dry_run(DATA)
    assert plan.target == DATA


def test_default_reads_backend_and_allowlist_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENBLADE_BACKEND", "real")
    monkeypatch.delenv("OPENBLADE_REAL_HARDWARE_ENABLED", raising=False)
    monkeypatch.setenv("OPENBLADE_SCRATCH_BARCODES", f" {SCRATCH.lower()} ")
    service = _service(real=None, allowlist=None)
    with pytest.raises(FormatRequiresConfirmationError):
        service.dry_run(DATA)
    assert service.dry_run(SCRATCH)[0].target == SCRATCH


def test_is_scratch_barcode_normalises_and_fails_closed() -> None:
    assert is_scratch_barcode("scr001l9", parse_barcodes("SCR001L9,X"))
    assert not is_scratch_barcode(SCRATCH, [])


def test_real_backend_padded_lowercase_barcode_is_normalised_and_accepted() -> None:
    inventory = SimpleNamespace(slots=[SimpleNamespace(barcode="ABC123")], drives=[])
    library = SimpleNamespace(inventory=lambda: inventory)
    catalog = SimpleNamespace(save_safety_token=lambda token: None)
    fake: Any = (catalog, library, SimpleNamespace())
    service = FormatService(*fake, real_backend=True, scratch_barcodes=["ABC123"])

    plan, token = service.dry_run(" abc123 ")

    assert plan.target == "ABC123"
    assert token.target_barcode == "ABC123"
