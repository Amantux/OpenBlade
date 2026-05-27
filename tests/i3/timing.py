"""
Timing profiles for Quantum i3 test suite.

Three profiles:
  instant   — zero delays, for fast CI runs
  realistic — short delays that feel like real hardware (default in emulator mode)
  hardware  — real Quantum i3 mechanical tolerances (used with real i3 target)

Usage:
    from tests.i3.timing import get_profile, wait_for_op, assert_within_tolerance
"""
from __future__ import annotations

import os
import time
from typing import Literal

OpType = Literal[
    "tape_load",
    "tape_unload",
    "move",
    "rewind",
    "format",
    "mount",
    "unmount",
    "inventory",
    "auth",
]

ProfileName = Literal["instant", "realistic", "hardware"]

TIMING_PROFILES: dict[ProfileName, dict[OpType, float]] = {
    "instant": {
        "tape_load": 0.0,
        "tape_unload": 0.0,
        "move": 0.0,
        "rewind": 0.0,
        "format": 0.0,
        "mount": 0.0,
        "unmount": 0.0,
        "inventory": 0.0,
        "auth": 0.0,
    },
    "realistic": {
        "tape_load": 3.0,
        "tape_unload": 2.0,
        "move": 1.5,
        "rewind": 5.0,
        "format": 8.0,
        "mount": 2.0,
        "unmount": 1.5,
        "inventory": 2.0,
        "auth": 0.1,
    },
    "hardware": {
        "tape_load": 35.0,
        "tape_unload": 25.0,
        "move": 8.0,
        "rewind": 90.0,
        "format": 300.0,
        "mount": 15.0,
        "unmount": 10.0,
        "inventory": 45.0,
        "auth": 0.5,
    },
}

# Tolerance multipliers for hardware mode (real i3 timing can vary)
HARDWARE_TOLERANCE: dict[OpType, float] = {
    "tape_load": 0.4,    # ±40% — mechanical variation
    "tape_unload": 0.4,
    "move": 0.5,
    "rewind": 0.6,       # tape length dependent
    "format": 0.3,
    "mount": 0.3,
    "unmount": 0.3,
    "inventory": 0.5,
    "auth": 2.0,         # network latency varies widely
}


def get_profile_name() -> ProfileName:
    """Read profile from env. Defaults to 'instant' for emulator, 'hardware' for real."""
    env = os.environ.get("I3_TIMING_PROFILE", "").strip().lower()
    if env in ("instant", "realistic", "hardware"):
        return env  # type: ignore[return-value]
    mode = os.environ.get("I3_TEST_MODE", "emulator").strip().lower()
    return "hardware" if mode == "real" else "instant"


def get_profile() -> dict[OpType, float]:
    """Return the active timing profile dict."""
    return TIMING_PROFILES[get_profile_name()]


def wait_for_op(op_type: OpType, multiplier: float = 1.0) -> None:
    """Sleep for the appropriate time for this operation.

    In instant mode this is a no-op. In realistic/hardware modes the sleep
    simulates the mechanical delay an operator would observe on a real i3.
    """
    delay = get_profile().get(op_type, 0.0) * multiplier
    if delay > 0:
        time.sleep(delay)


def assert_within_tolerance(
    elapsed: float,
    op_type: OpType,
    *,
    profile_override: ProfileName | None = None,
) -> None:
    """Assert that a measured elapsed time falls within acceptable bounds.

    Only asserts in hardware profile (emulator timing is not meaningful for
    duration assertions). In instant/realistic mode this is a no-op.
    """
    name = profile_override or get_profile_name()
    if name != "hardware":
        return
    expected = TIMING_PROFILES["hardware"][op_type]
    tolerance = HARDWARE_TOLERANCE.get(op_type, 0.5)
    lo = expected * (1.0 - tolerance)
    hi = expected * (1.0 + tolerance)
    assert lo <= elapsed <= hi, (
        f"Timing out of range for {op_type}: "
        f"expected {lo:.1f}–{hi:.1f}s, got {elapsed:.2f}s"
    )
