from __future__ import annotations

import threading
import time

import pytest

from openblade.hardware.mtx import parse_mtx_status

pytestmark = pytest.mark.real_hardware


MTX_TIMEOUT = 180


def _status(changer_device: str, runner):
    result = runner.run(["mtx", "-f", changer_device, "status"], timeout=MTX_TIMEOUT)
    assert result.returncode == 0, result.stderr
    return parse_mtx_status(result.stdout)


def _find_scratch_slot(status, scratch_barcodes):
    wanted = {barcode.upper() for barcode in scratch_barcodes}
    for slot in status.slots:
        if slot.barcode and slot.barcode.upper() in wanted:
            return slot
    return None


def _load_scratch(changer_device: str, runner, scratch_barcodes):
    before = _status(changer_device, runner)
    drive = next(
        (drive for drive in before.drives if drive.drive_id == 0 and not drive.loaded), None
    )
    if drive is None:
        pytest.skip("Drive 0 is not empty")
    slot = _find_scratch_slot(before, scratch_barcodes)
    if slot is None:
        pytest.skip("No scratch tape is currently in a library slot")
    load_result = runner.run(
        ["mtx", "-f", changer_device, "load", str(slot.slot_id), "0"],
        timeout=MTX_TIMEOUT,
    )
    assert load_result.returncode == 0, load_result.stderr
    return slot.slot_id


def _unload_to_slot(changer_device: str, runner, slot_id: int):
    unload_result = runner.run(
        ["mtx", "-f", changer_device, "unload", str(slot_id), "0"],
        timeout=MTX_TIMEOUT,
    )
    assert unload_result.returncode == 0, unload_result.stderr


def test_mtx_status_parses(real_hardware_guard, changer_device, runner):
    """Requires: changer connected at OPENBLADE_CHANGER_DEVICE."""
    status = _status(changer_device, runner)
    assert status.drive_count >= len(status.drives)
    assert status.slot_count >= len(status.slots)


def test_mtx_inventory_runs(real_hardware_guard, changer_device, runner):
    """Requires: barcode-capable library changer and mtx installed."""
    result = runner.run(["mtx", "-f", changer_device, "inventory"], timeout=MTX_TIMEOUT)
    assert result.returncode == 0, result.stderr


def test_element_addresses_consistent(real_hardware_guard, changer_device, runner):
    """Requires: changer connected at OPENBLADE_CHANGER_DEVICE."""
    status = _status(changer_device, runner)
    if not status.slots:
        pytest.skip("No storage elements were reported")
    slot_ids = sorted(slot.slot_id for slot in status.slots)
    assert slot_ids == list(range(slot_ids[0], slot_ids[-1] + 1))


def test_drive_elements_reported(real_hardware_guard, changer_device, runner):
    """Requires: changer connected at OPENBLADE_CHANGER_DEVICE."""
    status = _status(changer_device, runner)
    assert status.drives, "No Data Transfer Element lines were reported"


def test_storage_elements_reported(real_hardware_guard, changer_device, runner):
    """Requires: changer connected at OPENBLADE_CHANGER_DEVICE."""
    status = _status(changer_device, runner)
    assert status.slots, "No Storage Element lines were reported"


def test_load_unload_roundtrip(real_hardware_guard, changer_device, runner, scratch_barcodes):
    """Requires: a scratch barcode currently present in a library slot."""
    slot_id = _load_scratch(changer_device, runner, scratch_barcodes)
    try:
        loaded_status = _status(changer_device, runner)
        assert any(drive.drive_id == 0 and drive.loaded for drive in loaded_status.drives)
    finally:
        _unload_to_slot(changer_device, runner, slot_id)
    final_status = _status(changer_device, runner)
    assert any(slot.slot_id == slot_id and slot.occupied for slot in final_status.slots)


def test_concurrent_move_blocked(
    real_hardware_guard,
    changer_device,
    runner,
    scratch_barcodes,
    real_library_backend,
):
    """Requires: a library backend that serializes robotic moves."""
    del changer_device, runner, scratch_barcodes
    if type(real_library_backend).__name__ == "MtxChangerBackend":
        pytest.skip("No higher-level locking backend is available in this repository yet")
    if not hasattr(real_library_backend, "load"):
        pytest.skip("RealLibraryBackend does not expose a load() method")
    results = []
    errors = []

    def _invoke(slot: int) -> None:
        try:
            start = time.monotonic()
            real_library_backend.load(slot, 0)
            results.append(time.monotonic() - start)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    first = threading.Thread(target=_invoke, args=(1,))
    second = threading.Thread(target=_invoke, args=(1,))
    first.start()
    time.sleep(0.05)
    second.start()
    first.join(timeout=60)
    second.join(timeout=60)
    assert errors or any(duration > 0.05 for duration in results)


def test_status_after_load(real_hardware_guard, changer_device, runner, scratch_barcodes):
    """Requires: a scratch barcode currently present in a library slot."""
    slot_id = _load_scratch(changer_device, runner, scratch_barcodes)
    try:
        status = _status(changer_device, runner)
        drive_zero = next((drive for drive in status.drives if drive.drive_id == 0), None)
        assert drive_zero is not None and drive_zero.loaded
    finally:
        _unload_to_slot(changer_device, runner, slot_id)


def test_status_after_unload(real_hardware_guard, changer_device, runner, scratch_barcodes):
    """Requires: a scratch barcode currently present in a library slot."""
    slot_id = _load_scratch(changer_device, runner, scratch_barcodes)
    _unload_to_slot(changer_device, runner, slot_id)
    status = _status(changer_device, runner)
    slot = next((slot for slot in status.slots if slot.slot_id == slot_id), None)
    drive_zero = next((drive for drive in status.drives if drive.drive_id == 0), None)
    assert slot is not None and slot.occupied
    assert drive_zero is not None and not drive_zero.loaded
