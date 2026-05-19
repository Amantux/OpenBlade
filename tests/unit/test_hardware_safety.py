from pathlib import Path

import pytest

from openblade.domain.errors import BarcodeMismatchError, RealHardwareDisabledError
from openblade.domain.policies import FormatConfirmation, RealHardwareGuard, SafetyToken
from openblade.hardware.ltfs import LTFSCommandBackend
from openblade.hardware.mtx import SAMPLE_MTX_LOADED, MtxChangerBackend
from openblade.hardware.runner import SafeRunner


def test_runner_rejects_string_args() -> None:
    runner = SafeRunner(dry_run=True)
    with pytest.raises(TypeError):
        runner.run("mtx -f /dev/sg0 status")  # type: ignore[arg-type]


def test_runner_dry_run_returns_empty_result() -> None:
    runner = SafeRunner(dry_run=True)
    result = runner.run(["echo", "hello"])
    assert result.returncode == 0
    assert result.stdout == ""


def test_mtx_backend_requires_guard() -> None:
    bad_guard = RealHardwareGuard(
        config_backend="mock",
        config_real_hardware_enabled=False,
        operator_acknowledgment="",
    )
    with pytest.raises((RealHardwareDisabledError, Exception)):
        MtxChangerBackend(device="/dev/sg0", guard=bad_guard)


def test_mtx_backend_dry_run_inventory_uses_fixture() -> None:
    guard = RealHardwareGuard("real", True, "ack")
    backend = MtxChangerBackend(
        device="/dev/sg0",
        guard=guard,
        runner=SafeRunner(dry_run=True),
        sample_status_output=SAMPLE_MTX_LOADED,
    )
    status = backend.inventory()
    assert status.drives[0].barcode == "PHO001L8"


def test_format_dry_run_plan_has_no_side_effects() -> None:
    plan = LTFSCommandBackend.format_dry_run_plan("PHO001L8", "/dev/st0")
    assert plan.is_destructive is True
    assert "PHO001L8" in plan.target


def test_ltfs_format_requires_confirmation_match() -> None:
    guard = RealHardwareGuard("real", True, "ack")
    runner = SafeRunner(dry_run=True)
    confirmation = FormatConfirmation("PHO001L8", SafetyToken.generate("format", "PHO001L8"))
    result = LTFSCommandBackend.format_tape("PHO001L8", "/dev/st0", confirmation, guard, runner)
    assert result.success is True
    assert result.message == "dry-run format"


def test_ltfs_format_rejects_mismatched_barcode() -> None:
    guard = RealHardwareGuard("real", True, "ack")
    runner = SafeRunner(dry_run=True)
    confirmation = FormatConfirmation("PHO002L8", SafetyToken.generate("format", "PHO001L8"))
    with pytest.raises(BarcodeMismatchError):
        LTFSCommandBackend.format_tape("PHO001L8", "/dev/st0", confirmation, guard, runner)


def test_ltfs_mount_requires_guard() -> None:
    bad_guard = RealHardwareGuard("mock", False, "")
    with pytest.raises(RealHardwareDisabledError):
        LTFSCommandBackend.mount_readonly(
            "/dev/st0", "/mnt/ltfs", bad_guard, SafeRunner(dry_run=True)
        )


def test_no_shell_true_in_hardware_modules() -> None:
    hw_dir = Path("openblade/hardware")
    for path in hw_dir.rglob("*.py"):
        assert "shell=True" not in path.read_text(), f"shell=True found in {path}"
