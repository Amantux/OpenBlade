from __future__ import annotations

import re
from pathlib import Path

import pytest

from openblade.hardware.discovery import parse_lsscsi
from openblade.hardware.sg import parse_sg_inq

pytestmark = pytest.mark.real_hardware


TAPE_ALERT_RE = re.compile(
    r"^\s*(?P<name>.+?)\s+\[(?P<code>0x[0-9a-fA-F]+)\]:\s+(?P<value>\d+)", re.MULTILINE
)
NON_MEDIUM_ERROR_RE = re.compile(r"non-medium error count:\s*(?P<value>\d+)", re.IGNORECASE)
MEDIUM_ERROR_RE = re.compile(r"medium error count:\s*(?P<value>\d+)", re.IGNORECASE)
LOAD_COUNT_RE = re.compile(r"load(?:s| count)?[^\d]*(?P<value>\d+)", re.IGNORECASE)


def _lsscsi_devices(runner):
    result = runner.run(["lsscsi", "-g"], timeout=30)
    assert result.returncode == 0, result.stderr
    return parse_lsscsi(result.stdout)


def _normalize_drive_path(device: str) -> str:
    if device.startswith("/dev/nst"):
        return f"/dev/st{device.removeprefix('/dev/nst')}"
    return device


def _drive_sg_device(requested_device: str, devices) -> str:
    requested = _normalize_drive_path(requested_device)
    requested_name = Path(requested).name
    for device in devices:
        candidates = {value for value in (device.block_device, device.sg_device) if value}
        if requested in candidates or requested_name in {Path(value).name for value in candidates}:
            return device.sg_device or requested
    return requested_device


def _sg_logs(runner, device: str, page: str) -> str:
    result = runner.run(["sg_logs", "-p", page, device], timeout=30)
    assert result.returncode == 0, result.stderr
    return result.stdout


def _parse_tapealert(output: str) -> dict[str, int]:
    return {
        match.group("name").strip().lower(): int(match.group("value"))
        for match in TAPE_ALERT_RE.finditer(output)
    }


def _find_metric(output: str, *patterns):
    for pattern in patterns:
        match = pattern.search(output)
        if match is not None:
            return int(match.group("value"))
    pytest.skip("Requested diagnostic field was not present in sg_logs output")


def _flag_value(flags: dict[str, int], *names: str) -> int:
    for name in names:
        if name.lower() in flags:
            return flags[name.lower()]
    pytest.skip(f"TapeAlert flag not present: {', '.join(names)}")


def test_sg_logs_tapealert_flags(real_hardware_guard, drive_devices, runner):
    """Requires: sg_logs available and at least one tape drive attached."""
    sg_device = _drive_sg_device(drive_devices[0], _lsscsi_devices(runner))
    flags = _parse_tapealert(_sg_logs(runner, sg_device, "0x2e"))
    assert _flag_value(flags, "read warning") == 0
    assert _flag_value(flags, "write warning") == 0


def test_drive_load_count_readable(real_hardware_guard, drive_devices, runner):
    """Requires: sg_logs page set to include load statistics."""
    sg_device = _drive_sg_device(drive_devices[0], _lsscsi_devices(runner))
    output = _sg_logs(runner, sg_device, "0x17")
    load_count = _find_metric(output, LOAD_COUNT_RE)
    assert load_count >= 0


def test_medium_error_rate(real_hardware_guard, drive_devices, runner):
    """Requires: sg_logs write error counters on the attached drive."""
    sg_device = _drive_sg_device(drive_devices[0], _lsscsi_devices(runner))
    output = _sg_logs(runner, sg_device, "0x03")
    medium_errors = _find_metric(output, MEDIUM_ERROR_RE)
    assert medium_errors < 1000


def test_hard_error_count(real_hardware_guard, drive_devices, runner):
    """Requires: sg_logs write error counters on the attached drive."""
    sg_device = _drive_sg_device(drive_devices[0], _lsscsi_devices(runner))
    output = _sg_logs(runner, sg_device, "0x03")
    hard_errors = _find_metric(output, NON_MEDIUM_ERROR_RE)
    assert hard_errors == 0


def test_drive_firmware_version(real_hardware_guard, drive_devices, runner):
    """Requires: sg_inq available and at least one tape drive attached."""
    sg_device = _drive_sg_device(drive_devices[0], _lsscsi_devices(runner))
    result = runner.run(["sg_inq", sg_device], timeout=30)
    assert result.returncode == 0, result.stderr
    inquiry = parse_sg_inq(result.stdout)
    print(f"Drive firmware revision for {sg_device}: {inquiry.revision}")
    assert inquiry.revision


def test_cleaning_not_required(real_hardware_guard, drive_devices, runner):
    """Requires: TapeAlert support on the attached drive."""
    sg_device = _drive_sg_device(drive_devices[0], _lsscsi_devices(runner))
    flags = _parse_tapealert(_sg_logs(runner, sg_device, "0x2e"))
    assert _flag_value(flags, "cleaning required", "cleaning requested") == 0


def test_drive_not_reporting_fault(real_hardware_guard, drive_devices, runner):
    """Requires: TapeAlert support on the attached drive."""
    sg_device = _drive_sg_device(drive_devices[0], _lsscsi_devices(runner))
    flags = _parse_tapealert(_sg_logs(runner, sg_device, "0x2e"))
    assert _flag_value(flags, "hardware a") == 0


# ---------------------------------------------------------------------------
# openblade.hardware.tapealert against the rig
#
# mhvtl's emulated LTO drives DO answer LOG SENSE page 0x2E -- `sg_logs -p 0x2e`
# decodes all 64 flags -- but every flag is always 0 and mhvtl has no way to set
# one. So the rig can prove the read path and the "no alerts" path, and nothing
# else: every set-flag assertion lives in tests/unit/test_tapealert.py against
# captured sg3_utils output. Do not "fix" that by asserting a set flag here; it
# would never fire.
# ---------------------------------------------------------------------------


def test_read_tape_alerts_degrades_gracefully(real_hardware_guard, drive_devices, runner):
    """Requires: a tape drive. Passes whether or not TapeAlert is implemented."""
    from openblade.config import load_config
    from openblade.hardware.safety import require_real_hardware
    from openblade.hardware.tapealert import read_tape_alerts

    guard = require_real_hardware(load_config())
    report = read_tape_alerts(drive_devices[0], runner, guard)
    if not report.supported:
        print(f"TapeAlert not supported on {report.device}: {report.reason}")
        assert report.active == ()
        return
    print(f"TapeAlert on {report.device}: {len(report.flags)} flags, {len(report.active)} set")
    assert len(report.flags) == 64
    for flag in report.active:
        print(f"  set: {flag.number} {flag.name} ({flag.severity})")


def test_read_tape_alerts_on_the_changer_is_unsupported(
    real_hardware_guard, changer_device, runner
):
    """Requires: a media changer. It answers 0x2e with a non-TapeAlert payload."""
    from openblade.config import load_config
    from openblade.hardware.safety import require_real_hardware
    from openblade.hardware.tapealert import read_tape_alerts

    guard = require_real_hardware(load_config())
    report = read_tape_alerts(changer_device, runner, guard)
    assert report.active == ()


def test_drive_health_command_exits_zero(real_hardware_guard, drive_devices):
    """Requires: a tape drive. The CLI must not fail on a drive with no alerts."""
    from typer.testing import CliRunner

    from openblade.cli.main import app

    result = CliRunner().invoke(app, ["hardware", "drive-health", "--device", drive_devices[0]])
    assert result.exit_code == 0, result.output
    print(result.output)
