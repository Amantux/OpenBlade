from __future__ import annotations

from pathlib import Path

import pytest

from openblade.config import BackendMode, OpenBladeConfig
from openblade.hardware.ltfs import LTFSDevice
from openblade.hardware.runner import SafeRunner
from openblade.hardware.validation import (
    _device_in_list,
    connect_quantum_i3,
    validate_ltfs_capabilities,
)


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
    # A dry run probes nothing, so the report must not claim a verified correlation.
    assert report.drive_correlation_serials_verified is False
    assert report.drive_correlation_source == "dry_run"
    assert report.drive_correlation[0] == {"driveId": 0, "device": "/dev/st0", "serial": ""}


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


class TestDeviceListOk:
    """`device_list_ok` must compare tape nodes against LTFS's sg nodes.

    LTFS only ever enumerates /dev/sgN, while callers name a tape node - the
    CLI help for `hardware validate-ltfs` suggests "/dev/st0" and Phase 4.1 of
    docs/runbooks/real-i3-bringup-plan.md passes /dev/nst0. A raw string
    equality therefore reported device_list_ok=false on a perfectly healthy
    library, at the very first gate of the bring-up.
    """

    @staticmethod
    def _devices() -> list[LTFSDevice]:
        return [
            LTFSDevice(index=0, device="/dev/sg1", description="IBM ULT3580-TD8"),
            LTFSDevice(index=1, device="/dev/sg2", description="IBM ULT3580-TD8"),
        ]

    def test_matches_sg_device_directly(self) -> None:
        assert _device_in_list("/dev/sg2", self._devices()) is True

    def test_matches_tape_node_via_sysfs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # st2 -> sg1 is deliberately mismatched in number: the mapping must be
        # resolved, never derived from the device name.
        mapping = {"/dev/st2": "/dev/sg1"}
        monkeypatch.setattr(
            "openblade.hardware.validation.resolve_sg_device",
            lambda device, **_: mapping.get(device, device),
        )
        assert _device_in_list("/dev/st2", self._devices()) is True

    def test_reports_false_for_a_drive_ltfs_did_not_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mapping = {"/dev/st9": "/dev/sg9"}
        monkeypatch.setattr(
            "openblade.hardware.validation.resolve_sg_device",
            lambda device, **_: mapping.get(device, device),
        )
        assert _device_in_list("/dev/st9", self._devices()) is False

    def test_empty_device_list_is_false(self) -> None:
        assert _device_in_list("/dev/sg1", []) is False


def test_validate_ltfs_capabilities_requires_mount_path_for_mount_checks(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="mount_point is required"):
        validate_ltfs_capabilities(
            _config(tmp_path),
            device="/dev/st0",
            barcode="PHO001L8",
            exercise_mounts=True,
            runner=SafeRunner(dry_run=True),
        )
