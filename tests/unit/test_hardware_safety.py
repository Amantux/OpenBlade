import subprocess
import sys
import time
from pathlib import Path

import pytest

from openblade.domain.errors import BarcodeMismatchError, RealHardwareDisabledError
from openblade.domain.policies import FormatConfirmation, RealHardwareGuard, SafetyToken
from openblade.hardware.ltfs import (
    SAMPLE_LTFS_DEVICE_LIST,
    SAMPLE_LTFS_DEVICE_LIST_REAL,
    LTFSCommandBackend,
    _ltfs_processes_holding,
    wait_for_ltfs_release,
)
from openblade.hardware.mtx import SAMPLE_MTX_LOADED, MtxChangerBackend
from openblade.hardware.runner import CommandError, CommandResult, SafeRunner


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


class RecordingRunner(SafeRunner):
    """SafeRunner that records argv and replays canned results.

    The hardware suite can only assert these command lines against a live
    library. This pins them in unit tests, so reverting the device-resolution
    or output-parsing fixes fails here rather than months later at the i3.
    """

    def __init__(self, results: list[CommandResult] | None = None) -> None:
        super().__init__(dry_run=False)
        self.calls: list[list[str]] = []
        self._results = list(results or [])

    def run(self, args, timeout=None, redact_args=None):  # type: ignore[override]
        self.calls.append(list(args))
        if self._results:
            return self._results.pop(0)
        return CommandResult(
            args=list(args), returncode=0, stdout="", stderr="", elapsed_seconds=0.0
        )


