"""Drive correlation: bind mtx Data Transfer Elements to /dev/nst* devices.

Covers the safety guard from docs/runbooks/real-i3-bringup-plan.md ("Drive order
!= device order") with no real hardware: a fake SafeRunner replays captured
`sg_inq` output per device.
"""

from __future__ import annotations

import logging

import pytest

from openblade.config import parse_drive_serial_map
from openblade.domain.errors import DriveCorrelationError, SafetyViolationError
from openblade.domain.policies import RealHardwareGuard
from openblade.hardware.correlation import (
    SOURCE_DRY_RUN,
    SOURCE_POSITIONAL,
    SOURCE_SERIAL_MAP,
    correlate_drives,
    read_drive_serials,
)
from openblade.hardware.runner import CommandResult, SafeRunner

# Serials of a three-drive Scalar i3 whose DEVICE order is deliberately NOT the
# library's drive-element order: nst0 is DTE 2, nst1 is DTE 0, nst2 is DTE 1.
DRIVE_SERIALS = {
    "/dev/nst0": "10WT073819",
    "/dev/nst1": "10WT073820",
    "/dev/nst2": "10WT073821",
}
THREE_DRIVE_MAP = "10WT073820:0,10WT073821:1,10WT073819:2"


def _sg_inq_output(serial: str) -> str:
    return (
        "standard INQUIRY:\n"
        "  PQual=0  PDT=1  RMB=1  LU_CONG=0  version=0x06  [SPC-4]\n"
        "    length=96 (0x60)   Peripheral device type: tape\n"
        " Vendor identification: IBM\n"
        " Product identification: ULTRIUM-TD8\n"
        " Product revision level: H3S4\n"
        f" Unit serial number: {serial}\n"
    )


class FakeRunner(SafeRunner):
    """SafeRunner that replays per-device sg_inq output instead of executing it."""

    def __init__(self, serials: dict[str, str]) -> None:
        super().__init__(dry_run=False)
        self.serials = serials
        self.calls: list[list[str]] = []

    def run(
        self,
        args: list[str],
        timeout: int | None = None,
        redact_args: list[int] | None = None,
    ) -> CommandResult:
        self.calls.append(list(args))
        assert args[0] == "sg_inq", f"unexpected command {args!r}"
        device = args[-1]
        if device not in self.serials:
            return CommandResult(
                args=args, returncode=2, stdout="", stderr="no such device", elapsed_seconds=0.0
            )
        return CommandResult(
            args=args,
            returncode=0,
            stdout=_sg_inq_output(self.serials[device]),
            stderr="",
            elapsed_seconds=0.0,
        )


@pytest.fixture
def guard() -> RealHardwareGuard:
    return RealHardwareGuard(
        config_backend="real",
        config_real_hardware_enabled=True,
        operator_acknowledgment="test",
    )


class TestParseDriveSerialMap:
    def test_parses_three_entries(self) -> None:
        assert parse_drive_serial_map(THREE_DRIVE_MAP) == (
            ("10WT073820", 0),
            ("10WT073821", 1),
            ("10WT073819", 2),
        )

    def test_empty_is_empty(self) -> None:
        assert parse_drive_serial_map("") == ()
        assert parse_drive_serial_map("  ,  ") == ()

    def test_tolerates_whitespace(self) -> None:
        assert parse_drive_serial_map(" A1 : 0 , B2:1 ") == (("A1", 0), ("B2", 1))

    @pytest.mark.parametrize(
        "raw",
        [
            "10WT073819",  # no separator
            ":0",  # no serial
            "10WT073819:",  # no drive id
            "10WT073819:x",  # non-integer drive id
            "10WT073819:-1",  # negative drive id
            "A:0,A:1",  # duplicate serial
            "A:0,B:0",  # duplicate drive element
        ],
    )
    def test_malformed_entries_raise(self, raw: str) -> None:
        with pytest.raises(ValueError):
            parse_drive_serial_map(raw)


