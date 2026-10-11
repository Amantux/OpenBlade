"""Pin the safety-token / format-confirmation / real-hardware guards in policies.py.

Written to kill surviving mutmut mutants: every assertion here fails when the
corresponding guard, field or curated operator message is mutated.
"""

from __future__ import annotations

import pytest

from openblade.domain import policies
from openblade.domain.errors import (
    BarcodeMismatchError,
    RealHardwareDisabledError,
    SafetyViolationError,
)
from openblade.domain.policies import FormatConfirmation, RealHardwareGuard, SafetyToken


def test_generate_binds_operation_barcode_and_a_32_byte_token() -> None:
    tok = SafetyToken.generate("format", "ABC123L6")
    assert tok.operation == "format"
    assert tok.target_barcode == "ABC123L6"
    assert isinstance(tok.token, str)
    # secrets.token_urlsafe(32) -> 32 bytes base64url without padding = 43 chars.
    assert len(tok.token) == 43
    assert tok.token != SafetyToken.generate("format", "ABC123L6").token


def test_token_is_invalid_at_exact_expiry_instant(monkeypatch: pytest.MonkeyPatch) -> None:
    tok = SafetyToken.generate("format", "ABC123L6", ttl_seconds=10)
    monkeypatch.setattr(policies.time, "time", lambda: tok.expires_at)
    assert tok.is_valid() is False
    monkeypatch.setattr(policies.time, "time", lambda: tok.expires_at - 0.001)
    assert tok.is_valid() is True


def test_expired_token_raises_curated_message(monkeypatch: pytest.MonkeyPatch) -> None:
    tok = SafetyToken.generate("format", "ABC123L6", ttl_seconds=10)
    monkeypatch.setattr(policies.time, "time", lambda: tok.expires_at + 1)
    with pytest.raises(SafetyViolationError) as exc:
        tok.validate()
    assert str(exc.value) == "Safety token has expired"


def test_barcode_mismatch_names_both_barcodes() -> None:
    conf = FormatConfirmation("ABC123L6", SafetyToken.generate("format", "ABC123L6"))
    conf.validate("ABC123L6")
    with pytest.raises(BarcodeMismatchError) as exc:
        conf.validate("XYZ999L6")
    assert str(exc.value) == "Expected barcode 'ABC123L6' but got 'XYZ999L6'"


@pytest.mark.parametrize(("backend", "enabled"), [("sim", True), ("real", False)])
def test_real_hardware_disabled_message_names_both_settings(backend: str, enabled: bool) -> None:
    with pytest.raises(RealHardwareDisabledError) as exc:
        RealHardwareGuard(backend, enabled, "operator ok").validate()
    assert str(exc.value) == (
        "Real hardware operations require OPENBLADE_BACKEND=real and "
        "OPENBLADE_REAL_HARDWARE_ENABLED=true"
    )


def test_real_hardware_requires_operator_acknowledgment() -> None:
    RealHardwareGuard("real", True, "operator ok").validate()
    with pytest.raises(RealHardwareDisabledError) as exc:
        RealHardwareGuard("real", True, "").validate()
    assert str(exc.value) == "Operator acknowledgment is required"
