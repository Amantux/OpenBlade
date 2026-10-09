"""
Timing profiles for Quantum i3 test suite.

Legacy profiles (names kept stable for CI and existing callers):
  instant   — zero delays, for fast CI runs
  realistic — short delays that feel like real hardware (default in emulator mode)
  hardware  — real Quantum i3 mechanical tolerances (used with real i3 target)

Scenario profiles (realistic baseline plus one stressor each):
  normal             — alias of realistic delays, no faults
  slow-robotics      — robot/drive mechanics 3x slower
  busy-library       — every mechanical op waits behind a contended robot queue
  intermittent-drive — every Nth tape load fails with a RetryableOpError
  session-expiry     — auth sessions expire after a short TTL
  rebooting          — a window of HTTP 503 at the start of the run
  degraded-media     — read/verify 3x slower + periodic checksum retry

All sleeping goes through a ``Clock`` (``RealClock`` by default). Unit tests
install a ``VirtualClock`` with ``use_clock()`` so profile behaviour can be
asserted deterministically without real sleeping.

Delays are applied client-side by ``wait_for_op``. Faults (load failures,
session expiry, 503 window, checksum retries) are applied client-side by a
``ProfileRuntime`` because the emulator has no env-driven fault hook for them;
``emulator_env()`` exports the env vars the emulator DOES honour
(``I3_TIMING_PROFILE``, ``OPENBLADE_EMULATOR_LATENCY_PROFILE``,
``OPENBLADE_EMULATOR_LATENCY_PROFILE_MS``) so its own latency matches.

Usage:
    from tests.i3.timing import get_profile, wait_for_op, assert_within_tolerance
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Literal, Protocol, get_args

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
    "read",
    "verify",
]

LegacyProfileName = Literal["instant", "realistic", "hardware"]
ProfileName = Literal[
    "instant",
    "realistic",
    "hardware",
    "normal",
    "slow-robotics",
    "busy-library",
    "intermittent-drive",
    "session-expiry",
    "rebooting",
    "degraded-media",
]
PROFILE_NAMES: tuple[ProfileName, ...] = get_args(ProfileName)


# ---------------------------------------------------------------------------
# Clock
# ---------------------------------------------------------------------------


class Clock(Protocol):
    """Time source used by every profile sleep."""

    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class RealClock:
    """Wall-clock implementation backed by :mod:`time`."""

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class VirtualClock:
    """Deterministic clock: ``sleep`` advances virtual time instantly."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self._now

    def sleep(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("cannot sleep a negative duration")
        self.sleeps.append(seconds)
        self._now += seconds

    def advance(self, seconds: float) -> None:
        """Move virtual time forward without recording a sleep."""
        if seconds < 0:
            raise ValueError("cannot advance a negative duration")
        self._now += seconds


_clock: Clock = RealClock()


def get_clock() -> Clock:
    return _clock


def set_clock(clock: Clock) -> Clock:
    """Install ``clock`` globally and return the previous one."""
    global _clock
    previous = _clock
    _clock = clock
    return previous


@contextmanager
def use_clock(clock: Clock) -> Iterator[Clock]:
    previous = set_clock(clock)
    try:
        yield clock
    finally:
        set_clock(previous)


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
        "read": 0.0,
        "verify": 0.0,
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
        "read": 2.0,
        "verify": 3.0,
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
        "read": 60.0,
        "verify": 120.0,
    },
}

_MECHANICAL_OPS: tuple[OpType, ...] = (
    "tape_load",
    "tape_unload",
    "move",
    "mount",
    "unmount",
    "inventory",
)
_REALISTIC = TIMING_PROFILES["realistic"]
_BUSY_QUEUE_WAIT_S = 2.0


def _scaled(ops: tuple[OpType, ...], factor: float) -> dict[OpType, float]:
    return {op: (v * factor if op in ops else v) for op, v in _REALISTIC.items()}


TIMING_PROFILES.update(
    {
        "normal": dict(_REALISTIC),
        "slow-robotics": _scaled(_MECHANICAL_OPS, 3.0),
        "busy-library": {
            op: (v + _BUSY_QUEUE_WAIT_S if op in _MECHANICAL_OPS else v)
            for op, v in _REALISTIC.items()
        },
        "intermittent-drive": dict(_REALISTIC),
        "session-expiry": dict(_REALISTIC),
        "rebooting": dict(_REALISTIC),
        "degraded-media": _scaled(("read", "verify"), 3.0),
    }
)


@dataclass(frozen=True)
class ProfileFaults:
    """Client-side fault knobs for a scenario profile (0/None = off)."""

    base: LegacyProfileName = "realistic"
    load_fail_every: int = 0
    auth_ttl_s: float | None = None
    reboot_window_s: float = 0.0
    checksum_retry_every: int = 0


PROFILE_FAULTS: dict[ProfileName, ProfileFaults] = {
    "instant": ProfileFaults(base="instant"),
    "realistic": ProfileFaults(base="realistic"),
    "hardware": ProfileFaults(base="hardware"),
    "normal": ProfileFaults(),
    "slow-robotics": ProfileFaults(),
    "busy-library": ProfileFaults(),
    "intermittent-drive": ProfileFaults(load_fail_every=3),
    "session-expiry": ProfileFaults(auth_ttl_s=5.0),
    "rebooting": ProfileFaults(reboot_window_s=20.0),
    "degraded-media": ProfileFaults(checksum_retry_every=4),
}


