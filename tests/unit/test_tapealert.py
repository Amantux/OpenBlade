"""TapeAlert parser, severity table, and guard enforcement.

Fixtures under ``tests/fixtures/tapealert/`` are *real* sg3_utils 1.46 output:
the clean/undecodable/unsupported cases were captured from the rig, and the
set-flag cases were produced by hand-encoding log page 0x2E and decoding it with
the tool itself (``sg_logs --in=<ascii-hex>``), because mhvtl never sets a flag.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openblade.domain.errors import RealHardwareDisabledError
from openblade.domain.policies import RealHardwareGuard
from openblade.hardware.runner import CommandResult, SafeRunner
from openblade.hardware.tapealert import (
    SG_LOGS_FLAG_NAMES,
    TAPEALERT_FLAGS,
    TapeAlertSeverity,
    parse_sg_logs_tapealert,
    read_tape_alerts,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "tapealert"


def fixture(name: str) -> str:
    return (FIXTURES / f"{name}.txt").read_text()


def _guard() -> RealHardwareGuard:
    return RealHardwareGuard(
        config_backend="real",
        config_real_hardware_enabled=True,
        operator_acknowledgment="test",
    )


class _StubRunner(SafeRunner):
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        super().__init__(dry_run=False)
        self._result = CommandResult(
            args=[], returncode=returncode, stdout=stdout, stderr=stderr, elapsed_seconds=0.0
        )
        self.calls: list[list[str]] = []

    def run(
        self,
        args: list[str],
        timeout: int | None = None,
        redact_args: list[int] | None = None,
    ) -> CommandResult:
        self.calls.append(list(args))
        return self._result


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------


def test_clean_drive_reports_all_flags_clear() -> None:
    report = parse_sg_logs_tapealert(fixture("clean_drive"), device="/dev/sg1")
    assert report.supported is True
    assert len(report.flags) == 64
    assert report.active == ()
    assert report.worst_severity is None


def test_warning_flags_are_decoded_with_numbers_and_severity() -> None:
    report = parse_sg_logs_tapealert(fixture("warning_flags"))
    assert {flag.number for flag in report.active} == {1, 21}
    assert {flag.severity for flag in report.active} == {TapeAlertSeverity.WARNING}
    assert report.worst_severity is TapeAlertSeverity.WARNING


def test_critical_flags_dominate_the_worst_severity() -> None:
    report = parse_sg_logs_tapealert(fixture("critical_flags"))
    assert {flag.number for flag in report.active} == {4, 30}
    assert report.worst_severity is TapeAlertSeverity.CRITICAL
    assert {flag.name for flag in report.active} == {"Media", "Hardware A"}


def test_undecodable_page_is_unsupported_not_healthy() -> None:
    """A changer answers LOG SENSE 0x2e with a non-TapeAlert payload.

    Reporting that as "no flags set" would be a silent lie about a device we
    never actually read, so it must come back unsupported.
    """
    report = parse_sg_logs_tapealert(fixture("changer_undecodable"), device="/dev/sg2")
    assert report.supported is False
    assert report.active == ()
    assert report.reason is not None


def test_illegal_request_is_unsupported() -> None:
    report = parse_sg_logs_tapealert(fixture("page_unsupported"), device="/dev/sg1")
    assert report.supported is False
    assert "illegal request" in (report.reason or "")


def test_malformed_output_does_not_raise() -> None:
    report = parse_sg_logs_tapealert(fixture("malformed"))
    # The one parseable line has an unknown name: kept, but with no number and
    # UNKNOWN severity rather than being silently mapped to something real.
    assert report.supported is True
    assert [(flag.number, flag.severity) for flag in report.active] == [
        (None, TapeAlertSeverity.UNKNOWN)
    ]


def test_empty_output_is_unsupported() -> None:
    report = parse_sg_logs_tapealert("")
    assert report.supported is False


# --------------------------------------------------------------------------
# severity table
# --------------------------------------------------------------------------


def test_table_covers_all_64_flags() -> None:
    assert sorted(TAPEALERT_FLAGS) == list(range(1, 65))
    assert len(SG_LOGS_FLAG_NAMES) == 64


@pytest.mark.parametrize(
    ("number", "name", "severity"),
    [
        (1, "Read Warning", TapeAlertSeverity.WARNING),
        (4, "Media", TapeAlertSeverity.CRITICAL),
        (10, "No Removal", TapeAlertSeverity.INFORMATION),
        (20, "Clean Now", TapeAlertSeverity.CRITICAL),
        (21, "Clean Periodic", TapeAlertSeverity.WARNING),
        (30, "Hardware A", TapeAlertSeverity.CRITICAL),
        (39, "Diagnostics Required", TapeAlertSeverity.WARNING),
        (50, "Lost Statistics", TapeAlertSeverity.WARNING),
        (54, "No Start of Data", TapeAlertSeverity.CRITICAL),
    ],
)
def test_severity_spot_checks(number: int, name: str, severity: TapeAlertSeverity) -> None:
    """Spot-checks against T10/02-142r0 "Tape Drive Flag Definitions"."""
    spec = TAPEALERT_FLAGS[number]
    assert spec.name == name
    assert spec.severity is severity


def test_flags_outside_the_spec_table_are_unknown_not_guessed() -> None:
    # SSC-3 added 55-60; TapeAlert v3.0 does not classify them, so we do not
    # invent a severity for them.
    for number in range(55, 65):
        assert TAPEALERT_FLAGS[number].severity is TapeAlertSeverity.UNKNOWN


def test_sg_logs_names_all_resolve_to_their_flag_number() -> None:
    """Every name sg3_utils prints must map back to its parameter code."""
    lines = "\n".join(f"  {name}: 1" for name in SG_LOGS_FLAG_NAMES)
    report = parse_sg_logs_tapealert(f"Tape alert page (ssc-3) [0x2e]\n{lines}\n")
    assert [flag.number for flag in report.flags] == list(range(1, 65))


# --------------------------------------------------------------------------
# guard enforcement
# --------------------------------------------------------------------------


def test_read_tape_alerts_requires_the_guard() -> None:
    """Mutation check: delete `guard.validate()` in read_tape_alerts and this fails."""
    runner = _StubRunner(stdout=fixture("clean_drive"))
    disabled = RealHardwareGuard(
        config_backend="mock",
        config_real_hardware_enabled=False,
        operator_acknowledgment="test",
    )
    with pytest.raises(RealHardwareDisabledError):
        read_tape_alerts("/dev/nst0", runner, disabled)
    assert runner.calls == []  # nothing was executed against the device


def test_read_tape_alerts_requires_operator_acknowledgment() -> None:
    runner = _StubRunner(stdout=fixture("clean_drive"))
    unacknowledged = RealHardwareGuard(
        config_backend="real",
        config_real_hardware_enabled=True,
        operator_acknowledgment="",
    )
    with pytest.raises(RealHardwareDisabledError):
        read_tape_alerts("/dev/nst0", runner, unacknowledged)
    assert runner.calls == []


def test_read_tape_alerts_invokes_sg_logs_with_the_page() -> None:
    runner = _StubRunner(stdout=fixture("critical_flags"))
    report = read_tape_alerts("/dev/sg1", runner, _guard())
    assert runner.calls == [["sg_logs", "-p", "0x2e", "/dev/sg1"]]
    assert report.worst_severity is TapeAlertSeverity.CRITICAL


def test_dry_run_never_touches_the_device() -> None:
    runner = SafeRunner(dry_run=True)
    report = read_tape_alerts("/dev/sg1", runner, _guard())
    assert report.supported is True
    assert report.active == ()


def test_command_failure_is_reported_not_mistaken_for_a_clean_drive() -> None:
    runner = _StubRunner(stderr="sg_logs: error opening file: /dev/sg9", returncode=2)
    report = read_tape_alerts("/dev/sg9", runner, _guard())
    assert report.supported is False
    assert "sg_logs exited 2" in (report.reason or "")


# --------------------------------------------------------------------------
# CLI degradation
# --------------------------------------------------------------------------


def test_drive_health_cli_refuses_without_real_hardware(monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from openblade.cli.main import app

    monkeypatch.delenv("OPENBLADE_BACKEND", raising=False)
    monkeypatch.delenv("OPENBLADE_REAL_HARDWARE_ENABLED", raising=False)
    result = CliRunner().invoke(app, ["hardware", "drive-health", "--device", "/dev/nst0"])
    assert result.exit_code == 1
    assert "OPENBLADE_BACKEND=real" in result.output


def test_drive_health_cli_reports_unsupported_drives(monkeypatch: pytest.MonkeyPatch) -> None:
    """No TapeAlert page must read as "not supported", and still exit 0."""
    from typer.testing import CliRunner

    from openblade.cli import fuse_and_health
    from openblade.cli.main import app
    from openblade.hardware.sg import ScsiInquiry
    from openblade.hardware.tapealert import TapeAlertReport

    monkeypatch.setenv("OPENBLADE_BACKEND", "real")
    monkeypatch.setenv("OPENBLADE_REAL_HARDWARE_ENABLED", "true")
    monkeypatch.setattr(
        fuse_and_health,
        "sg_inq",
        lambda device, runner, guard: ScsiInquiry(
            device_type="tape", vendor="IBM", product="ULT3580-TD8", revision="HB81", serial="X1"
        ),
    )
    monkeypatch.setattr(
        fuse_and_health,
        "read_tape_alerts",
        lambda device, runner, guard: TapeAlertReport(
            device=device, supported=False, flags=(), reason="no TapeAlert page in sg_logs output"
        ),
    )
    result = CliRunner().invoke(app, ["hardware", "drive-health", "--device", "/dev/nst0"])
    assert result.exit_code == 0
    assert "not supported by this drive" in result.output
