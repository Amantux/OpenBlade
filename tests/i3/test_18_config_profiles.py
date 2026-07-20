"""Compliance suite for configurable Scalar i3 profiles.

Proves the emulator honours the contracted EMULATOR_* configuration knobs and that
every supported configuration stays internally + behaviourally consistent — the
same invariants a real i3 would satisfy. Two layers:

- builder invariants over every named profile (no server needed);
- live reflection: boot the app under a non-default profile and confirm /aml
  reports the selected shape (the aml_state repoint end-to-end).
"""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig
from openblade.simulator.i3_config import (
    _CAPACITY_BY_GENERATION,
    NAMED_PROFILES,
    ScalarI3Profile,
    build_scalar_i3_config,
)

pytestmark = pytest.mark.i3

_COORD_RE = re.compile(r"\A1,[12],\d+\Z")

_EMU_ENV = (
    "EMULATOR_PROFILE",
    "EMULATOR_SLOT_COUNT",
    "EMULATOR_DRIVE_COUNT",
    "EMULATOR_OCCUPANCY_PERCENT",
)


# --------------------------------------------------------------------------- #
# Builder invariants — every named profile must be internally consistent.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name", sorted(NAMED_PROFILES))
def test_named_profile_is_internally_consistent(name: str) -> None:
    profile = NAMED_PROFILES[name]
    config = build_scalar_i3_config(profile)

    data = [m for m in config["media"] if m["role"] == "data"]
    cleaning = [m for m in config["media"] if m["role"] == "cleaning"]

    # 1. Slot totals and drive counts match the profile; drives within the i3 max.
    assert config["partition"]["slotCount"] == profile.slot_count
    assert len(config["drives"]) == profile.drive_count
    assert 1 <= profile.drive_count <= 6

    # 2. Addressing: every slot/drive coordinate is a well-formed 1,bay,slot; unique.
    slot_coords = [m["slotAddress"] for m in config["media"]]
    assert all(_COORD_RE.match(c) for c in slot_coords)
    assert all(_COORD_RE.match(d["location"]) for d in config["drives"])
    assert len(slot_coords) == len(set(slot_coords))  # no two cartridges share a slot

    # 3. Exactly one cleaning slot per drive, at the top-most slot ids.
    assert len(cleaning) == profile.drive_count
    cleaning_slot_ids = sorted(int(m["mockSlotId"]) for m in cleaning)
    assert cleaning_slot_ids == list(
        range(profile.slot_count - profile.drive_count + 1, profile.slot_count + 1)
    )
    for item in cleaning:
        assert str(item["type"]).endswith("-CLN")

    # 4. Data-tape count follows occupancy; occupied + empty == total.
    fillable = profile.slot_count - profile.drive_count
    assert len(data) == round(profile.occupancy_percent / 100 * fillable)
    occupied = len(data) + len(cleaning)
    assert occupied <= profile.slot_count

    # 5. Each cartridge capacity matches its LTO generation (incl. LTO-9 = 18TB).
    for item in data:
        assert item["capacityBytes"] == _CAPACITY_BY_GENERATION[item["type"]]

    # 6. Data media generations are all readable by some drive in the library.
    drive_generations = {str(d["type"]) for d in config["drives"]}
    assert {str(m["type"]) for m in data} <= drive_generations

    # 7. Partition bookkeeping.
    if profile.partition_count > 1:
        partitions = config["partitions"]
        assert len(partitions) == profile.partition_count
        assert sum(p["slotCount"] for p in partitions) == profile.slot_count
    else:
        assert "partitions" not in config
    assert config["partition"]["ieSlotCount"] == profile.ie_slot_count


@pytest.mark.parametrize(
    "mix,slot_count,occupancy,partitions",
    [
        (("LTO-8",) * 7, 50, 60, 1),  # > 6 drives (exceeds i3 max)
        ((), 50, 60, 1),  # 0 drives
        (("LTO-7", "LTO-8"), 50, 101, 1),  # occupancy out of range
        (("LTO-7", "LTO-8"), 2, 60, 1),  # slot_count < drive_count + 1
        (("LTO-6", "LTO-8"), 50, 60, 1),  # unknown generation
        (("LTO-7", "LTO-8"), 50, 60, 9),  # partitions > drives
    ],
)
def test_invalid_profile_fails_fast(mix, slot_count, occupancy, partitions) -> None:
    profile = ScalarI3Profile(
        profile_name="invalid",
        slot_count=slot_count,
        drive_generation_mix=mix,
        occupancy_percent=occupancy,
        partition_count=partitions,
    )
    with pytest.raises(ValueError):
        build_scalar_i3_config(profile)


# --------------------------------------------------------------------------- #
# Live reflection — the running app must report the env-selected shape.
# --------------------------------------------------------------------------- #

def _client_for_profile(tmp_path) -> TestClient:
    # The mock robotics backend is built at context creation, so the env must be set
    # before create_context(); aml_state reads it per-request.
    reset_context(
        create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'profile.db'}"))
    )
    return TestClient(app)


@pytest.fixture
def _restore_default_context() -> Iterator[None]:
    yield
    reset_context(create_context())


@pytest.mark.parametrize("profile_name", ["scalar-i3-25-1", "scalar-i3-50-6"])
def test_running_app_reflects_selected_profile(
    profile_name: str,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    _restore_default_context: None,
) -> None:
    for name in _EMU_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("EMULATOR_PROFILE", profile_name)
    expected = build_scalar_i3_config(NAMED_PROFILES[profile_name])
    expected_slots = expected["partition"]["slotCount"]
    expected_drive_count = len(expected["drives"])

    client = _client_for_profile(tmp_path)
    login = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert login.status_code == 200
    auth = {"Cookie": f"sessionID={login.cookies.get('sessionID')}"}

    library = client.get("/aml/library", headers=auth).json()["library"]
    assert library["slotsTotal"] == expected_slots

    inventory = client.get("/aml/library/inventory", headers=auth).json()
    assert len(inventory.get("slots") or []) == expected_slots
    assert len(inventory.get("drives") or []) == expected_drive_count

    drives_payload = client.get("/aml/drives", headers=auth).json()
    drives = drives_payload.get("drives") or (drives_payload.get("driveList") or {}).get("drive") or []
    assert len(drives) == expected_drive_count
