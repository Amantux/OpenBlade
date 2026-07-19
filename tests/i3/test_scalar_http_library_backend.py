"""Contract tests for ScalarHttpLibraryBackend against the in-process emulator.

Verifies the read/control-plane mapping (AML physicalLibrary/elements ->
LibraryInventory) at the backend boundary, deriving expected values from the live
inventory rather than hard-coding emulator seeds.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from openblade.api import aml_state
from openblade.api.main import app
from openblade.bootstrap import get_context
from openblade.domain.errors import OpenBladeError
from openblade.domain.models import LibraryInventory
from openblade.hardware.scalar_http import ScalarHttpLibraryBackend, ScalarHttpSession


@pytest.fixture
def backend() -> Generator[ScalarHttpLibraryBackend, None, None]:
    aml_state.ensure_initialized(get_context().config.db_url, force_reset=True)
    with TestClient(app) as client:
        session = ScalarHttpSession(client, username="admin", password="password")
        yield ScalarHttpLibraryBackend(session, library_id="test-i3")


def test_inventory_returns_slots_and_drives(backend: ScalarHttpLibraryBackend) -> None:
    inventory = backend.inventory()

    assert isinstance(inventory, LibraryInventory)
    assert inventory.library_id == "test-i3"
    assert inventory.slots, "expected the emulator to report storage slots"
    assert inventory.drives, "expected the emulator to report drives"


def test_occupied_slots_carry_a_barcode(backend: ScalarHttpLibraryBackend) -> None:
    occupied = [slot for slot in backend.inventory().slots if slot.occupied]

    assert occupied, "expected at least one occupied slot"
    assert all(slot.barcode is not None for slot in occupied)


def test_find_slot_by_barcode_roundtrips_a_real_barcode(
    backend: ScalarHttpLibraryBackend,
) -> None:
    occupied = next(slot for slot in backend.inventory().slots if slot.occupied)
    assert occupied.barcode is not None

    found_slot_id = backend.find_slot_by_barcode(occupied.barcode.value)

    assert found_slot_id == occupied.slot_id


def test_find_slot_by_barcode_returns_none_for_unknown(
    backend: ScalarHttpLibraryBackend,
) -> None:
    assert backend.find_slot_by_barcode("ZZ999ZZ9") is None


def test_get_drive_returns_known_drive(backend: ScalarHttpLibraryBackend) -> None:
    drive_id = backend.inventory().drives[0].drive_id

    assert backend.get_drive(drive_id).drive_id == drive_id


def test_get_drive_unknown_raises(backend: ScalarHttpLibraryBackend) -> None:
    with pytest.raises(OpenBladeError):
        backend.get_drive(9999)


def test_backend_satisfies_library_read_protocol(backend: ScalarHttpLibraryBackend) -> None:
    # The methods the archive/restore orchestration and LTFS layer call.
    for name in ("inventory", "get_drive", "get_slot", "find_slot_by_barcode",
                 "find_drive_by_barcode", "get_all_barcodes"):
        assert callable(getattr(backend, name))
