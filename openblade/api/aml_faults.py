"""Env-driven AML wire-level fault injection for the emulator.

``OPENBLADE_EMULATOR_FAULT_PROFILE`` is either a preset name (see ``PRESETS``) or a
JSON object with any of the keys ``load_fail_every`` (int), ``auth_ttl_s`` (float),
``reboot_window_s`` (float) and ``checksum_retry_every`` (int). Unset/empty means
no faults. Anything unparseable raises ``FaultProfileError``: a silent no-fault run
would be a false green for the scenario that asked for faults.

The presets are the single source of truth for the i3 timing profiles
(``tests/i3/timing.py`` imports them).
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, fields

FAULT_PROFILE_ENV = "OPENBLADE_EMULATOR_FAULT_PROFILE"


class FaultProfileError(ValueError):
    """``OPENBLADE_EMULATOR_FAULT_PROFILE`` could not be parsed."""


@dataclass(frozen=True)
class FaultProfile:
    """Fault knobs (0/None = off)."""

    load_fail_every: int = 0
    auth_ttl_s: float | None = None
    reboot_window_s: float = 0.0
    checksum_retry_every: int = 0

    @property
    def active(self) -> bool:
        return self != FaultProfile()


PRESETS: dict[str, FaultProfile] = {
    "instant": FaultProfile(),
    "realistic": FaultProfile(),
    "hardware": FaultProfile(),
    "normal": FaultProfile(),
    "slow-robotics": FaultProfile(),
    "busy-library": FaultProfile(),
    "intermittent-drive": FaultProfile(load_fail_every=3),
    "session-expiry": FaultProfile(auth_ttl_s=5.0),
    "rebooting": FaultProfile(reboot_window_s=20.0),
    "degraded-media": FaultProfile(checksum_retry_every=4),
}

_INT_KEYS = ("load_fail_every", "checksum_retry_every")
_FLOAT_KEYS = ("auth_ttl_s", "reboot_window_s")


def parse_fault_profile(raw: str | None) -> FaultProfile:
    """Strictly parse a preset name or JSON object; raise ``FaultProfileError``."""
    text = (raw or "").strip()
    if not text:
        return FaultProfile()
    if not text.startswith("{"):
        try:
            return PRESETS[text.lower()]
        except KeyError:
            raise FaultProfileError(
                f"{FAULT_PROFILE_ENV}: unknown preset {text!r}; expected one of "
                f"{sorted(PRESETS)} or a JSON object"
            ) from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise FaultProfileError(f"{FAULT_PROFILE_ENV}: invalid JSON") from None
    if not isinstance(data, dict):
        raise FaultProfileError(f"{FAULT_PROFILE_ENV}: JSON must be an object")
    known = {f.name for f in fields(FaultProfile)}
    unknown = sorted(set(data) - known)
    if unknown:
        raise FaultProfileError(f"{FAULT_PROFILE_ENV}: unknown keys {unknown}")
    values: dict[str, int | float] = {}
    for key, value in data.items():
        # bool is an int subclass; reject it explicitly.
        if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
            raise FaultProfileError(f"{FAULT_PROFILE_ENV}: {key} must be a number >= 0")
        if key in _INT_KEYS:
            if not isinstance(value, int):
                raise FaultProfileError(f"{FAULT_PROFILE_ENV}: {key} must be an integer")
            values[key] = value
        else:
            values[key] = float(value)
    return FaultProfile(
        load_fail_every=int(values.get("load_fail_every", 0)),
        auth_ttl_s=float(values["auth_ttl_s"]) if "auth_ttl_s" in values else None,
        reboot_window_s=float(values.get("reboot_window_s", 0.0)),
        checksum_retry_every=int(values.get("checksum_retry_every", 0)),
    )


def load_fault_profile_from_env() -> FaultProfile:
    return parse_fault_profile(os.environ.get(FAULT_PROFILE_ENV))


class FaultState:
    """Process-wide fault counters (thread-safe)."""

    def __init__(self, profile: FaultProfile) -> None:
        self.profile = profile
        self.started_at = time.monotonic()
        self.loads = 0
        self.verifies = 0
        self._lock = threading.Lock()

    def in_reboot_window(self) -> bool:
        window = self.profile.reboot_window_s
        return bool(window) and time.monotonic() - self.started_at < window

    def reboot_retry_after_s(self) -> int:
        remaining = self.profile.reboot_window_s - (time.monotonic() - self.started_at)
        return max(1, int(remaining + 0.999))

    def should_fail_load(self) -> bool:
        every = self.profile.load_fail_every
        if not every:
            return False
        with self._lock:
            self.loads += 1
            return self.loads % every == 0

    def should_retry_checksum(self) -> bool:
        every = self.profile.checksum_retry_every
        if not every:
            return False
        with self._lock:
            self.verifies += 1
            return self.verifies % every == 0


_state: FaultState | None = None
_state_lock = threading.Lock()


def get_fault_state() -> FaultState:
    """Return the process-wide state, parsing the env on first use (may raise)."""
    global _state
    with _state_lock:
        if _state is None:
            _state = FaultState(load_fault_profile_from_env())
        return _state


def reset_fault_state() -> None:
    """Drop the cached state so the next call re-reads the env (tests)."""
    global _state
    with _state_lock:
        _state = None
