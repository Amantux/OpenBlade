"""``ScalarHttpLibraryBackend.drive_device`` — element -> host device correlation.

The Web Services contract publishes drive *serials* (``GET /aml/drives``) and drive
*element addresses* (``GET /aml/physicalLibrary/elements``) but never the join
between them, so the mapping is operator-declared and machine-checked twice:
against the serials read live from the attached drives (``correlate_drives``), and
against the serials this library reports. These tests pin the refusals.

No network: the session is stubbed, and ``sg_inq`` is replayed by the same
``FakeRunner`` the correlation tests use.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from openblade.config import parse_drive_serial_map
from openblade.domain.errors import DriveCorrelationError
from openblade.domain.policies import RealHardwareGuard
from openblade.hardware.correlation import (
    DriveCorrelation,
    DriveCorrelationEntry,
    correlate_drives,
)
from openblade.hardware.scalar_http.errors import ScalarHttpError
from openblade.hardware.scalar_http.library_backend import ScalarHttpLibraryBackend
from tests.unit.test_drive_correlation import FakeRunner

DRIVE_SERIALS = {"/dev/nst0": "10WT073819", "/dev/nst1": "10WT073820"}
#: Deliberately NOT device order: nst1 is element 0, nst0 is element 1.
SERIAL_MAP = "10WT073820:0,10WT073819:1"


@pytest.fixture
def guard() -> RealHardwareGuard:
    return RealHardwareGuard(
        config_backend="real",
        config_real_hardware_enabled=True,
        operator_acknowledgment="test",
    )


def _correlation(guard: RealHardwareGuard, serial_map: str = SERIAL_MAP) -> DriveCorrelation:
    return correlate_drives(
        devices=list(DRIVE_SERIALS),
        serial_map=parse_drive_serial_map(serial_map),
        runner=FakeRunner(DRIVE_SERIALS),
        guard=guard,
    )


#: Drive element addresses the stub library reports, matching SERIAL_MAP's ids.
ELEMENTS = (0, 1)

DRIVES_PATH = "/aml/drives"
ELEMENTS_PATH = "/aml/physicalLibrary/elements"


class StubSession:
    """Answers the two GETs the backend makes; records the paths asked.

    Path-aware on purpose: ``drive_device`` reads the drive list *and* the element
    list, and they mean different things. A stub that returned one payload for
    every path would let an element-address bug pass unnoticed.
    """

    def __init__(
        self,
        payload: Any = None,
        *,
        error: ScalarHttpError | None = None,
        elements: tuple[int, ...] | None = ELEMENTS,
    ) -> None:
        self._payload = payload
        self._error = error
        self._elements = elements
        self.paths: list[str] = []

    def get_json(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.paths.append(path)
        if path == ELEMENTS_PATH:
            return _elements_payload(self._elements or ())
        if self._error is not None:
            raise self._error
        return self._payload if isinstance(self._payload, dict) else {}


def _drives_payload(*serials: str) -> dict[str, Any]:
    return {"driveList": {"drive": [{"serialNumber": serial} for serial in serials]}}


def _elements_payload(addresses: tuple[int, ...]) -> dict[str, Any]:
    return {
        "elementList": {
            "element": [
                {"type": "drive", "address": address, "state": "empty", "barcode": None}
                for address in addresses
            ]
        }
    }


def _backend(
    session: StubSession, correlation: DriveCorrelation | None
) -> ScalarHttpLibraryBackend:
    factory = (lambda: correlation) if correlation is not None else None
    return ScalarHttpLibraryBackend(
        session,  # type: ignore[arg-type]  # structural stand-in for ScalarHttpSession
        library_id="test-i3",
        correlation_factory=factory,
    )


class TestRefusals:
    def test_no_correlation_configured_names_both_env_vars(self) -> None:
        backend = _backend(StubSession(_drives_payload(*DRIVE_SERIALS.values())), None)

        with pytest.raises(DriveCorrelationError) as excinfo:
            backend.drive_device(0)

        message = str(excinfo.value)
        assert "OPENBLADE_DRIVE_DEVICES" in message
        assert "OPENBLADE_DRIVE_SERIAL_MAP" in message
        # The element the caller asked about is named, not "<unknown>".
        assert "element 0" in message
        assert "<unknown>" not in message

    def test_positional_correlation_is_refused_never_used(self, guard: RealHardwareGuard) -> None:
        # No declared map => correlate_drives returns SOURCE_POSITIONAL. The SCSI
        # backend tolerates that; this one must not, because element order and
        # /dev/nst* order come from two unrelated systems.
        positional = _correlation(guard, serial_map="")
        backend = _backend(StubSession(_drives_payload(*DRIVE_SERIALS.values())), positional)

        with pytest.raises(DriveCorrelationError) as excinfo:
            backend.drive_device(0)

        assert "positional" in str(excinfo.value)
        assert "OPENBLADE_DRIVE_SERIAL_MAP" in str(excinfo.value)

    def test_element_ids_in_the_wrong_number_space_are_refused(
        self, guard: RealHardwareGuard
    ) -> None:
        """The wrong-drive bug, as a unit test.

        The map is written in 1-based bay numbers (or mtx DTE numbering offset by
        one) while this library addresses its drive elements 0 and 1. Every serial
        check passes: the declared serials ARE the attached drives and ARE what the
        library reports. Without this guard, ``drive_device(1)`` — the library's
        SECOND element — would return the device declared for element 1, which is
        the FIRST drive, and LTFS would be written to the wrong cartridge.
        """
        shifted = _correlation(guard, serial_map="10WT073820:1,10WT073819:2")
        backend = _backend(StubSession(_drives_payload(*DRIVE_SERIALS.values())), shifted)

        with pytest.raises(DriveCorrelationError) as excinfo:
            backend.drive_device(1)

        message = str(excinfo.value)
        assert "[1, 2]" in message, "the declared element ids must be named"
        assert "[0, 1]" in message, "the library's real element addresses must be named"
        assert "element address" in message.lower()

    def test_an_uncovered_library_element_is_refused(self, guard: RealHardwareGuard) -> None:
        # The library has a third drive element the map does not cover. A job
        # scheduled onto it would load a cartridge and then have no device to
        # mount it on, stranding the tape.
        session = StubSession(_drives_payload(*DRIVE_SERIALS.values()), elements=(0, 1, 2))
        backend = _backend(session, _correlation(guard))

        with pytest.raises(DriveCorrelationError) as excinfo:
            backend.drive_device(0)

        assert "[0, 1, 2]" in str(excinfo.value)

    def test_partial_library_mismatch_refuses_and_names_the_missing_serial(
        self, guard: RealHardwareGuard
    ) -> None:
        # The library knows one declared drive and not the other: the two sides
        # demonstrably spell serials the same way, so the gap is a real
        # disagreement (wrong library, or a drive moved) and must refuse.
        backend = _backend(
            StubSession(_drives_payload("10WT073820", "SOMEONE-ELSES")), _correlation(guard)
        )

        with pytest.raises(DriveCorrelationError) as excinfo:
            backend.drive_device(0)

        assert "10wt073819" in str(excinfo.value).lower()

    def test_unknown_element_refuses_rather_than_returning_a_neighbour(
        self, guard: RealHardwareGuard
    ) -> None:
        backend = _backend(
            StubSession(_drives_payload(*DRIVE_SERIALS.values())), _correlation(guard)
        )

        with pytest.raises(DriveCorrelationError):
            backend.drive_device(7)


class TestResolution:
    def test_declared_map_wins_over_device_order(self, guard: RealHardwareGuard) -> None:
        backend = _backend(
            StubSession(_drives_payload(*DRIVE_SERIALS.values())), _correlation(guard)
        )

        # SERIAL_MAP puts nst1 at element 0 and nst0 at element 1.
        assert backend.drive_device(0) == "/dev/nst1"
        assert backend.drive_device(1) == "/dev/nst0"

    def test_serials_are_compared_case_and_whitespace_insensitively(
        self, guard: RealHardwareGuard
    ) -> None:
        payload = _drives_payload(" 10wt073819 ", "10WT073820")
        backend = _backend(StubSession(payload), _correlation(guard))

        assert backend.drive_device(0) == "/dev/nst1"

    def test_correlation_and_library_lookup_happen_once(self, guard: RealHardwareGuard) -> None:
        session = StubSession(_drives_payload(*DRIVE_SERIALS.values()))
        calls = 0

        def factory() -> DriveCorrelation:
            nonlocal calls
            calls += 1
            return _correlation(guard)

        backend = ScalarHttpLibraryBackend(
            session,  # type: ignore[arg-type]
            correlation_factory=factory,
        )
        backend.drive_device(0)
        backend.drive_device(1)

        assert calls == 1
        # One element-address check plus one drive-serial check, for two mounts.
        assert session.paths == [ELEMENTS_PATH, DRIVES_PATH]

    def test_a_refusal_is_not_cached(self, guard: RealHardwareGuard) -> None:
        # Fixing the configuration and retrying must work without a restart, so a
        # failed resolution may not poison the cache.
        session = StubSession(_drives_payload("SOMEONE-ELSES", "10WT073820"))
        backend = _backend(session, _correlation(guard))

        with pytest.raises(DriveCorrelationError):
            backend.drive_device(0)

        session._payload = _drives_payload(*DRIVE_SERIALS.values())
        assert backend.drive_device(0) == "/dev/nst1"


class TestAbsenceOfEvidence:
    """Absence is warned about, never silently ignored and never a refusal."""

    def test_disjoint_serial_spellings_warn_and_proceed(
        self, guard: RealHardwareGuard, caplog: pytest.LogCaptureFixture
    ) -> None:
        # A library UI and a SCSI Unit serial number do not always spell the same
        # drive the same way. With ZERO overlap we cannot tell "different library"
        # from "different spelling", and refusing would break a correct install.
        backend = _backend(StubSession(_drives_payload("DRV-001", "DRV-002")), _correlation(guard))

        with caplog.at_level(logging.WARNING):
            assert backend.drive_device(0) == "/dev/nst1"

        assert "NOT cross-checked" in caplog.text

    def test_unreadable_drive_list_warns_and_proceeds(
        self, guard: RealHardwareGuard, caplog: pytest.LogCaptureFixture
    ) -> None:
        error = ScalarHttpError("drives unavailable", status_code=404, action="GET /aml/drives")
        backend = _backend(StubSession(error=error), _correlation(guard))

        with caplog.at_level(logging.WARNING):
            assert backend.drive_device(0) == "/dev/nst1"

        assert "not cross-checked" in caplog.text.lower()

    def test_a_transport_failure_warns_and_proceeds_without_leaking_the_url(
        self, guard: RealHardwareGuard, caplog: pytest.LogCaptureFixture
    ) -> None:
        # ScalarHttpSession only converts >=400 responses; a connect/timeout/TLS
        # failure arrives as a bare httpx error whose text carries the target URL,
        # and OPENBLADE_SCALAR_URL can embed credentials.
        import httpx

        secret = "connection refused to https://admin:hunter2@i3.internal"

        class ExplodingSession(StubSession):
            def get_json(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
                if path == DRIVES_PATH:
                    raise httpx.ConnectError(secret)
                return super().get_json(path, params=params)

        backend = _backend(ExplodingSession(), _correlation(guard))

        with caplog.at_level(logging.WARNING):
            assert backend.drive_device(0) == "/dev/nst1"

        assert "hunter2" not in caplog.text
        assert "ConnectError" in caplog.text

    def test_empty_drive_list_is_distinguished_from_unreadable(self) -> None:
        assert (
            _backend(StubSession({"driveList": {"drive": []}}), None).library_drive_serials() == []
        )
        assert _backend(StubSession({}), None).library_drive_serials() is None


class TestMutationCheck:
    """The refusal must be load-bearing: remove the check and these must fail.

    Recorded here rather than left to a reviewer's imagination — the guard being
    tested is "a mismatched map refuses", and a test that passes with no guard at
    all is worse than no test.
    """

    def test_mismatch_refusal_fails_when_the_check_is_removed(
        self, guard: RealHardwareGuard, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import openblade.hardware.scalar_http.library_backend as module

        monkeypatch.setattr(module, "verify_against_library_serials", lambda **_: ())
        backend = _backend(
            StubSession(_drives_payload("10WT073820", "SOMEONE-ELSES")), _correlation(guard)
        )

        # With the guard neutered the mismatched map is accepted — which is exactly
        # what test_partial_library_mismatch_refuses_and_names_the_missing_serial
        # would then fail to catch, proving that test is not vacuous.
        assert backend.drive_device(0) == "/dev/nst1"

    def test_positional_refusal_fails_when_the_source_check_is_removed(
        self, guard: RealHardwareGuard, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import openblade.hardware.scalar_http.library_backend as module

        monkeypatch.setattr(module, "SOURCE_SERIAL_MAP", "positional")
        backend = _backend(
            StubSession(_drives_payload(*DRIVE_SERIALS.values())),
            _correlation(guard, serial_map=""),
        )

        assert backend.drive_device(0) == "/dev/nst0"

    def test_the_wrong_drive_really_happens_without_the_element_address_check(
        self, guard: RealHardwareGuard, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Proof that the element-address guard is what stops a wrong-drive write.

        Neuter only that guard and the shifted map is accepted: asking for the
        library's element 1 — physically the FIRST drive, which is on /dev/nst0 —
        hands back /dev/nst1, the second drive. That is a silent wrong-drive LTFS
        write, and it is exactly what
        ``test_element_ids_in_the_wrong_number_space_are_refused`` prevents.
        """
        monkeypatch.setattr(
            ScalarHttpLibraryBackend, "_refuse_on_element_address_mismatch", lambda self, _: None
        )
        shifted = _correlation(guard, serial_map="10WT073820:1,10WT073819:2")
        backend = _backend(StubSession(_drives_payload(*DRIVE_SERIALS.values())), shifted)

        assert backend.drive_device(1) == "/dev/nst1"


class TestLibraryDriveSerials:
    def test_malformed_payloads_report_unreadable_not_empty(self) -> None:
        for payload in ({"driveList": "nope"}, {"driveList": {"drive": "nope"}}, {"other": 1}):
            assert _backend(StubSession(payload), None).library_drive_serials() is None

    def test_drives_without_a_serial_are_dropped(self) -> None:
        payload = {"driveList": {"drive": [{"serialNumber": "A"}, {"model": "no serial"}, "junk"]}}

        assert _backend(StubSession(payload), None).library_drive_serials() == ["A"]


def test_correlation_entry_devices_are_not_reordered(guard: RealHardwareGuard) -> None:
    correlation = _correlation(guard)

    assert correlation.entries == (
        DriveCorrelationEntry(drive_id=0, device="/dev/nst1", serial="10WT073820"),
        DriveCorrelationEntry(drive_id=1, device="/dev/nst0", serial="10WT073819"),
    )