class TestLtfsUsesTheScsiGenericNode:
    """LTFS addresses drives as /dev/sgN; a tape node silently reads garbage.

    Given /dev/st0 the sg backend reports "No index found in the medium", which
    is indistinguishable from blank media - so this must be pinned, not left to
    a hardware-only test.
    """

    @staticmethod
    def _sg_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
        # st2 -> sg1 is deliberately mismatched in number, as real hosts are.
        mapping = {"/dev/st2": "/dev/sg1", "/dev/nst2": "/dev/sg1"}
        monkeypatch.setattr(
            "openblade.hardware.ltfs.resolve_sg_device",
            lambda device: mapping.get(device, device),
        )

    def test_format_tape_passes_the_sg_node(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._sg_mapping(monkeypatch)
        runner = RecordingRunner()
        confirmation = FormatConfirmation("PHO001L8", SafetyToken.generate("format", "PHO001L8"))
        LTFSCommandBackend.format_tape(
            "PHO001L8", "/dev/st2", confirmation, RealHardwareGuard("real", True, "ack"), runner
        )
        assert runner.calls == [["mkltfs", "-d", "/dev/sg1", "-n", "PHO001L8", "--force"]]

    def test_mount_readwrite_passes_the_sg_node(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._sg_mapping(monkeypatch)
        runner = RecordingRunner()
        LTFSCommandBackend.mount_readwrite(
            "/dev/nst2", "/mnt/ltfs", RealHardwareGuard("real", True, "ack"), runner
        )
        assert runner.calls == [["ltfs", "/mnt/ltfs", "-o", "devname=/dev/sg1,rw"]]

    def test_mount_readonly_passes_the_sg_node(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._sg_mapping(monkeypatch)
        runner = RecordingRunner()
        LTFSCommandBackend.mount_readonly(
            "/dev/st2", "/mnt/ltfs", RealHardwareGuard("real", True, "ack"), runner
        )
        assert runner.calls == [["ltfs", "/mnt/ltfs", "-o", "devname=/dev/sg1,ro"]]

    def test_dry_run_plan_shows_the_device_that_would_be_used(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Resolution happens before the dry-run branch, so a plan never
        # advertises a device different from the one a real run would touch.
        self._sg_mapping(monkeypatch)
        confirmation = FormatConfirmation("PHO001L8", SafetyToken.generate("format", "PHO001L8"))
        result = LTFSCommandBackend.format_tape(
            "PHO001L8",
            "/dev/st2",
            confirmation,
            RealHardwareGuard("real", True, "ack"),
            SafeRunner(dry_run=True),
        )
        assert result.details["device"] == "/dev/sg1"


class TestLtfsDeviceList:
    """`ltfs -o device_list` writes to stderr and exits 1 even on success."""

    @staticmethod
    def _result(stdout: str = "", stderr: str = "", returncode: int = 1) -> CommandResult:
        return CommandResult(
            args=["ltfs", "-o", "device_list"],
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            elapsed_seconds=0.0,
        )

    def test_reads_devices_from_stderr_despite_exit_1(self) -> None:
        # Parsing result.stdout and calling raise_on_error() - the original
        # implementation - could never return anything on a real system.
        runner = RecordingRunner([self._result(stderr=SAMPLE_LTFS_DEVICE_LIST_REAL)])
        devices = LTFSCommandBackend.device_list(runner, RealHardwareGuard("real", True, "ack"))
        assert [device.device for device in devices] == ["/dev/sg4", "/dev/sg2", "/dev/sg1"]

    def test_raises_when_no_device_was_parsed(self) -> None:
        # A genuine failure must still be an error, not an empty list.
        runner = RecordingRunner([self._result(stderr="LTFS12345E something broke")])
        with pytest.raises(CommandError):
            LTFSCommandBackend.device_list(runner, RealHardwareGuard("real", True, "ack"))

    def test_still_reads_devices_from_stdout(self) -> None:
        runner = RecordingRunner([self._result(stdout=SAMPLE_LTFS_DEVICE_LIST, returncode=0)])
        devices = LTFSCommandBackend.device_list(runner, RealHardwareGuard("real", True, "ack"))
        assert [device.device for device in devices] == ["/dev/st0", "/dev/st1"]


class TestUnmountWaitsForTheDriveToBeFree:
    """Unmount promises the DRIVE is free, not merely that umount exited 0.

    "Never unload while LTFS is mounted or dirty" is a project non-negotiable,
    and `can_unload_drive()` gates on mount_state == UNMOUNTED - which callers
    only set when this reports success.
    """

    @staticmethod
    def _guard() -> RealHardwareGuard:
        return RealHardwareGuard("real", True, "ack")

    def test_success_only_when_the_drive_was_released(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("openblade.hardware.ltfs.wait_for_ltfs_release", lambda *_: True)
        result = LTFSCommandBackend.unmount("/mnt/ltfs", self._guard(), RecordingRunner())
        assert result.success is True
        assert result.details["device_released"] is True

    def test_failure_when_ltfs_still_holds_the_drive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # umount exits 0, but LTFS has not let go. Reporting success here is
        # what would let an unload yank the cartridge mid-index-write.
        monkeypatch.setattr("openblade.hardware.ltfs.wait_for_ltfs_release", lambda *_: False)
        result = LTFSCommandBackend.unmount("/mnt/ltfs", self._guard(), RecordingRunner())
        assert result.success is False
        assert result.details["umount_exit_ok"] is True
        assert "still holds the drive" in result.message

    def test_retry_succeeds_once_ltfs_has_gone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Second call: umount fails with "not mounted", but the drive is free,
        # which is what the operation actually promises.
        monkeypatch.setattr("openblade.hardware.ltfs.wait_for_ltfs_release", lambda *_: True)
        runner = RecordingRunner(
            [
                CommandResult(
                    args=["umount", "/mnt/ltfs"],
                    returncode=1,
                    stdout="",
                    stderr="umount: /mnt/ltfs: not mounted.",
                    elapsed_seconds=0.0,
                )
            ]
        )
        result = LTFSCommandBackend.unmount("/mnt/ltfs", self._guard(), runner)
        assert result.success is True

    def test_dry_run_does_not_touch_the_system(self) -> None:
        runner = RecordingRunner()
        runner.dry_run = True
        result = LTFSCommandBackend.unmount("/mnt/ltfs", self._guard(), runner)
        assert result.success is True
        assert runner.calls == []


class TestWaitForLtfsRelease:
    def test_reports_not_released_when_procfs_cannot_be_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Under hidepid, in a container, or as an unprivileged uid we cannot
        # see the LTFS process. "I could not look" must not read as "safe" -
        # this gate protects an unload.
        monkeypatch.setattr(
            "openblade.hardware.ltfs._ltfs_processes_holding", lambda _mount_point: None
        )
        assert wait_for_ltfs_release("/mnt/ltfs", timeout_seconds=0.0) is False

    def test_reports_released_when_nothing_holds_the_mount(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "openblade.hardware.ltfs._ltfs_processes_holding", lambda _mount_point: []
        )
        assert wait_for_ltfs_release("/mnt/ltfs", timeout_seconds=0.0) is True

    def test_times_out_while_a_process_still_holds_the_mount(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "openblade.hardware.ltfs._ltfs_processes_holding", lambda _mount_point: [4242]
        )
        assert wait_for_ltfs_release("/mnt/ltfs", timeout_seconds=0.0) is False

    def test_unreadable_procfs_is_unknown_not_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Exercises the OSError branch itself rather than a stubbed return.
        # Returning [] here would make an unreadable /proc mean "released",
        # which is the fail-open this guard must not have.
        def boom(_self):
            raise PermissionError("hidepid=2")

        monkeypatch.setattr(Path, "iterdir", boom)
        assert _ltfs_processes_holding("/mnt/ltfs") is None
        assert wait_for_ltfs_release("/mnt/ltfs", timeout_seconds=0.0) is False

    def test_procfs_scan_finds_a_real_process_holding_the_mount(self, tmp_path: Path) -> None:
        # Exercise the procfs scan against a real live process named `ltfs`,
        # rather than only against a monkeypatched stand-in. Without this the
        # scan itself is never executed by any unit test.
        mount_point = str(tmp_path / "mnt")
        other_mount = str(tmp_path / "other")

        # `executable=` lets us choose argv[0] independently of the binary, so
        # the child genuinely presents as `ltfs <mount point>` in procfs - a
        # shebang script would show up as /bin/sh and prove nothing.
        process = subprocess.Popen(  # noqa: S603
            ["ltfs", "-c", "import time; time.sleep(30)", mount_point],
            executable=sys.executable,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 10
            while process.pid not in (_ltfs_processes_holding(mount_point) or []):
                assert time.monotonic() < deadline, "procfs scan never saw the ltfs process"
                time.sleep(0.05)
            # It must match on the MOUNT POINT, not merely on the process name.
            assert _ltfs_processes_holding(other_mount) == []
        finally:
            process.terminate()
            process.wait(timeout=10)

        # Once it is gone the mount reads as free.
        deadline = time.monotonic() + 10
        while _ltfs_processes_holding(mount_point):
            assert time.monotonic() < deadline, "process never disappeared from the scan"
            time.sleep(0.05)
