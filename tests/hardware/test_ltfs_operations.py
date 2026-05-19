from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from openblade.hardware.mtx import parse_mtx_status

pytestmark = pytest.mark.real_hardware


LTFS_TIMEOUT = 600


def _mtx_status(changer_device: str, runner):
    result = runner.run(["mtx", "-f", changer_device, "status"], timeout=180)
    assert result.returncode == 0, result.stderr
    return parse_mtx_status(result.stdout)


def _find_barcode_slot(changer_device: str, runner, barcode: str) -> int:
    status = _mtx_status(changer_device, runner)
    for slot in status.slots:
        if slot.barcode and slot.barcode.upper() == barcode.upper():
            return slot.slot_id
    pytest.skip(f"Scratch barcode {barcode} is not currently present in the library")


def _load_barcode(changer_device: str, runner, barcode: str) -> int:
    slot_id = _find_barcode_slot(changer_device, runner, barcode)
    result = runner.run(["mtx", "-f", changer_device, "load", str(slot_id), "0"], timeout=180)
    assert result.returncode == 0, result.stderr
    return slot_id


def _unload_barcode(changer_device: str, runner, slot_id: int):
    result = runner.run(["mtx", "-f", changer_device, "unload", str(slot_id), "0"], timeout=180)
    assert result.returncode == 0, result.stderr


def _format_tape(runner, drive_device: str, barcode: str):
    result = runner.run(
        ["mkltfs", f"--device={drive_device}", f"--tape-serial={barcode}", "--force"],
        timeout=LTFS_TIMEOUT,
    )
    assert result.returncode == 0, result.stderr
    return result


def _mount_ltfs(runner, drive_device: str, mount_dir: Path, read_only: bool = False):
    option = f"devname={drive_device}" if not read_only else f"devname={drive_device},ro"
    result = runner.run(["ltfs", str(mount_dir), "-o", option], timeout=LTFS_TIMEOUT)
    assert result.returncode == 0, result.stderr
    return result


def _unmount_ltfs(runner, mount_dir: Path):
    result = runner.run(["umount", str(mount_dir)], timeout=120)
    if result.returncode == 0:
        return result
    fallback = runner.run(["fusermount", "-u", str(mount_dir)], timeout=120)
    assert fallback.returncode == 0, f"{result.stderr}\n{fallback.stderr}"
    return fallback


def _write_payload(path: Path, size_bytes: int = 1024 * 1024) -> str:
    data = os.urandom(size_bytes)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def test_mkltfs_formats_tape(
    real_hardware_guard,
    changer_device,
    drive_devices,
    runner,
    scratch_barcode,
):
    """Requires: a scratch tape that is safe to format and load into drive 0."""
    slot_id = _load_barcode(changer_device, runner, scratch_barcode)
    try:
        result = _format_tape(runner, drive_devices[0], scratch_barcode)
        assert result.returncode == 0
    finally:
        _unload_barcode(changer_device, runner, slot_id)


def test_ltfs_mount_formatted(
    real_hardware_guard,
    changer_device,
    drive_devices,
    runner,
    scratch_barcode,
    tmp_mount_dir,
):
    """Requires: a scratch tape that is safe to format and mount."""
    slot_id = _load_barcode(changer_device, runner, scratch_barcode)
    try:
        _format_tape(runner, drive_devices[0], scratch_barcode)
        result = _mount_ltfs(runner, drive_devices[0], tmp_mount_dir)
        assert result.returncode == 0
        assert tmp_mount_dir.exists()
    finally:
        try:
            _unmount_ltfs(runner, tmp_mount_dir)
        except Exception:
            pass
        _unload_barcode(changer_device, runner, slot_id)


def test_ltfs_write_small_file(
    real_hardware_guard,
    changer_device,
    drive_devices,
    runner,
    scratch_barcode,
    tmp_mount_dir,
    tmp_path,
):
    """Requires: a scratch tape that is safe to format and mount."""
    slot_id = _load_barcode(changer_device, runner, scratch_barcode)
    payload = tmp_path / "small.bin"
    try:
        _format_tape(runner, drive_devices[0], scratch_barcode)
        _mount_ltfs(runner, drive_devices[0], tmp_mount_dir)
        _write_payload(payload)
        target = tmp_mount_dir / payload.name
        target.write_bytes(payload.read_bytes())
        assert target.exists()
        assert target.stat().st_size == payload.stat().st_size
    finally:
        try:
            _unmount_ltfs(runner, tmp_mount_dir)
        except Exception:
            pass
        _unload_barcode(changer_device, runner, slot_id)


