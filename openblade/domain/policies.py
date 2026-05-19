"""Safety policies for OpenBlade operations."""

import secrets
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class DryRunPlan:
    """Describes what an operation WOULD do without executing it."""

    operation: str
    target: str
    affected_barcodes: list[str]
    warnings: list[str]
    is_destructive: bool
    estimated_duration_seconds: int | None = None


@dataclass(frozen=True)
class SafetyToken:
    """One-time token authorizing a specific destructive operation."""

    token: str
    operation: str
    target_barcode: str
    expires_at: float

    @staticmethod
    def generate(operation: str, target_barcode: str, ttl_seconds: int = 300) -> "SafetyToken":
        return SafetyToken(
            token=secrets.token_urlsafe(32),
            operation=operation,
            target_barcode=target_barcode,
            expires_at=time.time() + ttl_seconds,
        )

    def is_valid(self) -> bool:
        return time.time() < self.expires_at

    def validate(self) -> None:
        if not self.is_valid():
            from openblade.domain.errors import SafetyViolationError

            raise SafetyViolationError("Safety token has expired")


@dataclass(frozen=True)
class FormatConfirmation:
    """Explicit confirmation required before formatting a tape."""

    expected_barcode: str
    safety_token: SafetyToken
    operator_note: str = ""

    def validate(self, actual_barcode: str) -> None:
        """Raise an error if confirmation is invalid or barcodes don't match."""
        self.safety_token.validate()
        if self.expected_barcode != actual_barcode:
            from openblade.domain.errors import BarcodeMismatchError

            raise BarcodeMismatchError(
                f"Expected barcode {self.expected_barcode!r} but got {actual_barcode!r}"
            )


@dataclass(frozen=True)
class RealHardwareGuard:
    """Must be passed to any real hardware operation."""

    config_backend: str
    config_real_hardware_enabled: bool
    operator_acknowledgment: str

    def validate(self) -> None:
        """Raise SafetyViolationError if real hardware is not properly enabled."""
        from openblade.domain.errors import RealHardwareDisabledError

        if self.config_backend != "real" or not self.config_real_hardware_enabled:
            raise RealHardwareDisabledError(
                "Real hardware operations require OPENBLADE_BACKEND=real and "
                "OPENBLADE_REAL_HARDWARE_ENABLED=true"
            )
        if not self.operator_acknowledgment:
            raise RealHardwareDisabledError("Operator acknowledgment is required")
