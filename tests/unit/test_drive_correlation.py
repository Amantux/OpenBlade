"""Drive correlation: bind mtx Data Transfer Elements to /dev/nst* devices.

Covers the safety guard from docs/runbooks/real-i3-bringup-plan.md ("Drive order
!= device order") with no real hardware: a fake SafeRunner replays captured
`sg_inq` output per device.
"""

from __future__ import annotations

import logging

import pytest

import openblade.hardware.correlation as correlation_module
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
        with pytest.raises(DriveCorrelationError):
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

        assert correlation.serials_verified is True
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

    def test_uncorrelated_drive_element_raises_a_typed_error(
        self, guard: RealHardwareGuard
    ) -> None:
        correlation = correlate_drives(
            devices=list(DRIVE_SERIALS),
            serial_map=parse_drive_serial_map(THREE_DRIVE_MAP),
            runner=FakeRunner(DRIVE_SERIALS),
            guard=guard,
            element_count=3,
        )
        with pytest.raises(DriveCorrelationError, match="no correlated host device"):
            correlation.device_for(3)

    def test_serial_check_does_not_claim_the_element_assignment_was_verified(
        self, guard: RealHardwareGuard
    ) -> None:
        """A transposed declaration passes the serial check — by design, documented.

        Nothing observes which serial sits in which element, so the check proves
        the SET of attached drives, not the assignment. This test pins the
        limitation so the API can never quietly start claiming more: the flag is
        named `serials_verified`, and there is no `verified` attribute to read.
        """
        transposed = correlate_drives(
            devices=list(DRIVE_SERIALS),
            # Every element rotated by one (the 1-based-bay-number mistake).
            serial_map=parse_drive_serial_map("10WT073820:1,10WT073821:2,10WT073819:0"),
            runner=FakeRunner(DRIVE_SERIALS),
            guard=guard,
            element_count=3,
        )
        assert transposed.serials_verified is True
        assert transposed.device_for(0) == "/dev/nst0"  # the rotation is NOT detected
        assert not hasattr(transposed, "verified")


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

    def test_duplicate_live_serials_refuse_without_a_map_too(
        self, guard: RealHardwareGuard
    ) -> None:
        """Two devices that are one drive (e.g. /dev/st0 and /dev/nst0) can never be right."""
        aliased = {"/dev/st0": "SAME", "/dev/nst0": "SAME"}
        with pytest.raises(DriveCorrelationError, match="same physical drive"):
            correlate_drives(
                devices=list(aliased),
                serial_map=(),
                runner=FakeRunner(aliased),
                guard=guard,
                element_count=2,
            )

    def test_duplicate_device_paths_refuse(self, guard: RealHardwareGuard) -> None:
        with pytest.raises(DriveCorrelationError, match="more than once"):
            correlate_drives(
                devices=["/dev/nst0", "/dev/nst0", "/dev/nst2"],
                serial_map=(),
                runner=FakeRunner(DRIVE_SERIALS),
                guard=guard,
            )

    def test_sg_inq_failure_is_a_typed_correlation_error(self, guard: RealHardwareGuard) -> None:
        """A drive that cannot be probed must not be silently treated as correlated."""
        runner = FakeRunner(DRIVE_SERIALS)
        with pytest.raises(DriveCorrelationError, match="sg_inq failed"):
            correlate_drives(
                devices=["/dev/nst9"],
                serial_map=(),
                runner=runner,
                guard=guard,
            )

    def test_missing_sg_inq_binary_is_a_typed_correlation_error(
        self, guard: RealHardwareGuard
    ) -> None:
        class NoSgInqRunner(SafeRunner):
            def run(
                self,
                args: list[str],
                timeout: int | None = None,
                redact_args: list[int] | None = None,
            ) -> CommandResult:
                raise FileNotFoundError(args[0])

        with pytest.raises(DriveCorrelationError, match="sg_inq is not installed"):
            correlate_drives(
                devices=["/dev/nst0"],
                serial_map=(),
                runner=NoSgInqRunner(dry_run=False),
                guard=guard,
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

    def test_no_devices_warns_and_refuses_only_on_use(
        self, guard: RealHardwareGuard, caplog: pytest.LogCaptureFixture
    ) -> None:
        # connect-i3 is the diagnostic you run *because* no drive shows up, so an
        # empty device list must still produce a report; using a drive then fails.
        with caplog.at_level(logging.WARNING, logger="openblade.hardware.correlation"):
            correlation = correlate_drives(
                devices=[],
                serial_map=(),
                runner=FakeRunner(DRIVE_SERIALS),
                guard=guard,
            )
        assert correlation.entries == ()
        assert any("No tape devices" in record.getMessage() for record in caplog.records)
        with pytest.raises(DriveCorrelationError):
            correlation.device_for(0)


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
        assert correlation.serials_verified is False
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

    def test_dry_run_says_the_declared_map_could_not_be_applied(
        self, guard: RealHardwareGuard, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A map binds serials to elements, and serials need a live probe.

        A dry run therefore cannot apply it — and must not present its positional
        guess as though it were the plan the live run will follow.
        """
        with caplog.at_level(logging.WARNING, logger="openblade.hardware.correlation"):
            correlation = correlate_drives(
                devices=list(DRIVE_SERIALS),
                serial_map=parse_drive_serial_map(THREE_DRIVE_MAP),
                runner=SafeRunner(dry_run=True),
                guard=guard,
                element_count=3,
            )
        assert correlation.source == SOURCE_DRY_RUN
        assert correlation.serials_verified is False
        assert correlation.serial_for(0) == ""
        assert any("could not be applied" in warning for warning in correlation.warnings)
        assert any("could not be applied" in record.getMessage() for record in caplog.records)
        live = correlate_drives(
            devices=list(DRIVE_SERIALS),
            serial_map=parse_drive_serial_map(THREE_DRIVE_MAP),
            runner=FakeRunner(DRIVE_SERIALS),
            guard=guard,
            element_count=3,
        )
        # Proof the disclosure is needed: the orders genuinely differ.
        assert correlation.devices_in_drive_order() != live.devices_in_drive_order()

    def test_dry_run_refuses_an_out_of_range_declared_element(
        self, guard: RealHardwareGuard
    ) -> None:
        with pytest.raises(DriveCorrelationError, match="only 3 Data Transfer Element"):
            correlate_drives(
                devices=list(DRIVE_SERIALS),
                serial_map=parse_drive_serial_map("A:0,B:1,C:9"),
                runner=SafeRunner(dry_run=True),
                guard=guard,
                element_count=3,
            )

    def test_dry_run_is_positional_without_a_map(self, guard: RealHardwareGuard) -> None:
        correlation = correlate_drives(
            devices=list(DRIVE_SERIALS),
            serial_map=(),
            runner=SafeRunner(dry_run=True),
            guard=guard,
        )
        assert correlation.devices_in_drive_order() == list(DRIVE_SERIALS)


class TestGuardIsEnforced:
    def test_correlation_refuses_before_probing_anything(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The pre-check exists so a closed gate reaches NO device at all.

        Asserting only that the call raises is vacuous — sg_inq validates the
        guard itself — so this stubs sg_inq and asserts it was never reached.
        """
        probed: list[str] = []
        monkeypatch.setattr(
            correlation_module,
            "sg_inq",
            lambda device, runner, guard: probed.append(device),  # type: ignore[misc,return-value]
        )
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
        assert probed == []


class TestEveryElementNeedsADevice:
    def test_declared_map_with_an_uncovered_element_refuses(self, guard: RealHardwareGuard) -> None:
        """The scheduler sizes itself from the changer, so an element with no
        device would get a cartridge loaded into it and strand it at mount."""
        two = {"/dev/nst0": "A1", "/dev/nst1": "B2"}
        with pytest.raises(DriveCorrelationError, match="strand a cartridge"):
            correlate_drives(
                devices=list(two),
                serial_map=parse_drive_serial_map("A1:0,B2:1"),
                runner=FakeRunner(two),
                guard=guard,
                element_count=3,
            )

    def test_more_devices_than_elements_is_only_a_warning(self, guard: RealHardwareGuard) -> None:
        # A spare device the library does not expose as an element strands nothing.
        correlation = correlate_drives(
            devices=list(DRIVE_SERIALS),
            serial_map=(),
            runner=FakeRunner(DRIVE_SERIALS),
            guard=guard,
            element_count=2,
        )
        assert any("2 drive element" in warning for warning in correlation.warnings)
        assert len(correlation.entries) == 3

    def test_uncovered_element_without_a_map_warns_and_refuses_on_use(
        self, guard: RealHardwareGuard
    ) -> None:
        """Without a declared map we cannot tell "not cabled" from "forgot one",
        so this stays a warning — and using the element refuses with a typed error."""
        two = {"/dev/nst0": "A1", "/dev/nst1": "B2"}
        correlation = correlate_drives(
            devices=list(two),
            serial_map=(),
            runner=FakeRunner(two),
            guard=guard,
            element_count=3,
        )
        assert any("3 drive element" in warning for warning in correlation.warnings)
        with pytest.raises(DriveCorrelationError, match="no correlated host device"):
            correlation.device_for(2)


class TestSgNodeProbing:
    def test_serials_are_read_from_the_probe_node_when_given(
        self, guard: RealHardwareGuard
    ) -> None:
        """LTFS holds /dev/nstN open; the generic sg node always answers INQUIRY."""
        by_sg = {"/dev/sg4": "10WT073819", "/dev/sg5": "10WT073820"}
        runner = FakeRunner(by_sg)
        correlation = correlate_drives(
            devices=["/dev/nst0", "/dev/nst1"],
            serial_map=parse_drive_serial_map("10WT073819:0,10WT073820:1"),
            runner=runner,
            guard=guard,
            element_count=2,
            probe_devices={"/dev/nst0": "/dev/sg4", "/dev/nst1": "/dev/sg5"},
        )
        assert [call[-1] for call in runner.calls] == ["/dev/sg4", "/dev/sg5"]
        # The correlated device is still the node the writer opens.
        assert correlation.device_for(0) == "/dev/nst0"
        assert correlation.serial_for(0) == "10WT073819"
