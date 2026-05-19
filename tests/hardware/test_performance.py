from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest

from openblade.hardware.mtx import parse_mtx_status

pytestmark = pytest.mark.real_hardware


PERF_RESULTS_FILE = Path(
    os.environ.get(
        "OPENBLADE_PERF_RESULTS_FILE",
        str(Path.cwd() / ".openblade_perf_results.json"),
    )
)


def save_perf_result(test_name: str, metric: str, value: float, unit: str):
    results = json.loads(PERF_RESULTS_FILE.read_text()) if PERF_RESULTS_FILE.exists() else []
    results.append({"test": test_name, "metric": metric, "value": value, "unit": unit, "ts": time.time()})
    PERF_RESULTS_FILE.write_text(json.dumps(results, indent=2))


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
        timeout=900,
    )
    assert result.returncode == 0, result.stderr


def _mount_ltfs(runner, drive_device: str, mount_dir: Path):
    result = runner.run(["ltfs", str(mount_dir), "-o", f"devname={drive_device}"], timeout=900)
    assert result.returncode == 0, result.stderr


def _unmount_ltfs(runner, mount_dir: Path):
    result = runner.run(["umount", str(mount_dir)], timeout=120)
    if result.returncode == 0:
        return
    fallback = runner.run(["fusermount", "-u", str(mount_dir)], timeout=120)
    assert fallback.returncode == 0, f"{result.stderr}\n{fallback.stderr}"


def _write_fixed_size_file(path: Path, size_bytes: int):
    chunk = b"0" * (8 * 1024 * 1024)
    remaining = size_bytes
    with path.open("wb") as handle:
        while remaining > 0:
            piece = chunk[: min(len(chunk), remaining)]
            handle.write(piece)
            remaining -= len(piece)


def test_single_drive_write_throughput(
    real_hardware_guard,
    changer_device,
    drive_devices,
    runner,
    scratch_barcode,
    tmp_mount_dir,
    tmp_path,
):
    """Requires: a scratch tape safe to format, mount, and benchmark."""
    slot_id = _load_barcode(changer_device, runner, scratch_barcode)
    payload = tmp_path / "write-throughput.bin"
    try:
        _write_fixed_size_file(payload, 1024 * 1024 * 1024)
        _format_tape(runner, drive_devices[0], scratch_barcode)
        _mount_ltfs(runner, drive_devices[0], tmp_mount_dir)
        start = time.perf_counter()
        shutil.copyfile(payload, tmp_mount_dir / payload.name)
        elapsed = time.perf_counter() - start
        throughput = payload.stat().st_size / (1024 * 1024) / elapsed
        print(f"single-drive write throughput: {throughput:.2f} MB/s")
        save_perf_result("test_single_drive_write_throughput", "write_throughput", throughput, "MB/s")
        assert throughput > 50.0
    finally:
        try:
            _unmount_ltfs(runner, tmp_mount_dir)
        except Exception:
            pass
        _unload_barcode(changer_device, runner, slot_id)


def test_single_drive_read_throughput(
    real_hardware_guard,
    changer_device,
    drive_devices,
    runner,
    scratch_barcode,
    tmp_mount_dir,
    tmp_path,
):
    """Requires: a scratch tape safe to format, mount, and benchmark."""
    slot_id = _load_barcode(changer_device, runner, scratch_barcode)
    payload = tmp_path / "read-throughput.bin"
    restored = tmp_path / "readback.bin"
    try:
        _write_fixed_size_file(payload, 1024 * 1024 * 1024)
        _format_tape(runner, drive_devices[0], scratch_barcode)
        _mount_ltfs(runner, drive_devices[0], tmp_mount_dir)
        target = tmp_mount_dir / payload.name
        shutil.copyfile(payload, target)
        start = time.perf_counter()
        shutil.copyfile(target, restored)
        elapsed = time.perf_counter() - start
        throughput = restored.stat().st_size / (1024 * 1024) / elapsed
        print(f"single-drive read throughput: {throughput:.2f} MB/s")
        save_perf_result("test_single_drive_read_throughput", "read_throughput", throughput, "MB/s")
        assert throughput > 0.0
    finally:
        try:
            _unmount_ltfs(runner, tmp_mount_dir)
        except Exception:
            pass
        _unload_barcode(changer_device, runner, slot_id)


def test_changer_move_latency(
    real_hardware_guard,
    changer_device,
    runner,
    scratch_barcode,
):
    """Requires: a scratch tape currently present in a library slot."""
    slot_id = _find_barcode_slot(changer_device, runner, scratch_barcode)
    start = time.perf_counter()
    load = runner.run(["mtx", "-f", changer_device, "load", str(slot_id), "0"], timeout=180)
    assert load.returncode == 0, load.stderr
    unload = runner.run(["mtx", "-f", changer_device, "unload", str(slot_id), "0"], timeout=180)
    assert unload.returncode == 0, unload.stderr
    elapsed = time.perf_counter() - start
    print(f"changer load+unload latency: {elapsed:.2f}s")
    save_perf_result("test_changer_move_latency", "move_latency", elapsed, "s")
    assert elapsed < 90.0


def test_throughput_log(real_hardware_guard, runner):
    """Requires: a writable performance results file path."""
    del runner
    save_perf_result("test_throughput_log", "sanity", 1.0, "ok")
    assert PERF_RESULTS_FILE.exists()
