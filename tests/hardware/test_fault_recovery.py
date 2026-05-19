from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.real_hardware


@pytest.fixture
def fault_tests_enabled():
    if os.environ.get("OPENBLADE_FAULT_TESTS") != "enabled":
        pytest.skip("OPENBLADE_FAULT_TESTS must be set to 'enabled'")


def _require_real_app_context(context):
    if not hasattr(context, "archive_service") or not hasattr(context, "catalog"):
        pytest.skip("Real AppContext support is not implemented for fault recovery yet")


def test_dirty_ltfs_detection_after_kill(
    real_hardware_guard,
    fault_tests_enabled,
    changer_device,
    drive_devices,
    runner,
    scratch_barcode,
    tmp_mount_dir,
    real_app_context,
):
    """Requires: OPENBLADE_FAULT_TESTS=enabled and manual approval for disruptive LTFS fault injection."""
    del fault_tests_enabled, changer_device, drive_devices, runner, scratch_barcode, tmp_mount_dir
    _require_real_app_context(real_app_context)
    pytest.skip("Dirty LTFS process-kill testing needs process control beyond SafeRunner")


def test_catalog_rollback_on_failed_archive(
    real_hardware_guard,
    fault_tests_enabled,
    scratch_barcodes,
    real_app_context,
    tmp_path,
    monkeypatch,
):
    """Requires: OPENBLADE_FAULT_TESTS=enabled and a real archive service."""
    del fault_tests_enabled
    _require_real_app_context(real_app_context)
    group = real_app_context.catalog.get_volume_group(
        "hw-fault"
    ) or real_app_context.catalog.create_volume_group("hw-fault")
    for barcode in scratch_barcodes:
        real_app_context.catalog.add_barcode_to_volume_group(group.id, barcode)
    source_dir = tmp_path / "fault-source"
    source_dir.mkdir()
    source_file = source_dir / "rollback.bin"
    source_file.write_bytes(b"rollback")
    original_write = getattr(real_app_context.ltfs, "write_file", None)
    if original_write is None:
        pytest.skip("LTFS backend does not expose write_file")

    def _boom(*args, **kwargs):
        raise RuntimeError("intentional archive failure")

    monkeypatch.setattr(real_app_context.ltfs, "write_file", _boom)
    with pytest.raises(RuntimeError):
        real_app_context.archive_service.enqueue("hw-fault", source_dir)
    record = real_app_context.catalog.get_file_record(f"/hw-fault/{source_file.name}")
    assert record is None or all(instance.state != "archived" for instance in record.instances)


def test_changer_state_reconcile(
    real_hardware_guard,
    fault_tests_enabled,
    changer_device,
    runner,
):
    """Requires: OPENBLADE_FAULT_TESTS=enabled and a changer that survives invalid commands."""
    del fault_tests_enabled
    failed = runner.run(["mtx", "-f", changer_device, "load", "999", "0"], timeout=60)
    assert failed.returncode != 0
    recovered = runner.run(["mtx", "-f", changer_device, "status"], timeout=60)
    assert recovered.returncode == 0, recovered.stderr


def test_recovery_from_interrupted_load(
    real_hardware_guard,
    fault_tests_enabled,
    changer_device,
    runner,
):
    """Requires: OPENBLADE_FAULT_TESTS=enabled and a changer that survives invalid drive targets."""
    del fault_tests_enabled
    failed = runner.run(["mtx", "-f", changer_device, "load", "1", "999"], timeout=60)
    assert failed.returncode != 0
    recovered = runner.run(["mtx", "-f", changer_device, "status"], timeout=60)
    assert recovered.returncode == 0, recovered.stderr