class TestReadDriveSerials:
    def test_reads_one_serial_per_device(self, guard: RealHardwareGuard) -> None:
        runner = FakeRunner(DRIVE_SERIALS)
        assert read_drive_serials(list(DRIVE_SERIALS), runner, guard) == DRIVE_SERIALS
        assert [call[-1] for call in runner.calls] == list(DRIVE_SERIALS)


class TestCorrelateDrivesHappyPath:
    def test_three_drives_bind_to_declared_elements(self, guard: RealHardwareGuard) -> None:
        correlation = correlate_drives(
            devices=list(DRIVE_SERIALS),
            serial_map=parse_drive_serial_map(THREE_DRIVE_MAP),
            runner=FakeRunner(DRIVE_SERIALS),
            guard=guard,
            element_count=3,
        )

        assert correlation.verified is True
        assert correlation.source == SOURCE_SERIAL_MAP
        assert correlation.warnings == ()
        # The whole point: element order != device order.
        assert correlation.device_for(0) == "/dev/nst1"
        assert correlation.device_for(1) == "/dev/nst2"
        assert correlation.device_for(2) == "/dev/nst0"
        assert correlation.serial_for(0) == "10WT073820"
        assert correlation.devices_in_drive_order() == ["/dev/nst1", "/dev/nst2", "/dev/nst0"]

    def test_payload_is_serializable_for_reports(self, guard: RealHardwareGuard) -> None:
        correlation = correlate_drives(
            devices=list(DRIVE_SERIALS),
            serial_map=parse_drive_serial_map(THREE_DRIVE_MAP),
            runner=FakeRunner(DRIVE_SERIALS),
            guard=guard,
            element_count=3,
        )
        assert correlation.to_payload()[0] == {
            "driveId": 0,
            "device": "/dev/nst1",
            "serial": "10WT073820",
        }

    def test_unknown_drive_id_raises_keyerror(self, guard: RealHardwareGuard) -> None:
        correlation = correlate_drives(
            devices=list(DRIVE_SERIALS),
            serial_map=parse_drive_serial_map(THREE_DRIVE_MAP),
            runner=FakeRunner(DRIVE_SERIALS),
            guard=guard,
            element_count=3,
        )
        with pytest.raises(KeyError):
            correlation.device_for(3)


class TestCorrelateDrivesRefusesMismatch:
    """The safety guard: a declared map that disagrees with live serials must refuse."""

    def test_declared_serial_not_attached_refuses(self, guard: RealHardwareGuard) -> None:
        # The operator declared a drive that has since been swapped out.
        with pytest.raises(DriveCorrelationError) as excinfo:
            correlate_drives(
                devices=list(DRIVE_SERIALS),
                serial_map=parse_drive_serial_map("10WT073820:0,10WT073821:1,10WT_SWAPPED_OUT:2"),
                runner=FakeRunner(DRIVE_SERIALS),
                guard=guard,
                element_count=3,
            )
        message = str(excinfo.value)
        assert "10WT_SWAPPED_OUT" in message
        assert "10WT073819" in message  # the attached-but-undeclared serial
        assert "Refusing to start" in message

    def test_drive_correlation_error_is_a_safety_violation(self) -> None:
        assert issubclass(DriveCorrelationError, SafetyViolationError)

    def test_partial_map_refuses(self, guard: RealHardwareGuard) -> None:
        # Only two of three drives declared: the third would silently fall back to
        # a positional guess, which is exactly the bug this guard exists to stop.
        with pytest.raises(DriveCorrelationError, match="not declared"):
            correlate_drives(
                devices=list(DRIVE_SERIALS),
                serial_map=parse_drive_serial_map("10WT073820:0,10WT073821:1"),
                runner=FakeRunner(DRIVE_SERIALS),
                guard=guard,
                element_count=3,
            )

    def test_duplicate_live_serials_refuse(self, guard: RealHardwareGuard) -> None:
        cloned = {"/dev/nst0": "SAME", "/dev/nst1": "SAME", "/dev/nst2": "10WT073821"}
        with pytest.raises(DriveCorrelationError, match="same unit serial number"):
            correlate_drives(
                devices=list(cloned),
                serial_map=parse_drive_serial_map("SAME:0,10WT073821:1"),
                runner=FakeRunner(cloned),
                guard=guard,
                element_count=3,
            )

    def test_device_without_a_serial_refuses(self, guard: RealHardwareGuard) -> None:
        blank = {**DRIVE_SERIALS, "/dev/nst2": ""}
        with pytest.raises(DriveCorrelationError, match="no unit serial number"):
            correlate_drives(
                devices=list(blank),
                serial_map=parse_drive_serial_map(THREE_DRIVE_MAP),
                runner=FakeRunner(blank),
                guard=guard,
                element_count=3,
            )

    def test_element_id_beyond_the_changer_refuses(self, guard: RealHardwareGuard) -> None:
        # Three devices declared onto elements 0,1,5 but the changer reports 3 DTEs.
        with pytest.raises(DriveCorrelationError, match="only 3 Data Transfer Element"):
            correlate_drives(
                devices=list(DRIVE_SERIALS),
                serial_map=parse_drive_serial_map("10WT073820:0,10WT073821:1,10WT073819:5"),
                runner=FakeRunner(DRIVE_SERIALS),
                guard=guard,
                element_count=3,
            )

    def test_no_devices_refuses(self, guard: RealHardwareGuard) -> None:
        with pytest.raises(DriveCorrelationError, match="No tape devices configured"):
            correlate_drives(
                devices=[],
                serial_map=(),
                runner=FakeRunner(DRIVE_SERIALS),
                guard=guard,
            )


