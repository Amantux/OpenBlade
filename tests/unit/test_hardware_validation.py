from __future__ import annotations

from pathlib import Path

import pytest

from openblade.config import BackendMode, OpenBladeConfig
from openblade.hardware.runner import SafeRunner
from openblade.hardware.validation import connect_quantum_i3, validate_ltfs_capabilities


def _config(tmp_path: Path) -> OpenBladeConfig:
    return OpenBladeConfig(
        backend=BackendMode.REAL,
        real_hardware_enabled=True,
        hardware_dry_run=True,
        ltfs_mount_root=str(tmp_path / "ltfs"),
    )


def test_connect_quantum_i3_returns_inventory_report_in_dry_run(tmp_path: Path) -> None:
    report = connect_quantum_i3(_config(tmp_path), runner=SafeRunner(dry_run=True))

    assert report.library_id == "sg0"
    assert report.changer_device == "/dev/sg0"
    assert report.drive_count == 1
    assert report.slot_count == 2
    assert report.discovered_drives == ["/dev/st0", "/dev/st1"]
    assert report.sg_inquiry[0]["device"] == "/dev/sg0"


def test_validate_ltfs_capabilities_reports_device_list_and_plan(tmp_path: Path) -> None:
    report = validate_ltfs_capabilities(
        _config(tmp_path),
        device="/dev/st0",
        barcode="PHO001L8",
        runner=SafeRunner(dry_run=True),
    )

    assert report.device_list_ok is True
    assert report.format_plan["target"] == "format PHO001L8 on /dev/st0"
    assert report.readonly_mount_ok is None
    assert report.readwrite_mount_ok is None


def test_validate_ltfs_capabilities_can_exercise_mounts_in_dry_run(tmp_path: Path) -> None:
    report = validate_ltfs_capabilities(
        _config(tmp_path),
        device="/dev/st0",
        barcode="PHO001L8",
        mount_point=tmp_path / "mnt",
        exercise_mounts=True,
        runner=SafeRunner(dry_run=True),
    )

    assert report.readonly_mount_ok is True
    assert report.readwrite_mount_ok is True


def test_validate_ltfs_capabilities_requires_mount_path_for_mount_checks(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="mount_point is required"):
        validate_ltfs_capabilities(
            _config(tmp_path),
            device="/dev/st0",
            barcode="PHO001L8",
            exercise_mounts=True,
            runner=SafeRunner(dry_run=True),
        )
