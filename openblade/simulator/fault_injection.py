"""Fault injection framework for the tape library simulator.

Allows tests to simulate hardware faults, network timeouts, and
partial failures without real hardware. All faults are ephemeral
and scoped to a single test context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from threading import RLock
from typing import Any


class FaultType(str, Enum):
    """Supported simulator fault types."""

    LOAD_FAILURE = "load_failure"
    UNLOAD_FAILURE = "unload_failure"
    WRITE_ERROR = "write_error"
    READ_ERROR = "read_error"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    DRIVE_UNAVAILABLE = "drive_unavailable"
    TAPE_NOT_FOUND = "tape_not_found"
    MOUNT_TIMEOUT = "mount_timeout"
    PARTIAL_WRITE = "partial_write"
    CATALOG_DB_FAILURE = "catalog_db_failure"


class SimulatorFaultError(RuntimeError):
    """Raised when an injected simulator fault is triggered."""


@dataclass
class FaultSpec:
    """Configuration for a single injected fault."""

    fault_type: FaultType
    target: str = ""
    trigger_after: int = 0
    max_triggers: int = 1
    error_message: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class _TrackedFault:
    spec: FaultSpec
    successful_operations: int = 0
    trigger_count: int = 0

    def exhausted(self) -> bool:
        return self.trigger_count >= self.spec.max_triggers


_DEFAULT_ERROR_MESSAGES: dict[FaultType, str] = {
    FaultType.LOAD_FAILURE: "Injected load failure",
    FaultType.UNLOAD_FAILURE: "Injected unload failure",
    FaultType.WRITE_ERROR: "Injected write error",
    FaultType.READ_ERROR: "Injected read error",
    FaultType.CHECKSUM_MISMATCH: "Injected checksum mismatch",
    FaultType.DRIVE_UNAVAILABLE: "Injected drive unavailable fault",
    FaultType.TAPE_NOT_FOUND: "Injected tape not found fault",
    FaultType.MOUNT_TIMEOUT: "Injected mount timeout",
    FaultType.PARTIAL_WRITE: "Injected partial write",
    FaultType.CATALOG_DB_FAILURE: "Injected catalog database failure",
}


class FaultInjector:
    """Context-manager fault injector for simulator testing."""

    def __init__(self) -> None:
        """Initialize the injector with no active faults."""
        self._faults: list[_TrackedFault] = []
        self._lock = RLock()
        self._last_triggered: FaultSpec | None = None

    def inject(self, spec: FaultSpec) -> None:
        """Register a fault to be triggered."""
        tracked = _TrackedFault(
            spec=FaultSpec(
                fault_type=spec.fault_type,
                target=spec.target,
                trigger_after=spec.trigger_after,
                max_triggers=spec.max_triggers,
                error_message=spec.error_message,
                extra=dict(spec.extra),
            )
        )
        with self._lock:
            self._faults.append(tracked)

    def clear(self) -> None:
        """Remove all registered faults."""
        with self._lock:
            self._faults.clear()
            self._last_triggered = None

    def should_fault(self, fault_type: FaultType, target: str = "") -> bool:
        """Check whether a fault should trigger for the current operation."""
        with self._lock:
            for tracked in self._faults:
                spec = tracked.spec
                if tracked.exhausted() or not self._matches(spec, fault_type, target):
                    continue
                if tracked.successful_operations < spec.trigger_after:
                    tracked.successful_operations += 1
                    continue
                tracked.trigger_count += 1
                self._last_triggered = spec
                return True
        return False

    def get_error_message(self, fault_type: FaultType, target: str = "") -> str:
        """Return the error message for a triggered or matching fault."""
        with self._lock:
            spec = self._matching_spec(fault_type, target)
            if spec is not None and spec.error_message:
                return spec.error_message
        return self._default_error_message(fault_type, target)

    def active_faults(self) -> list[FaultSpec]:
        """Return the currently active, non-exhausted fault specs."""
        with self._lock:
            return [self._copy_spec(tracked.spec) for tracked in self._faults if not tracked.exhausted()]

    def __enter__(self) -> "FaultInjector":
        """Activate faults for a context-managed block."""
        return self

    def __exit__(self, *args: Any) -> None:
        """Clear all faults when leaving the managed context."""
        self.clear()

    def _matching_spec(self, fault_type: FaultType, target: str) -> FaultSpec | None:
        if self._last_triggered is not None and self._matches(self._last_triggered, fault_type, target):
            return self._last_triggered
        for tracked in self._faults:
            if self._matches(tracked.spec, fault_type, target):
                return tracked.spec
        return None

    @staticmethod
    def _matches(spec: FaultSpec, fault_type: FaultType, target: str) -> bool:
        if spec.fault_type is not fault_type:
            return False
        if spec.target in {"", "*"}:
            return True
        return spec.target == target

    @staticmethod
    def _copy_spec(spec: FaultSpec) -> FaultSpec:
        return FaultSpec(
            fault_type=spec.fault_type,
            target=spec.target,
            trigger_after=spec.trigger_after,
            max_triggers=spec.max_triggers,
            error_message=spec.error_message,
            extra=dict(spec.extra),
        )

    @staticmethod
    def _default_error_message(fault_type: FaultType, target: str) -> str:
        message = _DEFAULT_ERROR_MESSAGES.get(fault_type, "Injected simulator fault")
        return f"{message} for {target}" if target else message
