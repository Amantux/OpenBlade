"""AML coordinate -> serial -> sg_inq -> /dev/tape/by-id -> LTFS mount DRY.

Resolves the device LTFS *would* mount for each drive and stops: asserts the
by-id path exists and its sg_inq serial matches the library's, then performs
no mount.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from openblade.domain.scalar_coordinate import ScalarCoordinate
from openblade.hardware.correlation import resolve_tape_by_id
from openblade.hardware.runner import SafeRunner
from openblade.hardware.sg import sg_inq

from .conftest import ApplianceClient

pytestmark = [pytest.mark.real_hardware, pytest.mark.usefixtures("real_hardware_guard")]

_DRIVE_ELEMENT_TYPE = 4  # SMC Data Transfer Element


def _drive_elements(node: Any) -> list[dict[str, Any]]:
    if isinstance(node, dict):
        coord = node.get("coordinate")
        if isinstance(coord, dict) and node.get("serialNumber"):
            return [node]
        return [e for v in node.values() for e in _drive_elements(v)]
    if isinstance(node, list):
        return [e for v in node for e in _drive_elements(v)]
    return []


def test_drive_coordinate_resolves_to_matching_by_id_device(
    authed_appliance: ApplianceClient, hardware_guard
) -> None:
    resp = authed_appliance.request("GET", "/aml/physicalLibrary/elements")
    assert resp.status == 200
    drives = [
        e
        for e in _drive_elements(resp.body)
        if ScalarCoordinate.from_dict(e["coordinate"]).element_type == _DRIVE_ELEMENT_TYPE
    ]
    if not drives:
        pytest.skip("Appliance reported no drive elements with coordinate + serialNumber")
    for element in drives:
        serial = str(element["serialNumber"])
        by_id = resolve_tape_by_id(serial)
        assert by_id is not None, f"no /dev/tape/by-id entry for drive serial {serial}"
        assert os.path.exists(by_id)
        inquiry = sg_inq(os.path.realpath(by_id), SafeRunner(dry_run=False), hardware_guard)
        assert inquiry.serial.strip().casefold() == serial.strip().casefold()
        # DRY: the LTFS mount target is resolved; no mount is performed.
