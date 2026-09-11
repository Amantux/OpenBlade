from __future__ import annotations

import grp
import os
from pathlib import Path

import pytest

from openblade.hardware.discovery import find_tape_drives, parse_lsscsi, resolve_sg_device
from openblade.hardware.sg import parse_sg_inq

pytestmark = pytest.mark.real_hardware


def _lsscsi_devices(runner):
    result = runner.run(["lsscsi", "-g"], timeout=30)
    assert result.returncode == 0, result.stderr
    return result, parse_lsscsi(result.stdout)


def _normalize_drive_path(device: str) -> str:
    """Map a no-rewind node onto the rewinding node lsscsi reports in its output.

    This exists ONLY so a configured /dev/nstN can be matched against lsscsi's
    block-device column, which always shows /dev/stN. It must never be the path
    a SCSI command is issued against - see _resolve_scsi_path.
    """
    if device.startswith("/dev/nst"):
        return f"/dev/st{device.removeprefix('/dev/nst')}"
    return device


def _resolve_scsi_path(requested_device: str, devices) -> str:
    """Resolve a configured drive device to the node SCSI commands should use.

    Always prefer the SCSI generic node. Issuing sg_inq against the REWINDING
    /dev/stN node exits 50 ("close error: No medium found") whenever the drive
    is empty, because closing a rewinding node attempts a rewind - the inquiry
    itself succeeds and the command still reports failure. Drives are empty
    most of the time on a real library, so returning /dev/stN here made this a
    guaranteed false failure rather than an occasional one.
    """
    requested = _normalize_drive_path(requested_device)
    requested_name = Path(requested).name
    for device in devices:
        candidates = {value for value in (device.block_device, device.sg_device) if value}
        names = {Path(candidate).name for candidate in candidates}
        if requested in candidates or requested_name in names:
            return device.sg_device or device.block_device or requested_device
    return resolve_sg_device(requested_device)


def _user_in_tape_group() -> bool:
    group_ids = set(os.getgroups()) | {os.getgid()}
    for group_id in group_ids:
        try:
            if grp.getgrgid(group_id).gr_name == "tape":
                return True
        except KeyError:
            continue
    return False


def test_lsscsi_finds_changer(real_hardware_guard, changer_device, runner):
    """Requires: changer connected at OPENBLADE_CHANGER_DEVICE."""
    result, devices = _lsscsi_devices(runner)
    assert (
        any(device.device_type == "mediumx" for device in devices)
        or "media changer" in result.stdout.lower()
    )
    assert changer_device in result.stdout or any(
        device.sg_device == changer_device for device in devices
    )


def test_lsscsi_finds_drives(real_hardware_guard, runner):
    """Requires: at least one tape drive attached to the host."""
    result, devices = _lsscsi_devices(runner)
    assert find_tape_drives(devices) or "sequential-access" in result.stdout.lower()


def test_sg_device_readable(real_hardware_guard, changer_device, runner):
    """Requires: changer connected at OPENBLADE_CHANGER_DEVICE."""
    del runner
    assert os.access(changer_device, os.R_OK)


def test_drive_sg_devices_readable(real_hardware_guard, drive_devices, runner):
    """Requires: OPENBLADE_DRIVE_DEVICES to point at attached tape drives."""
    _, devices = _lsscsi_devices(runner)
    for drive_device in drive_devices:
        sg_or_drive = _resolve_scsi_path(drive_device, devices)
        assert os.access(sg_or_drive, os.R_OK), f"Drive device is not readable: {sg_or_drive}"


@pytest.mark.parametrize("drive_index", [0])
def test_sg_inquiry_drives(real_hardware_guard, drive_devices, runner, drive_index):
    """Requires: OPENBLADE_DRIVE_DEVICES to point at attached tape drives."""
    if drive_index >= len(drive_devices):
        pytest.skip("No drive at requested index")
    _, devices = _lsscsi_devices(runner)
    scsi_device = _resolve_scsi_path(drive_devices[drive_index], devices)
    result = runner.run(["sg_inq", scsi_device], timeout=30)
    assert result.returncode == 0, result.stderr
    inquiry = parse_sg_inq(result.stdout)
    assert inquiry.vendor
    assert inquiry.product


def test_sg_inquiry_changer(real_hardware_guard, changer_device, runner):
    """Requires: changer connected at OPENBLADE_CHANGER_DEVICE."""
    result = runner.run(["sg_inq", changer_device], timeout=30)
    assert result.returncode == 0, result.stderr
    inquiry = parse_sg_inq(result.stdout)
    assert inquiry.vendor or inquiry.product


def test_by_id_symlinks(real_hardware_guard, changer_device, runner):
    """Requires: udev to create /dev/tape/by-id symlinks for tape devices."""
    del changer_device, runner
    by_id = Path("/dev/tape/by-id")
    if not by_id.exists():
        pytest.skip("/dev/tape/by-id is not present on this host")
    symlinks = [path for path in by_id.iterdir() if path.is_symlink()]
    assert symlinks


def test_udev_info_changer(real_hardware_guard, changer_device, runner):
    """Requires: udevadm to be installed on the host."""
    result = runner.run(["udevadm", "info", "--query=all", f"--name={changer_device}"], timeout=30)
    assert result.returncode == 0, result.stderr


def test_drive_device_permissions(real_hardware_guard, drive_devices, runner):
    """Requires: tape devices to be readable by the current operator or tape group."""
    del runner
    if all(os.access(drive, os.R_OK) for drive in drive_devices):
        return
    assert _user_in_tape_group(), (
        "Current user cannot read drive devices and is not in the tape group"
    )