def test_ltfs_read_small_file(
    real_hardware_guard,
    changer_device,
    drive_devices,
    runner,
    scratch_barcode,
    tmp_mount_dir,
    tmp_path,
):
    """Requires: a scratch tape that is safe to format and mount."""
    slot_id = _load_barcode(changer_device, runner, scratch_barcode)
    source = tmp_path / "source.bin"
    restored = tmp_path / "restored.bin"
    try:
        expected_sha = _write_payload(source)
        _format_tape(runner, drive_devices[0], scratch_barcode)
        _mount_ltfs(runner, drive_devices[0], tmp_mount_dir)
        target = tmp_mount_dir / source.name
        target.write_bytes(source.read_bytes())
        restored.write_bytes(target.read_bytes())
        actual_sha = hashlib.sha256(restored.read_bytes()).hexdigest()
        assert actual_sha == expected_sha
    finally:
        try:
            _unmount_ltfs(runner, tmp_mount_dir)
        except Exception:
            pass
        _unload_barcode(changer_device, runner, slot_id)


def test_ltfs_unmount_clean(
    real_hardware_guard,
    changer_device,
    drive_devices,
    runner,
    scratch_barcode,
    tmp_mount_dir,
):
    """Requires: a scratch tape that is safe to format and mount."""
    slot_id = _load_barcode(changer_device, runner, scratch_barcode)
    try:
        _format_tape(runner, drive_devices[0], scratch_barcode)
        _mount_ltfs(runner, drive_devices[0], tmp_mount_dir)
        result = _unmount_ltfs(runner, tmp_mount_dir)
        assert result.returncode == 0
    finally:
        _unload_barcode(changer_device, runner, slot_id)


def test_ltfs_remount_persistence(
    real_hardware_guard,
    changer_device,
    drive_devices,
    runner,
    scratch_barcode,
    tmp_mount_dir,
    tmp_path,
):
    """Requires: a scratch tape that is safe to format and remount."""
    slot_id = _load_barcode(changer_device, runner, scratch_barcode)
    payload = tmp_path / "persist.bin"
    try:
        _format_tape(runner, drive_devices[0], scratch_barcode)
        expected_sha = _write_payload(payload)
        _mount_ltfs(runner, drive_devices[0], tmp_mount_dir)
        target = tmp_mount_dir / payload.name
        target.write_bytes(payload.read_bytes())
        _unmount_ltfs(runner, tmp_mount_dir)
        _mount_ltfs(runner, drive_devices[0], tmp_mount_dir, read_only=True)
        assert target.exists()
        actual_sha = hashlib.sha256(target.read_bytes()).hexdigest()
        assert actual_sha == expected_sha
    finally:
        try:
            _unmount_ltfs(runner, tmp_mount_dir)
        except Exception:
            pass
        _unload_barcode(changer_device, runner, slot_id)


def test_ltfs_capacity_reporting(
    real_hardware_guard,
    changer_device,
    drive_devices,
    runner,
    scratch_barcode,
    tmp_mount_dir,
):
    """Requires: a scratch tape that is safe to format and mount."""
    slot_id = _load_barcode(changer_device, runner, scratch_barcode)
    try:
        _format_tape(runner, drive_devices[0], scratch_barcode)
        _mount_ltfs(runner, drive_devices[0], tmp_mount_dir)
        result = runner.run(["df", "-h", str(tmp_mount_dir)], timeout=30)
        assert result.returncode == 0, result.stderr
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        assert len(lines) >= 2
        parts = lines[-1].split()
        assert parts[3] not in {"0", "0B"}
    finally:
        try:
            _unmount_ltfs(runner, tmp_mount_dir)
        except Exception:
            pass
        _unload_barcode(changer_device, runner, slot_id)


def test_ltfs_dirty_state_detected(
    real_hardware_guard,
    changer_device,
    drive_devices,
    runner,
    scratch_barcode,
    tmp_mount_dir,
    real_app_context,
):
    """Requires: OPENBLADE_TEST_DIRTY_UNMOUNT=1 and dirty-state handling in the app."""
    del changer_device, drive_devices, runner, scratch_barcode, tmp_mount_dir
    if os.environ.get("OPENBLADE_TEST_DIRTY_UNMOUNT") != "1":
        pytest.skip("OPENBLADE_TEST_DIRTY_UNMOUNT is not enabled")
    if not hasattr(real_app_context, "library"):
        pytest.skip("Real AppContext does not expose dirty-state handling")
    pytest.skip("Dirty LTFS fault injection needs process-control support beyond SafeRunner")
