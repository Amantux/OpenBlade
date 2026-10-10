from __future__ import annotations

import pytest

from openblade.api import aml_faults
from openblade.api.aml_faults import (
    FaultProfile,
    FaultProfileError,
    FaultState,
    parse_fault_profile,
)


def test_unset_means_no_faults() -> None:
    assert parse_fault_profile(None) == FaultProfile()
    assert parse_fault_profile("  ") == FaultProfile()
    assert not parse_fault_profile("instant").active


def test_presets_resolve() -> None:
    assert parse_fault_profile("intermittent-drive").load_fail_every == 3
    assert parse_fault_profile("Session-Expiry").auth_ttl_s == 5.0
    assert parse_fault_profile("rebooting").reboot_window_s == 20.0
    assert parse_fault_profile("degraded-media").checksum_retry_every == 4


def test_json_object() -> None:
    profile = parse_fault_profile('{"load_fail_every": 2, "auth_ttl_s": 1}')
    assert profile == FaultProfile(load_fail_every=2, auth_ttl_s=1.0)


@pytest.mark.parametrize(
    "raw",
    [
        "no-such-profile",
        "{not json",
        "[1]",
        '{"bogus": 1}',
        '{"load_fail_every": 1.5}',
        '{"load_fail_every": true}',
        '{"auth_ttl_s": -1}',
        '{"reboot_window_s": "20"}',
    ],
)
def test_strict_parse_rejects(raw: str) -> None:
    with pytest.raises(FaultProfileError):
        parse_fault_profile(raw)


def test_counters() -> None:
    state = FaultState(FaultProfile(load_fail_every=3, checksum_retry_every=2))
    assert [state.should_fail_load() for _ in range(6)] == [False, False, True] * 2
    assert [state.should_retry_checksum() for _ in range(4)] == [False, True] * 2
    assert not state.in_reboot_window()


def test_reboot_window(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    monkeypatch.setattr(aml_faults.time, "monotonic", lambda: now[0])
    state = FaultState(FaultProfile(reboot_window_s=20.0))
    assert state.in_reboot_window()
    assert state.reboot_retry_after_s() == 20
    now[0] = 120.0
    assert not state.in_reboot_window()


def test_env_state_cached_and_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(aml_faults.FAULT_PROFILE_ENV, "rebooting")
    aml_faults.reset_fault_state()
    try:
        assert aml_faults.get_fault_state().profile.reboot_window_s == 20.0
        monkeypatch.setenv(aml_faults.FAULT_PROFILE_ENV, "bad")
        assert aml_faults.get_fault_state().profile.reboot_window_s == 20.0
        aml_faults.reset_fault_state()
        with pytest.raises(FaultProfileError):
            aml_faults.get_fault_state()
    finally:
        aml_faults.reset_fault_state()
