from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from openblade.domain.errors import FileNotFoundError as OpenBladeFileNotFoundError

pytestmark = pytest.mark.real_hardware


def generate_test_file(path: Path, size_mb: int) -> str:
    """Generate random file, return sha256 hex digest."""
    data = os.urandom(size_mb * 1024 * 1024)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _prepare_volume_group(context, scratch_barcodes, name: str):
    group = context.catalog.get_volume_group(name) or context.catalog.create_volume_group(name)
    for barcode in scratch_barcodes:
        context.catalog.add_barcode_to_volume_group(group.id, barcode)
    return group


def _require_archive_services(context):
    required = ["archive_service", "restore_service", "catalog"]
    missing = [name for name in required if not hasattr(context, name)]
    if missing:
        pytest.skip(f"Real AppContext is missing required services: {', '.join(missing)}")


def test_single_file_archive_restore(
    real_hardware_guard,
    scratch_barcodes,
    real_app_context,
    tmp_path,
):
    """Requires: real archive/restore services and scratch media assigned to a volume group."""
    _require_archive_services(real_app_context)
    volume_group = "hw-archive-single"
    _prepare_volume_group(real_app_context, scratch_barcodes, volume_group)
    source_file = tmp_path / "payload-100mb.bin"
    expected_sha = generate_test_file(source_file, size_mb=100)
    archive_job = real_app_context.archive_service.enqueue(volume_group, source_file)
    assert archive_job.state == "completed"
    restore_dir = tmp_path / "restore-single"
    restore_dir.mkdir()
    restore_job = real_app_context.restore_service.enqueue(
        f"/{volume_group}/{source_file.name}",
        restore_dir,
    )
    assert restore_job.state == "completed"
    restored = restore_dir / source_file.name
    actual_sha = hashlib.sha256(restored.read_bytes()).hexdigest()
    assert actual_sha == expected_sha


def test_multiple_files_roundtrip(
    real_hardware_guard,
    scratch_barcodes,
    real_app_context,
    tmp_path,
):
    """Requires: real archive/restore services and scratch media assigned to a volume group."""
    _require_archive_services(real_app_context)
    volume_group = "hw-archive-multi"
    _prepare_volume_group(real_app_context, scratch_barcodes, volume_group)
    source_dir = tmp_path / "multi-source"
    source_dir.mkdir()
    expected = {}
    for index in range(5):
        file_path = source_dir / f"payload-{index}.bin"
        expected[file_path.name] = generate_test_file(file_path, size_mb=10)
    archive_job = real_app_context.archive_service.enqueue(volume_group, source_dir)
    assert archive_job.state == "completed"
    restore_dir = tmp_path / "multi-restore"
    restore_dir.mkdir()
    for name, checksum in expected.items():
        restore_job = real_app_context.restore_service.enqueue(f"/{volume_group}/{name}", restore_dir)
        assert restore_job.state == "completed"
        restored_checksum = hashlib.sha256((restore_dir / name).read_bytes()).hexdigest()
        assert restored_checksum == checksum


def test_catalog_records_archive(
    real_hardware_guard,
    scratch_barcodes,
    real_app_context,
    tmp_path,
):
    """Requires: real archive service and SQLite-backed catalog."""
    _require_archive_services(real_app_context)
    volume_group = "hw-catalog-job"
    _prepare_volume_group(real_app_context, scratch_barcodes, volume_group)
    source_dir = tmp_path / "catalog-source"
    source_dir.mkdir()
    generate_test_file(source_dir / "job.bin", size_mb=1)
    archive_job = real_app_context.archive_service.enqueue(volume_group, source_dir)
    jobs = real_app_context.catalog.list_jobs()
    assert any(job.id == archive_job.id and job.state == "completed" for job in jobs)


def test_restore_nonexistent_raises(real_hardware_guard, real_app_context, tmp_path):
    """Requires: real restore service with catalog-backed lookups."""
    _require_archive_services(real_app_context)
    with pytest.raises(OpenBladeFileNotFoundError):
        real_app_context.restore_service.enqueue("/does-not-exist/missing.bin", tmp_path / "restore")


def test_archive_marks_file_archived_after_success(
    real_hardware_guard,
    scratch_barcodes,
    real_app_context,
    tmp_path,
):
    """Requires: real archive service and scratch media assigned to a volume group."""
    _require_archive_services(real_app_context)
    volume_group = "hw-archive-state"
    _prepare_volume_group(real_app_context, scratch_barcodes, volume_group)
    source_dir = tmp_path / "state-source"
    source_dir.mkdir()
    file_path = source_dir / "archived.bin"
    generate_test_file(file_path, size_mb=1)
    archive_job = real_app_context.archive_service.enqueue(volume_group, source_dir)
    assert archive_job.state == "completed"
    record = real_app_context.catalog.get_file_record(f"/{volume_group}/{file_path.name}")
    assert record is not None
    assert record.instances[-1].state == "archived"
