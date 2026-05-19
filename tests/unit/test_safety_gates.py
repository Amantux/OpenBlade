from unittest.mock import patch

import pytest

from openblade.config import OpenBladeConfig
from openblade.domain.errors import (
    BarcodeMismatchError,
    RealHardwareDisabledError,
    SafetyViolationError,
)
from openblade.domain.policies import FormatConfirmation, RealHardwareGuard, SafetyToken
from openblade.hardware.safety import require_real_hardware


def test_real_hardware_guard_raises_for_mock_backend() -> None:
    with pytest.raises(RealHardwareDisabledError):
        RealHardwareGuard("mock", True, "ack").validate()


def test_real_hardware_guard_raises_for_disabled_flag() -> None:
    with pytest.raises(RealHardwareDisabledError):
        RealHardwareGuard("real", False, "ack").validate()


def test_real_hardware_guard_succeeds_when_enabled() -> None:
    RealHardwareGuard("real", True, "ack").validate()


def test_format_confirmation_raises_on_barcode_mismatch() -> None:
    token = SafetyToken.generate("format", "PHO001L8")
    with pytest.raises(BarcodeMismatchError):
        FormatConfirmation("PHO001L8", token).validate("PHO002L8")


@patch("time.time", return_value=10_000.0)
def test_format_confirmation_raises_on_expired_token(_: object) -> None:
    token = SafetyToken(token="x", operation="format", target_barcode="PHO001L8", expires_at=1.0)
    with pytest.raises(SafetyViolationError):
        FormatConfirmation("PHO001L8", token).validate("PHO001L8")


def test_format_confirmation_succeeds() -> None:
    token = SafetyToken.generate("format", "PHO001L8")
    FormatConfirmation("PHO001L8", token).validate("PHO001L8")


@patch("time.time", return_value=10_000.0)
def test_safety_token_is_invalid_when_expired(_: object) -> None:
    token = SafetyToken(token="x", operation="format", target_barcode="PHO001L8", expires_at=1.0)
    assert token.is_valid() is False


def test_require_real_hardware_raises_in_default_config() -> None:
    with pytest.raises(RealHardwareDisabledError):
        require_real_hardware(OpenBladeConfig())
