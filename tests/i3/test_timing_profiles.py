"""Unit tests for tests/i3/timing.py profiles.

Runs entirely under ``VirtualClock`` -- no emulator, no real sleeping.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator

import pytest

from tests.i3.timing import (
    PROFILE_NAMES,
    ProfileName,
    ProfileRuntime,
    RealClock,
    RetryableOpError,
    SessionExpiredError,
    VirtualClock,
    emulator_env,
    get_clock,
    get_profile_name,
    use_clock,
    wait_for_op,
)

_MAX_WALL_S = 1.0


@pytest.fixture
def vclock(monkeypatch: pytest.MonkeyPatch) -> Iterator[VirtualClock]:
    monkeypatch.delenv("I3_TIMING_PROFILE", raising=False)
    monkeypatch.delenv("I3_TEST_MODE", raising=False)
    monkeypatch.setenv("I3_FAULTS_SERVER_SIDE", "0")
    clock = VirtualClock()
    started = time.monotonic()
    with use_clock(clock):
        yield clock
    assert time.monotonic() - started < _MAX_WALL_S, "profile test slept for real"


def _runtime(name: ProfileName, clock: VirtualClock) -> ProfileRuntime:
    return ProfileRuntime(name, clock=clock)


# --- Clock ------------------------------------------------------------------


def test_virtual_clock_sleep_advances_and_records() -> None:
    clock = VirtualClock(start=10.0)

    clock.sleep(2.5)
    clock.advance(1.0)

    assert clock.monotonic() == 13.5
    assert clock.sleeps == [2.5]


def test_virtual_clock_negative_duration_raises() -> None:
    clock = VirtualClock()

    with pytest.raises(ValueError):
        clock.sleep(-1.0)
    with pytest.raises(ValueError):
        clock.advance(-1.0)


def test_use_clock_restores_previous_clock() -> None:
    before = get_clock()

    with use_clock(VirtualClock()) as installed:
        assert get_clock() is installed

    assert get_clock() is before
    assert isinstance(before, RealClock)


# --- Profile selection --------------------------------------------------------


@pytest.mark.parametrize("name", PROFILE_NAMES)
def test_profile_name_from_env_resolves(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("I3_TIMING_PROFILE", f"  {name.upper()} ")

    assert get_profile_name() == name


def test_unknown_profile_falls_back_to_mode_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("I3_TIMING_PROFILE", "bogus")
    monkeypatch.setenv("I3_TEST_MODE", "real")

    assert get_profile_name() == "hardware"


# --- Delays through the clock -------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"), [("instant", []), ("realistic", [3.0]), ("hardware", [35.0])]
)
def test_wait_for_op_legacy_profiles_sleep_via_clock(
    vclock: VirtualClock, monkeypatch: pytest.MonkeyPatch, name: str, expected: list[float]
) -> None:
    monkeypatch.setenv("I3_TIMING_PROFILE", name)

    wait_for_op("tape_load")

    assert vclock.sleeps == expected


def test_wait_for_op_hardware_rewind_does_not_really_sleep(
    vclock: VirtualClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("I3_TIMING_PROFILE", "hardware")

    wait_for_op("rewind", multiplier=2.0)

    assert vclock.monotonic() == 180.0


def test_slow_robotics_move_is_three_times_normal(vclock: VirtualClock) -> None:
    normal, slow = _runtime("normal", vclock), _runtime("slow-robotics", vclock)

    normal.perform("move")
    slow.perform("move")

    assert vclock.sleeps == [1.5, 4.5]


def test_busy_library_adds_queue_wait_to_mechanical_ops_only(vclock: VirtualClock) -> None:
    busy = _runtime("busy-library", vclock)

    busy.perform("tape_load")
    busy.perform("auth")

    assert vclock.sleeps == [5.0, 0.1]


# --- Faults -----------------------------------------------------------------


def _load_outcomes(runtime: ProfileRuntime, count: int) -> list[int | None]:
    outcomes: list[int | None] = []
    for _ in range(count):
        try:
            runtime.perform("tape_load")
            outcomes.append(None)
        except RetryableOpError as exc:
            outcomes.append(exc.status)
    return outcomes


def test_intermittent_drive_every_third_load_fails_retryably(vclock: VirtualClock) -> None:
    runtime = _runtime("intermittent-drive", vclock)

    outcomes = _load_outcomes(runtime, 6)

    assert outcomes == [None, None, 409, None, None, 409]


def test_normal_profile_loads_never_fail(vclock: VirtualClock) -> None:
    assert _load_outcomes(_runtime("normal", vclock), 6) == [None] * 6


def test_session_expiry_rejects_session_after_ttl(vclock: VirtualClock) -> None:
    runtime = _runtime("session-expiry", vclock)
    issued = runtime.issue_session()

    vclock.advance(4.9)
    runtime.check_session(issued)
    vclock.advance(0.1)

    with pytest.raises(SessionExpiredError):
        runtime.check_session(issued)


def test_normal_profile_session_survives_an_hour(vclock: VirtualClock) -> None:
    runtime = _runtime("normal", vclock)
    issued = runtime.issue_session()

    vclock.advance(3600.0)

    runtime.check_session(issued)


def test_rebooting_returns_503_until_window_closes(vclock: VirtualClock) -> None:
    runtime = _runtime("rebooting", vclock)
    statuses: list[int] = []

    while True:
        try:
            runtime.perform("inventory")
            break
        except RetryableOpError as exc:
            statuses.append(exc.status)
            vclock.sleep(5.0)

    assert statuses == [503, 503, 503, 503]
    assert vclock.monotonic() == 22.0


def test_degraded_media_verify_slow_with_checksum_retry(vclock: VirtualClock) -> None:
    runtime = _runtime("degraded-media", vclock)

    for _ in range(4):
        runtime.perform("verify")
    runtime.perform("read")

    assert runtime.checksum_retries == 1
    assert vclock.sleeps == [9.0, 9.0, 9.0, 9.0, 9.0, 6.0]


def test_normal_profile_verify_has_no_checksum_retry(vclock: VirtualClock) -> None:
    runtime = _runtime("normal", vclock)

    for _ in range(8):
        runtime.perform("verify")

    assert runtime.checksum_retries == 0


# --- Emulator env wiring ------------------------------------------------------


@pytest.mark.parametrize("name", ["instant", "realistic", "hardware"])
def test_emulator_env_legacy_profiles_unchanged(name: ProfileName) -> None:
    assert emulator_env(name) == {
        "I3_TIMING_PROFILE": name,
        "OPENBLADE_EMULATOR_LATENCY_PROFILE": name,
    }


def test_emulator_env_latency_override_accepted_by_emulator_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openblade.api.aml_state import _env_emulator_latency_profile_ms

    env = emulator_env("slow-robotics")
    monkeypatch.setenv(
        "OPENBLADE_EMULATOR_LATENCY_PROFILE_MS", env["OPENBLADE_EMULATOR_LATENCY_PROFILE_MS"]
    )

    parsed = _env_emulator_latency_profile_ms()

    assert env["OPENBLADE_EMULATOR_LATENCY_PROFILE"] == "realistic"
    assert parsed == json.loads(env["OPENBLADE_EMULATOR_LATENCY_PROFILE_MS"])
    assert parsed is not None and parsed["move"]["realistic"] == 4500


def test_profile_runtime_server_side_faults_skips_client_injection(
    vclock: VirtualClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("I3_FAULTS_SERVER_SIDE", "1")
    runtime = ProfileRuntime("intermittent-drive", clock=vclock)

    for _ in range(6):
        runtime.perform("tape_load")
    runtime.check_session(runtime.issue_session() - 3600.0)

    assert runtime.loads == 6
