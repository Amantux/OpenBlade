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
from openblade.hardware import sg, tapealert
from openblade.hardware.runner import CommandResult, SafeRunner


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


def test_it_issues_only_the_two_read_commands(
    client: TestClient, hardware_enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 405s above are free from FastAPI; this asserts the actual SCSI traffic.

    Captured at the SafeRunner boundary with dry_run OFF, so every argv the route
    would really execute is recorded. A LOG SELECT, a rewind, an `mt` command or a
    format would all show up here.
    """
    issued: list[list[str]] = []

    def record(self: SafeRunner, args: list[str], **kwargs: object) -> CommandResult:
        issued.append(list(args))
        sample = (
            sg.SAMPLE_SG_INQ
            if args[0] == "sg_inq"
            else tapealert.SAMPLE_SG_LOGS_TAPEALERT_CLEAN
            if args[0] == "sg_logs"
            else ""
        )
        return CommandResult(
            args=list(args), returncode=0, stdout=sample, stderr="", elapsed_seconds=0.0
        )

    context = get_context()
    reset_context(replace(context, config=replace(context.config, hardware_dry_run=False)))
    monkeypatch.setattr(SafeRunner, "run", record)

    response = client.get("/hardware/drive-health", params={"device": "/dev/sg1"})
    assert response.status_code == 200, response.text
    assert issued == [["sg_inq", "/dev/sg1"], ["sg_logs", "-p", "0x2e", "/dev/sg1"]]


def test_a_tool_failure_answers_502_without_the_argv_or_stderr(
    client: TestClient, hardware_enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(self: SafeRunner, args: list[str], **kwargs: object) -> CommandResult:
        return CommandResult(
            args=list(args),
            returncode=2,
            stdout="",
            stderr="sg_inq: error opening file: /dev/sg9 (dsn=secret-token)",
            elapsed_seconds=0.0,
        )

    context = get_context()
    reset_context(replace(context, config=replace(context.config, hardware_dry_run=False)))
    monkeypatch.setattr(SafeRunner, "run", explode)

    response = client.get("/hardware/drive-health", params={"device": "/dev/sg9"})
    # CommandError is not an OpenBladeError, so without the route's own handler
    # this would escape as a bare 500 carrying the full argv and the tool's stderr.
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "secret-token" not in detail
    assert "sg_inq:" not in detail
    assert "sg3_utils" in detail


def test_the_tapealert_reason_never_carries_the_tools_stderr(
    client: TestClient, hardware_enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Curation happens AT THE SOURCE now: read_tape_alerts puts tool text in
    # reason_detail (log/CLI only) and keeps reason curated. The route must
    # serialize reason verbatim and reason_detail never.
    monkeypatch.setattr(
        "openblade.api.routes_drive_health.read_tape_alerts",
        lambda device, runner, guard: tapealert.TapeAlertReport(
            device=device,
            supported=False,
            flags=(),
            reason="no TapeAlert page in sg_logs output (sg_logs exited 5; see server logs)",
            reason_detail="log_sense: field in cdb illegal, /dev/sg1 dsn=secret",
        ),
    )

    response = client.get("/hardware/drive-health", params={"device": "/dev/sg1"})
    assert response.status_code == 200
    body = response.text
    assert "secret" not in body
    assert "field in cdb illegal" not in body
    reason = response.json()["drives"][0]["tapeAlertReason"]
    assert "sg_logs exited 5" in reason


def test_paren_heavy_tool_stderr_never_reaches_the_wire(
    client: TestClient, hardware_enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reviewer-repro'd leak: the old regex scrub over the COMBINED reason
    string stopped at the first ')' inside real sg3_utils stderr, so
    'sense_key=0x5 (Invalid field in cdb) opening /dev/sg9 dsn=secret)'
    leaked a device path and a DSN in a 200. Curation now happens at the
    source: reason never contains tool text; reason_detail stays server-side.
    This exercises read_tape_alerts itself against the hostile stderr."""
    hostile = "sense_key=0x5 (Invalid field in cdb) opening /dev/sg9 dsn=secret-token)"

    from openblade.hardware.sg import SAMPLE_SG_INQ_MODERN

    def hostile_run(
        self: SafeRunner,
        args: list[str],
        timeout: int | None = None,
        redact_args: list[int] | None = None,
    ) -> CommandResult:
        if args[0] == "sg_logs":
            return CommandResult(
                args=args, returncode=5, stdout="", stderr=hostile, elapsed_seconds=0.0
            )
        return CommandResult(
            args=args, returncode=0, stdout=SAMPLE_SG_INQ_MODERN, stderr="", elapsed_seconds=0.0
        )

    # The hardware_enabled fixture keeps hardware_dry_run=True, and dry-run
    # short-circuits BEFORE runner.run — so patching run alone exercises
    # nothing. Hand the route a non-dry runner whose run IS the hostile stub.
    class HostileRunner(SafeRunner):
        def __init__(self, dry_run: bool = False) -> None:
            super().__init__(dry_run=False)

    monkeypatch.setattr(HostileRunner, "run", hostile_run)
    monkeypatch.setattr("openblade.api.routes_drive_health.SafeRunner", HostileRunner)
    response = client.get("/hardware/drive-health", params={"device": "/dev/sg1"})
    assert response.status_code == 200
    body = response.text
    assert "dsn=secret-token" not in body
    assert "sense_key" not in body
    reason = response.json()["drives"][0]["tapeAlertReason"]
    assert "see server logs" in reason
    assert "sg_logs exited 5" in reason
