"""LibraryBackend behavioural contract, run against every backend pairing."""

from __future__ import annotations

import pytest

from openblade.domain.backends import LibraryBackend, LTFSBackend
from openblade.domain.errors import OpenBladeError
from openblade.jobs.inventory import InventoryService
from tests.contract.conftest import BackendPair

pytestmark = pytest.mark.contract


def _home_slot(library: LibraryBackend, barcode: str) -> int:
    slot = library.find_slot_by_barcode(barcode)
    assert slot is not None, f"{barcode} not found in any slot"
    return slot


def test_backends_satisfy_runtime_protocols(backend_pair: BackendPair) -> None:
    assert isinstance(backend_pair.library, LibraryBackend)
    assert isinstance(backend_pair.ltfs, LTFSBackend)


def test_inventory_shape_is_consistent(backend_pair: BackendPair) -> None:
    snapshot = InventoryService(backend_pair.library).snapshot()

    slot_ids = [slot.slot_id for slot in snapshot.slots]
    drive_ids = [drive.drive_id for drive in snapshot.drives]
    assert slot_ids and drive_ids
    assert len(set(slot_ids)) == len(slot_ids)
    assert len(set(drive_ids)) == len(drive_ids)
    for slot in snapshot.slots:
        assert slot.occupied == (slot.barcode is not None)
    slot_barcodes = {slot.barcode.value for slot in snapshot.slots if slot.barcode}
    assert set(backend_pair.barcodes) <= slot_barcodes


def test_load_unload_round_trip_keeps_barcode_lookups_consistent(
    backend_pair: BackendPair,
) -> None:
    library = backend_pair.library
    barcode = backend_pair.barcodes[0]
    slot = _home_slot(library, barcode)
    drive_id = InventoryService(library).snapshot().drives[0].drive_id

    assert library.load(slot, drive_id).success
    assert library.find_drive_by_barcode(barcode) == drive_id
    assert library.find_slot_by_barcode(barcode) is None
    loaded = library.get_drive(drive_id).barcode
    assert loaded is not None and loaded.value == barcode

    assert library.unload(drive_id, slot).success
    assert library.find_drive_by_barcode(barcode) is None
    assert library.find_slot_by_barcode(barcode) == slot
    assert library.get_drive(drive_id).barcode is None


def test_double_load_into_occupied_drive_is_an_error(backend_pair: BackendPair) -> None:
    library = backend_pair.library
    first, second = backend_pair.barcodes[0], backend_pair.barcodes[1]
    first_slot, second_slot = _home_slot(library, first), _home_slot(library, second)
    drive_id = InventoryService(library).snapshot().drives[0].drive_id
    assert library.load(first_slot, drive_id).success

    try:
        result = library.load(second_slot, drive_id)
    except OpenBladeError:
        pass  # a typed domain error is an acceptable refusal
    else:
        assert not result.success, "loading into an occupied drive must fail"

    assert library.find_drive_by_barcode(first) == drive_id
    assert library.find_slot_by_barcode(second) == second_slot
    assert library.unload(drive_id, first_slot).success