class RetryableOpError(Exception):
    """A transient failure the caller is expected to retry."""

    def __init__(self, op_type: OpType, status: int, message: str) -> None:
        super().__init__(message)
        self.op_type = op_type
        self.status = status


class SessionExpiredError(Exception):
    """The session issued by ``ProfileRuntime.issue_session`` has expired (HTTP 401)."""


@dataclass
class ProfileRuntime:
    """Stateful, per-test application of a profile's delays and faults."""

    name: ProfileName
    clock: Clock = field(default_factory=get_clock)
    loads: int = 0
    verifies: int = 0
    checksum_retries: int = 0
    started_at: float = field(init=False)

    def __post_init__(self) -> None:
        self.started_at = self.clock.monotonic()

    @property
    def faults(self) -> ProfileFaults:
        return PROFILE_FAULTS[self.name]

    def _sleep_for(self, op_type: OpType, multiplier: float = 1.0) -> None:
        self.clock.sleep(TIMING_PROFILES[self.name].get(op_type, 0.0) * multiplier)

    def perform(self, op_type: OpType, multiplier: float = 1.0) -> None:
        """Apply delay + faults for one operation; raise on an injected fault."""
        window = self.faults.reboot_window_s
        if window and self.clock.monotonic() - self.started_at < window:
            raise RetryableOpError(op_type, 503, "library rebooting (503)")
        self._sleep_for(op_type, multiplier)
        if op_type == "tape_load":
            self.loads += 1
            every = self.faults.load_fail_every
            if every and self.loads % every == 0:
                raise RetryableOpError(op_type, 409, f"drive load {self.loads} failed (retryable)")
        if op_type == "verify":
            self.verifies += 1
            every = self.faults.checksum_retry_every
            if every and self.verifies % every == 0:
                self.checksum_retries += 1
                self._sleep_for("verify", multiplier)

    def issue_session(self) -> float:
        """Return the clock time at which a freshly issued session was created."""
        self._sleep_for("auth")
        return self.clock.monotonic()

    def check_session(self, issued_at: float) -> None:
        ttl = self.faults.auth_ttl_s
        if ttl is not None and self.clock.monotonic() - issued_at >= ttl:
            raise SessionExpiredError(f"session expired after {ttl:.1f}s")


_EMULATOR_LATENCY_OPS: tuple[OpType, ...] = (
    "auth",
    "inventory",
    "mount",
    "unmount",
    "move",
    "format",
)


def emulator_env(name: ProfileName) -> dict[str, str]:
    """Env vars to start the emulator with so its latency matches ``name``.

    Uses only hooks the emulator already reads: ``I3_TIMING_PROFILE``
    (simulator load delay; legacy names only), ``OPENBLADE_EMULATOR_LATENCY_PROFILE``
    and ``OPENBLADE_EMULATOR_LATENCY_PROFILE_MS`` (``openblade/api/aml_state.py``).
    Faults have no env hook and stay client-side in ``ProfileRuntime``.
    """
    base = PROFILE_FAULTS[name].base
    env: dict[str, str] = {"I3_TIMING_PROFILE": base, "OPENBLADE_EMULATOR_LATENCY_PROFILE": base}
    if name not in get_args(LegacyProfileName):
        delays = TIMING_PROFILES[name]
        hardware = TIMING_PROFILES["hardware"]
        env["OPENBLADE_EMULATOR_LATENCY_PROFILE_MS"] = json.dumps(
            {
                op: {
                    "instant": 0,
                    "realistic": round(delays[op] * 1000),
                    "hardware": round(hardware[op] * 1000),
                }
                for op in _EMULATOR_LATENCY_OPS
            },
            sort_keys=True,
        )
    return env


# Tolerance multipliers for hardware mode (real i3 timing can vary)
HARDWARE_TOLERANCE: dict[OpType, float] = {
    "tape_load": 0.4,  # ±40% — mechanical variation
    "tape_unload": 0.4,
    "move": 0.5,
    "rewind": 0.6,  # tape length dependent
    "format": 0.3,
    "mount": 0.3,
    "unmount": 0.3,
    "inventory": 0.5,
    "auth": 2.0,  # network latency varies widely
}


def get_profile_name() -> ProfileName:
    """Read profile from env. Defaults to 'instant' for emulator, 'hardware' for real."""
    env = os.environ.get("I3_TIMING_PROFILE", "").strip().lower()
    for name in PROFILE_NAMES:
        if env == name:
            return name
    mode = os.environ.get("I3_TEST_MODE", "emulator").strip().lower()
    return "hardware" if mode == "real" else "instant"


def get_profile() -> dict[OpType, float]:
    """Return the active timing profile dict."""
    return TIMING_PROFILES[get_profile_name()]


def wait_for_op(op_type: OpType, multiplier: float = 1.0) -> None:
    """Sleep for the appropriate time for this operation.

    In instant mode this is a no-op. Otherwise the sleep simulates the
    mechanical delay an operator would observe on a real i3. The sleep goes
    through the active ``Clock`` (see ``use_clock``). Faults are NOT applied
    here -- use ``ProfileRuntime.perform`` for fault-bearing profiles.
    """
    delay = get_profile().get(op_type, 0.0) * multiplier
    if delay > 0:
        get_clock().sleep(delay)


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
        f"Timing out of range for {op_type}: expected {lo:.1f}–{hi:.1f}s, got {elapsed:.2f}s"
    )
