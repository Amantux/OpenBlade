"""``drive_device`` against a real, separately-launched i3 emulator process.

The unit tests stub the session; this one proves the contract claim the design
rests on — that ``GET /aml/drives`` really does publish serials, and that
``GET /aml/physicalLibrary/elements`` really does not — by asking a live emulator
over HTTP, not by asking the code that would have to be wrong for the claim to be
wrong.

The emulator is launched exactly as the i3-compliance workflow launches it
(``OPENBLADE_BACKEND=mock OPENBLADE_DB_URL=sqlite:///<tmp> uvicorn
openblade.api.main:app``). Set ``I3_AML_URL`` to reuse an already-running one.
``sg_inq`` is still replayed — the host in CI has no tape drives — so what is
exercised end-to-end is the library half of the correlation.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Generator
from pathlib import Path

import httpx
import pytest

from openblade.config import parse_drive_serial_map
from openblade.domain.errors import DriveCorrelationError
from openblade.domain.policies import RealHardwareGuard
from openblade.hardware.correlation import DriveCorrelation, correlate_drives
from openblade.hardware.scalar_http import ScalarHttpLibraryBackend, ScalarHttpSession
from tests.unit.test_drive_correlation import FakeRunner

REPO_ROOT = Path(__file__).resolve().parents[2]
_STARTUP_TIMEOUT_SECONDS = 60


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _wait_for_health(base_url: str, process: subprocess.Popen[bytes] | None) -> bool:
    deadline = time.monotonic() + _STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=2) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    return False


@pytest.fixture(scope="module")
def emulator_url(tmp_path_factory: pytest.TempPathFactory) -> Generator[str, None, None]:
    external = os.environ.get("I3_AML_URL", "").strip().rstrip("/")
    if external:
        if not _wait_for_health(external, None):
            pytest.skip(f"I3_AML_URL={external} is set but not healthy")
        yield external
        return

    db_path = tmp_path_factory.mktemp("i3-emulator") / "emulator.db"
    port = _free_port()
    env = {
        **os.environ,
        "OPENBLADE_BACKEND": "mock",
        "OPENBLADE_DB_URL": f"sqlite:///{db_path}",
        "PYTHONPATH": str(REPO_ROOT),
    }
    process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        [
            sys.executable,
            "-m",
            "uvicorn",
            "openblade.api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        if not _wait_for_health(base_url, process):
            pytest.skip("the i3 emulator did not become healthy in time")
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover - shutdown safety net
            process.kill()


@pytest.fixture
def session(emulator_url: str) -> Generator[ScalarHttpSession, None, None]:
    user = os.environ.get("I3_AML_USER", "admin")
    password = os.environ.get("I3_AML_PASSWORD", "password")
    with httpx.Client(base_url=emulator_url, timeout=30.0) as client:
        yield ScalarHttpSession(client, username=user, password=password)


@pytest.fixture
def guard() -> RealHardwareGuard:
    return RealHardwareGuard(
        config_backend="real",
        config_real_hardware_enabled=True,
        operator_acknowledgment="test",
    )


def _correlation_for(
    serials: list[str], guard: RealHardwareGuard, elements: list[int]
) -> DriveCorrelation:
    """Declare ``serials[i]`` as library drive element ``elements[i]`` on ``/dev/nst{i}``.

    ``elements`` is passed in rather than assumed to be ``0..n-1``: the element ids
    are the LIBRARY's element addresses, and a test that invents them proves
    nothing about whether the mapping works against the live library.
    """
    device_list = [f"/dev/nst{index}" for index in range(len(serials))]
    live = dict(zip(device_list, serials, strict=True))
    declared = ",".join(
        f"{serial}:{element}" for serial, element in zip(serials, elements, strict=True)
    )
    return correlate_drives(
        devices=device_list,
        serial_map=parse_drive_serial_map(declared),
        runner=FakeRunner(live),
        guard=guard,
    )


def _live_drive_elements(backend: ScalarHttpLibraryBackend) -> list[int]:
    return [drive.drive_id for drive in backend.inventory().drives]


def test_emulator_publishes_drive_serials(session: ScalarHttpSession) -> None:
    backend = ScalarHttpLibraryBackend(session, library_id="emulator-i3")

    serials = backend.library_drive_serials()

    assert serials, "GET /aml/drives must publish serialNumber for every drive"
    assert all(isinstance(serial, str) and serial.strip() for serial in serials)


def test_elements_endpoint_does_not_publish_serials(session: ScalarHttpSession) -> None:
    # The premise of the whole design: the endpoint that carries drive ELEMENT
    # ADDRESSES carries no serial, so the join cannot be derived from the library.
    # If this ever starts failing, drive_device can stop requiring an operator map.
    body = session.get_json("/aml/physicalLibrary/elements")
    drive_elements = [
        element
        for element in body["elementList"]["element"]
        if isinstance(element, dict) and element.get("type") == "drive"
    ]

    assert drive_elements, "expected the emulator to report drive elements"
    assert all("serialNumber" not in element for element in drive_elements)
    assert all("address" in element for element in drive_elements)


def test_drive_device_resolves_against_the_live_librarys_own_element_addresses(
    session: ScalarHttpSession, guard: RealHardwareGuard
) -> None:
    backend = ScalarHttpLibraryBackend(session, library_id="emulator-i3")
    serials = backend.library_drive_serials()
    assert serials is not None
    # The element ids come FROM the library, not from range(len(serials)) — that
    # is the whole point, and asserting against an invented range would pass even
    # against a library that numbers its drive elements 256, 257, ...
    elements = _live_drive_elements(backend)
    assert len(elements) == len(serials)
    correlation = _correlation_for(serials, guard, elements)

    resolved = ScalarHttpLibraryBackend(
        session, library_id="emulator-i3", correlation_factory=lambda: correlation
    )

    for index, element in enumerate(elements):
        assert resolved.drive_device(element) == f"/dev/nst{index}"


def test_a_map_in_the_wrong_element_number_space_is_refused(
    session: ScalarHttpSession, guard: RealHardwareGuard
) -> None:
    """The wrong-drive bug: a map whose element ids are merely shifted.

    Every serial check passes — the declared serials ARE the attached drives and
    ARE the ones the library reports — so without the element-address check this
    returns a neighbouring drive's device and LTFS is written to the wrong
    cartridge. It must refuse instead.
    """
    backend = ScalarHttpLibraryBackend(session, library_id="emulator-i3")
    serials = backend.library_drive_serials()
    assert serials is not None
    shifted = [element + 1 for element in _live_drive_elements(backend)]
    correlation = _correlation_for(serials, guard, shifted)

    refusing = ScalarHttpLibraryBackend(
        session, library_id="emulator-i3", correlation_factory=lambda: correlation
    )

    with pytest.raises(DriveCorrelationError) as excinfo:
        refusing.drive_device(shifted[0])

    assert "element address" in str(excinfo.value).lower()


def test_a_map_from_another_library_is_refused(
    session: ScalarHttpSession, guard: RealHardwareGuard
) -> None:
    backend = ScalarHttpLibraryBackend(session, library_id="emulator-i3")
    serials = backend.library_drive_serials()
    assert serials is not None and len(serials) >= 2, "need >=2 drives for a partial mismatch"
    elements = _live_drive_elements(backend)

    # Keep one real serial (so the two sides demonstrably spell serials the same
    # way) and swap the rest for a drive this library has never seen.
    foreign = [serials[0], *(f"NOT-THIS-LIBRARY-{index}" for index in range(1, len(serials)))]
    correlation = _correlation_for(foreign, guard, elements)
    refusing = ScalarHttpLibraryBackend(
        session, library_id="emulator-i3", correlation_factory=lambda: correlation
    )

    with pytest.raises(DriveCorrelationError) as excinfo:
        refusing.drive_device(elements[0])

    assert "not-this-library-1" in str(excinfo.value).lower()


def test_no_declared_map_refuses_instead_of_guessing(session: ScalarHttpSession) -> None:
    backend = ScalarHttpLibraryBackend(session, library_id="emulator-i3")

    with pytest.raises(DriveCorrelationError) as excinfo:
        backend.drive_device(0)

    assert "OPENBLADE_DRIVE_SERIAL_MAP" in str(excinfo.value)