class TestPositionalFallback:
    def test_positional_fallback_warns_loudly(
        self, guard: RealHardwareGuard, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="openblade.hardware.correlation"):
            correlation = correlate_drives(
                devices=list(DRIVE_SERIALS),
                serial_map=(),
                runner=FakeRunner(DRIVE_SERIALS),
                guard=guard,
                element_count=3,
            )

        assert correlation.source == SOURCE_POSITIONAL
        assert correlation.verified is False
        assert correlation.device_for(0) == "/dev/nst0"
        # Serials are still captured so the operator can build the map from the log.
        assert correlation.serial_for(2) == "10WT073821"
        warnings = [record.getMessage() for record in caplog.records]
        assert any("DRIVE ORDER UNVERIFIED" in message for message in warnings)
        assert any("OPENBLADE_DRIVE_SERIAL_MAP" in message for message in warnings)
        assert any("10WT073819" in message for message in warnings)

    def test_device_count_mismatch_is_warned_not_fatal(
        self, guard: RealHardwareGuard, caplog: pytest.LogCaptureFixture
    ) -> None:
        two_devices = {"/dev/nst0": "A1", "/dev/nst1": "B2"}
        with caplog.at_level(logging.WARNING, logger="openblade.hardware.correlation"):
            correlation = correlate_drives(
                devices=list(two_devices),
                serial_map=(),
                runner=FakeRunner(two_devices),
                guard=guard,
                element_count=3,
            )
        assert len(correlation.entries) == 2
        assert any("3 drive element" in warning for warning in correlation.warnings)

    def test_dry_run_claims_nothing(self, guard: RealHardwareGuard) -> None:
        correlation = correlate_drives(
            devices=list(DRIVE_SERIALS),
            serial_map=parse_drive_serial_map(THREE_DRIVE_MAP),
            runner=SafeRunner(dry_run=True),
            guard=guard,
            element_count=3,
        )
        assert correlation.source == SOURCE_DRY_RUN
        assert correlation.verified is False
        assert correlation.serial_for(0) == ""


class TestGuardIsEnforced:
    def test_correlation_requires_the_real_hardware_gate(self) -> None:
        closed_guard = RealHardwareGuard(
            config_backend="mock",
            config_real_hardware_enabled=False,
            operator_acknowledgment="test",
        )
        with pytest.raises(SafetyViolationError):
            correlate_drives(
                devices=list(DRIVE_SERIALS),
                serial_map=(),
                runner=FakeRunner(DRIVE_SERIALS),
                guard=closed_guard,
            )
