"""Fault injection configuration for the simulator."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class FaultType(str, Enum):
    LOAD_FAILURE = "load_failure"
    UNLOAD_FAILURE = "unload_failure"
    DRIVE_TIMEOUT = "drive_timeout"
    MOUNT_FAILURE = "mount_failure"
    WRITE_FAILURE = "write_failure"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    NO_FREE_SPACE = "no_free_space"
    ROBOT_LOCK_CONFLICT = "robot_lock_conflict"
    CHANGER_TIMEOUT = "changer_timeout"
    MOVE_FAILURE = "move_failure"


@dataclass
class FaultConfig:
    """Configure which faults to inject and when."""

    faults: dict[FaultType, bool] = field(default_factory=dict)
    write_fail_after_bytes: int | None = None

    def should_fail(self, fault_type: FaultType) -> bool:
        return self.faults.get(fault_type, False)

    def to_json(self) -> dict[str, Any]:
        return {
            "faults": {fault.value: enabled for fault, enabled in self.faults.items()},
            "write_fail_after_bytes": self.write_fail_after_bytes,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any] | None) -> FaultConfig:
        if not payload:
            return cls.no_faults()
        raw_faults = payload.get("faults", {})
        faults = {
            FaultType(name): bool(enabled)
            for name, enabled in raw_faults.items()
            if name in {fault.value for fault in FaultType}
        }
        write_fail_after_bytes = payload.get("write_fail_after_bytes")
        return cls(
            faults=faults,
            write_fail_after_bytes=(
                int(write_fail_after_bytes) if write_fail_after_bytes is not None else None
            ),
        )

    @classmethod
    def no_faults(cls) -> FaultConfig:
        return cls()

    @classmethod
    def with_fault(cls, fault_type: FaultType) -> FaultConfig:
        return cls(faults={fault_type: True})
