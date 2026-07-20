"""Roundtrip tests for the scalar_http robotics write path (moveMedium).

Drives the real i3 dialect (POST /aml/media/operations/moveMedium) end-to-end
against the in-process emulator and confirms the physical change is observable in
the next inventory read.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from openblade.api import aml_state
from openblade.api.main import app
from openblade.bootstrap import get_context
from openblade.hardware.scalar_http import ScalarHttpLibraryBackend, ScalarHttpSession


@pytest.fixture
def backend() -> Generator[ScalarHttpLibraryBackend, None, None]:
    aml_state.ensure_initialized(get_context().config.db_url, force_reset=True)
    with TestClient(app) as client:
        session = ScalarHttpSession(client, username="admin", password="password")
        yield ScalarHttpLibraryBackend(session)


def _first_occupied_slot(backend: ScalarHttpLibraryBackend) -> int:
    return next(slot.slot_id for slot in backend.inventory().slots if slot.occupied)


def _first_empty_drive(backend: ScalarHttpLibraryBackend) -> int:
    return next(drive.drive_id for drive in backend.inventory().drives if drive.barcode is None)


def test_load_moves_cartridge_from_slot_into_drive(backend: ScalarHttpLibraryBackend) -> None:
    slot_id = _first_occupied_slot(backend)
    drive_id = _first_empty_drive(backend)
    barcode = backend.get_slot(slot_id).barcode
    assert barcode is not None

    result = backend.load(slot_id, drive_id)

    assert result.success, result.message
    inventory = backend.inventory()
    assert backend.get_slot(slot_id).barcode is None  # source slot now empty
    loaded = next(d for d in inventory.drives if d.drive_id == drive_id)
    assert loaded.barcode is not None and loaded.barcode.value == barcode.value


def test_load_then_unload_returns_cartridge_to_a_slot(backend: ScalarHttpLibraryBackend) -> None:
    slot_id = _first_occupied_slot(backend)
    drive_id = _first_empty_drive(backend)
    barcode = backend.get_slot(slot_id).barcode
    assert barcode is not None

    assert backend.load(slot_id, drive_id).success
    unload_result = backend.unload(drive_id, slot_id)

    assert unload_result.success, unload_result.message
    assert backend.get_drive(drive_id).barcode is None  # drive empty again
    assert backend.find_slot_by_barcode(barcode.value) is not None  # back in a slot


def test_move_between_two_slots(backend: ScalarHttpLibraryBackend) -> None:
    inventory = backend.inventory()
    source = next(s.slot_id for s in inventory.slots if s.occupied)
    target = next(s.slot_id for s in inventory.slots if not s.occupied)
    barcode = backend.get_slot(source).barcode
    assert barcode is not None

    result = backend.move(source, target)

    assert result.success, result.message
    assert backend.get_slot(source).barcode is None
    assert backend.find_slot_by_barcode(barcode.value) == target


def test_load_into_nonexistent_drive_fails_gracefully(backend: ScalarHttpLibraryBackend) -> None:
    slot_id = _first_occupied_slot(backend)

    result = backend.load(slot_id, 9999)

    assert result.success is False  # returned, not raised


def test_unload_returns_cartridge_to_the_specified_slot(
    backend: ScalarHttpLibraryBackend,
) -> None:
    # Regression for the moveClass=8 unload path: the cartridge must land in the
    # SPECIFIC target slot the client asked for, not merely "some" slot.
    slot_id = _first_occupied_slot(backend)
    drive_id = _first_empty_drive(backend)
    barcode = backend.get_slot(slot_id).barcode
    assert barcode is not None
    assert backend.load(slot_id, drive_id).success  # source slot now empty

    result = backend.unload(drive_id, slot_id)

    assert result.success, result.message
    landed = backend.get_slot(slot_id).barcode
    assert landed is not None and landed.value == barcode.value
    assert backend.find_drive_by_barcode(barcode.value) is None  # drive is empty


def test_unload_rejects_a_drive_destination(backend: ScalarHttpLibraryBackend) -> None:
    # moveClass=8 with a DRIVE destination must be rejected (slot/drive addresses
    # overlap, so honoring it would silently misroute the cartridge).
    slot_id = _first_occupied_slot(backend)
    drive_id = _first_empty_drive(backend)
    assert backend.load(slot_id, drive_id).success

    body = {
        "moveMedium": {
            "sourceCoordinate": {"elementType": "drive", "elementAddress": drive_id},
            "destinationCoordinate": {"elementType": "drive", "elementAddress": drive_id},
            "moveClass": 8,
        }
    }
    resp = backend._session.request("POST", "/aml/media/operations/moveMedium", json=body)
    assert resp.status_code == 422
