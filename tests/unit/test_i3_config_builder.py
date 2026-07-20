"""Unit tests for the parameterized Scalar i3 config builder.

Locks the byte-identical default, validates the env-driven active config + override
precedence, and asserts invalid configurations fail fast.
"""

from __future__ import annotations

import pytest

from openblade.simulator.i3_config import (
    DEFAULT_PROFILE,
    NAMED_PROFILES,
    ScalarI3Profile,
    build_scalar_i3_config,
    scalar_i3_active_config,
    scalar_i3_data_barcodes,
    scalar_i3_default_config,
)

_EMU_ENV = (
    "EMULATOR_PROFILE",
    "EMULATOR_SLOT_COUNT",
    "EMULATOR_DRIVE_COUNT",
    "EMULATOR_OCCUPANCY_PERCENT",
)


@pytest.fixture(autouse=True)
def _clear_emulator_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _EMU_ENV:
        monkeypatch.delenv(name, raising=False)


def test_default_profile_shape() -> None:
    config = scalar_i3_default_config()
    data = [m for m in config["media"] if m["role"] == "data"]
    cleaning = [m for m in config["media"] if m["role"] == "cleaning"]
    assert config["partition"]["slotCount"] == 50
    assert [d["type"] for d in config["drives"]] == ["LTO-7", "LTO-7", "LTO-8"]
    assert len(data) == 28
    assert len(cleaning) == 3
    assert config["partition"]["ieSlotCount"] == 2
    assert "partitions" not in config  # single-partition profiles stay additive-key-free


def test_default_rebuild_is_deterministic_and_independent() -> None:
    # Two calls are equal (locks the shape) but not the same object (deep-copied).
    first = scalar_i3_default_config()
    second = scalar_i3_default_config()
    assert first == second
    assert first is not second
    first["drives"][0]["status"] = "mutated"
    assert scalar_i3_default_config()["drives"][0]["status"] == "online"


def test_active_config_equals_default_without_env() -> None:
    assert scalar_i3_active_config() == scalar_i3_default_config()


def test_data_barcodes_stay_default_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    # Demo NAS seeding depends on these; they must not follow the active profile.
    monkeypatch.setenv("EMULATOR_PROFILE", "scalar-i3-25-1")
    assert len(scalar_i3_data_barcodes()) == 28  # unchanged regardless of active profile
    assert scalar_i3_data_barcodes()[0] == "VOL001L9"


@pytest.mark.parametrize("name", sorted(NAMED_PROFILES))
def test_named_profiles_build(name: str) -> None:
    profile = NAMED_PROFILES[name]
    config = build_scalar_i3_config(profile)
    assert config["partition"]["slotCount"] == profile.slot_count
    assert len(config["drives"]) == profile.drive_count
    cleaning = [m for m in config["media"] if m["role"] == "cleaning"]
    assert len(cleaning) == profile.drive_count  # one cleaning slot per drive


def test_env_profile_and_override_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMULATOR_PROFILE", "scalar-i3-50-6")
    assert len(scalar_i3_active_config()["drives"]) == 6
    monkeypatch.setenv("EMULATOR_DRIVE_COUNT", "4")  # per-field override wins
    config = scalar_i3_active_config()
    assert len(config["drives"]) == 4
    assert config["partition"]["slotCount"] == 50


def test_env_parsed_profile_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMULATOR_PROFILE", "scalar-i3-40-2")
    config = scalar_i3_active_config()
    assert config["partition"]["slotCount"] == 40
    assert len(config["drives"]) == 2


@pytest.mark.parametrize(
    "kwargs",
    [
        {"drive_generation_mix": tuple(["LTO-8"] * 7)},  # >6 drives
        {"drive_generation_mix": ()},  # 0 drives
        {"occupancy_percent": 101},
        {"slot_count": 2},  # < drive_count + 1
        {"drive_generation_mix": ("LTO-6", "LTO-7", "LTO-8")},  # unknown gen
        {"partition_count": 9},  # > drive_count
    ],
)
def test_invalid_config_fails_fast(kwargs: dict) -> None:
    base = dict(
        profile_name="invalid",
        slot_count=50,
        drive_generation_mix=("LTO-7", "LTO-7", "LTO-8"),
        occupancy_percent=60,
    )
    base.update(kwargs)
    with pytest.raises(ValueError):
        build_scalar_i3_config(ScalarI3Profile(**base))


def test_bad_env_profile_name_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMULATOR_PROFILE", "totally-bogus")
    with pytest.raises(ValueError):
        scalar_i3_active_config()


def test_default_profile_is_registered() -> None:
    assert NAMED_PROFILES["scalar-i3-50-3"] is DEFAULT_PROFILE
