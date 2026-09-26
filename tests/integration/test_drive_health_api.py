"""``GET /hardware/drive-health`` -- the HTTP half of ``openblade hardware drive-health``.

Read-only and guarded. Two properties matter more than the payload shape:

* with the simulator backend (the default) the route must refuse rather than go
  looking for a device, and
* a drive without the TapeAlert log page must come back as a 200 saying so, not
  as an error -- that is a property of the drive.

The "real hardware" cases run with ``hardware_dry_run=True``, so the guard is
satisfied and ``SafeRunner`` returns the sample ``sg_inq``/``sg_logs`` output
captured from a real rig instead of executing anything.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, get_context, reset_context
from openblade.config import BackendMode, OpenBladeConfig
from openblade.hardware import tapealert


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'health.db'}"))
    reset_context(context)
    return TestClient(app)


@pytest.fixture()
def hardware_enabled() -> None:
    """Claim real hardware while keeping the simulator backends.

    The route only consults ``context.config``; swapping the config alone
    exercises the guarded path without building real backends in a test process.
    """
    context = get_context()
    reset_context(
        replace(
            context,
            config=replace(
                context.config,
                backend=BackendMode.REAL,
                real_hardware_enabled=True,
                hardware_dry_run=True,
            ),
        )
    )


def test_simulator_backend_refuses_with_the_curated_reason(client: TestClient) -> None:
    response = client.get("/hardware/drive-health")
    assert response.status_code == 503
    detail = response.json()["detail"]
    # The refusal names both variables, because "unavailable" alone is unactionable.
    assert "OPENBLADE_BACKEND=real" in detail
    assert "OPENBLADE_REAL_HARDWARE_ENABLED=true" in detail


def test_one_named_device_reports_inquiry_and_tapealert(
    client: TestClient, hardware_enabled: None
) -> None:
    response = client.get("/hardware/drive-health", params={"device": "/dev/sg1"})
    assert response.status_code == 200
    drives = response.json()["drives"]
    assert len(drives) == 1
    drive = drives[0]
    assert drive["device"] == "/dev/sg1"
    assert drive["inquiry"]["vendor"]
    assert drive["inquiry"]["product"]
    assert drive["tapeAlertSupported"] is True
    # The clean sample sets no flags: every flag is read, none is active.
    assert drive["flagsRead"] > 0
    assert drive["activeFlags"] == []
    assert drive["worstSeverity"] is None
    assert drive["tapeAlertReason"] is None


def test_every_discovered_drive_is_reported_when_no_device_is_named(
    client: TestClient, hardware_enabled: None
) -> None:
    response = client.get("/hardware/drive-health")
    assert response.status_code == 200
    drives = response.json()["drives"]
    assert len(drives) >= 1
    assert len({drive["device"] for drive in drives}) == len(drives)


def test_active_flags_carry_their_severity(
    client: TestClient, hardware_enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = tapealert.parse_sg_logs_tapealert(
        tapealert.SAMPLE_SG_LOGS_TAPEALERT_CLEAN, device="/dev/sg1"
    )
    flags = tuple(
        replace(flag, value=flag.number in {4, 3}) if flag.number in {4, 3} else flag
        for flag in report.flags
    )
    monkeypatch.setattr(
        "openblade.api.routes_drive_health.read_tape_alerts",
        lambda device, runner, guard: replace(report, flags=flags),
    )

    response = client.get("/hardware/drive-health", params={"device": "/dev/sg1"})
    assert response.status_code == 200
    drive = response.json()["drives"][0]
    severities = {flag["number"]: flag["severity"] for flag in drive["activeFlags"]}
    assert severities == {4: "critical", 3: "warning"}
    # Worst-first, so a UI badge cannot report a critical drive as merely warned.
    assert drive["worstSeverity"] == "critical"


def test_a_drive_without_the_log_page_is_a_200_with_a_reason(
    client: TestClient, hardware_enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "openblade.api.routes_drive_health.read_tape_alerts",
        lambda device, runner, guard: tapealert.TapeAlertReport(
            device=device,
            supported=False,
            flags=(),
            reason="no TapeAlert page in sg_logs output",
        ),
    )

    response = client.get("/hardware/drive-health", params={"device": "/dev/sg1"})
    assert response.status_code == 200
    drive = response.json()["drives"][0]
    assert drive["tapeAlertSupported"] is False
    assert drive["tapeAlertReason"] == "no TapeAlert page in sg_logs output"
    assert drive["activeFlags"] == []


def test_the_route_is_read_only(client: TestClient, hardware_enabled: None) -> None:
    # Not a formality: drive health is the one surface that talks SCSI to a drive
    # a human is worried about, and a write here would be the worst possible bug.
    for method in (client.post, client.put, client.delete, client.patch):
        assert method("/hardware/drive-health").status_code == 405
