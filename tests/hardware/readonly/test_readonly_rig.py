"""Read-only checks against the real i3 rig (nightly lane).

Nothing here loads, moves, formats or mounts anything — enforced by
test_readonly_ast_safety.py. Skipped without the rig (see conftest gating).
"""

from __future__ import annotations

import json
import os
import tempfile
import warnings
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from openblade.domain.scalar_coordinate import ScalarCoordinate
from openblade.hardware.sg import sg_inq
from openblade.hardware.tapealert import read_tape_alerts
from openblade.jobs.inventory import InventoryService

from .conftest import CORPUS_DIR, ApplianceClient, ReadOnlyRunner, read_corpus

pytestmark = [pytest.mark.real_hardware, pytest.mark.usefixtures("real_hardware_guard")]


def test_appliance_login_then_logout_succeeds(appliance: ApplianceClient) -> None:
    assert appliance.login().status == 200
    assert appliance.logout().status in {200, 204}


def test_changer_firmware_and_identity_are_discoverable(changer_device, hardware_guard) -> None:
    inquiry = sg_inq(changer_device, ReadOnlyRunner(dry_run=False), hardware_guard)
    assert inquiry.vendor and inquiry.product and inquiry.revision


def test_inventory_snapshot_reports_slots_and_drives(real_library_backend) -> None:
    inventory = InventoryService(real_library_backend).snapshot()
    assert inventory.slots and inventory.drives


def test_element_coordinates_round_trip(authed_appliance: ApplianceClient) -> None:
    resp = authed_appliance.request("GET", "/aml/physicalLibrary/elements")
    assert resp.status == 200
    flat = _find_coordinates(resp.body)
    assert flat, "appliance returned no element coordinates"
    for raw in flat:
        assert ScalarCoordinate.from_dict(raw).to_dict() == {k: raw[k] for k in _COORD_KEYS}


def test_partitions_are_visible(authed_appliance: ApplianceClient) -> None:
    resp = authed_appliance.request("GET", "/aml/partitions")
    assert resp.status == 200 and resp.body


def test_tapealert_health_reads_on_every_drive(drive_devices, hardware_guard) -> None:
    for device in drive_devices:
        report = read_tape_alerts(device, ReadOnlyRunner(dry_run=False), hardware_guard)
        assert report.device


_COORD_KEYS = ("frame", "rack", "section", "column", "row", "type")


def _find_coordinates(node: Any) -> list[dict[str, Any]]:
    if isinstance(node, dict):
        if all(k in node for k in _COORD_KEYS):
            return [node]
        return [c for v in node.values() for c in _find_coordinates(v)]
    if isinstance(node, list):
        return [c for v in node for c in _find_coordinates(v)]
    return []


def _report_path() -> Path:
    default = Path(tempfile.gettempdir()) / "differential-report.json"
    return Path(os.environ.get("OPENBLADE_DIFFERENTIAL_REPORT", str(default)))


# The differential must compare SOMETHING: an empty/mis-filtered corpus would
# otherwise produce zero divergences and pass vacuously.
MIN_DIFFERENTIAL_CASES = 1


def _is_read_only(case: dict[str, Any]) -> bool:
    req = case["request"]
    return req["method"] == "GET" or req["path"] in {"/aml/users/login", "/aml/auth/logout"}


def test_emulator_matches_appliance_on_compatibility_corpus(
    appliance: ApplianceClient, tmp_path: Path
) -> None:
    from openblade.api.main import app
    from openblade.bootstrap import create_context, reset_context
    from openblade.config import OpenBladeConfig

    reset_context(create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'diff.db'}")))
    emulator = TestClient(app)
    divergences: list[dict[str, Any]] = []
    cases = [c for c in read_corpus() if _is_read_only(c)]
    assert len(cases) >= MIN_DIFFERENTIAL_CASES, (
        f"differential ran {len(cases)} read-only corpus case(s) from {CORPUS_DIR}; "
        f"need >= {MIN_DIFFERENTIAL_CASES} (refusing to pass vacuously)"
    )
    for case in cases:
        req = case["request"]
        if case.get("auth") == "admin":
            emulator.post("/aml/users/login", json={"name": "admin", "password": "password"})
            appliance.login()
        # Credentials in corpus cases are emulator defaults; the appliance gets the rig's.
        body = req.get("json")
        if req["path"] == "/aml/users/login" and body and body.get("password") == "password":
            body = {"name": appliance.user, "password": appliance.password}
        emu = emulator.request(req["method"], req["path"], json=req.get("json"))
        real = appliance.request(req["method"], req["path"], body)
        if emu.status_code != real.status:
            divergences.append(
                {
                    "id": case["id"],
                    "source": case["source"],
                    "path": req["path"],
                    "emulator_status": emu.status_code,
                    "appliance_status": real.status,
                }
            )
    _report_path().write_text(json.dumps({"divergences": divergences}, indent=2))
    captured = [d for d in divergences if d["source"] == "captured"]
    for d in divergences:
        if d["source"] != "captured":
            warnings.warn(f"inferred case diverges from appliance: {d}", stacklevel=1)
    assert not captured, f"captured-case divergence (see {_report_path()}): {captured}"
