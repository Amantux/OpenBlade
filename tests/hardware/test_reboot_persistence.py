"""Reboot persistence on the real appliance (destructive lane, environment-approved).

Reboots the appliance through the documented AML endpoint ``POST /aml/system/reboot``,
waits for its AML API to go down and come back, and asserts the library inventory,
drive state and OpenBlade's live drive leases are identical across the reboot.

Precondition (fails loudly otherwise): the library is quiescent — no live leases,
no drive loaded, nothing mounted. A reboot with a mounted drive is disruptive, and
"leases identical" would be vacuous with leases in flight.

The test does NOT call ``recover_after_restart`` itself: it has no report-only mode
(it fails jobs and releases leases), so the lane's existing "Recovery report must be
clean" step is the recovery assertion.

A reboot does not touch media, but it interrupts every client of the appliance, so
the test additionally requires ``OPENBLADE_REBOOT_TESTS=enabled`` (mirroring
``OPENBLADE_FAULT_TESTS``): ``pytest tests/hardware -m real_hardware`` alone never
reboots anything. A hard power-cycle stays a manual step — see
docs/runbooks/hardware-lanes.md "Reboot persistence".
"""

from __future__ import annotations

import http.client
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

import pytest

from openblade.bootstrap import create_context
from openblade.config import load_config
from tests.hardware.readonly.conftest import (
    ApplianceClient,
    ApplianceResponse,
    ReadOnlyViolation,
)
from tools.hardware.snapshot import capture_snapshot, diff_snapshots

REBOOT_PATH = "/aml/system/reboot"
# Bounded waits (the workflow step's timeout-minutes covers their sum). The
# appliance must stop answering within DOWN_TIMEOUT_S of accepting the reboot...
DOWN_TIMEOUT_S = 300.0
# ...and accept a login again within UP_TIMEOUT_S after it went down.
UP_TIMEOUT_S = 600.0
# Short enough that a brief downtime window is not missed between polls.
POLL_INTERVAL_S = 2.0
# Errors after the POST that mean "connection dropped mid-response", i.e. the
# appliance accepted the reboot and went down before answering.
_DROPPED = (urllib.error.URLError, OSError, http.client.HTTPException)


class RebootClient(ApplianceClient):
    """ApplianceClient plus the single non-read-only call this test needs.

    ``request`` keeps the read-only guard unchanged; only ``reboot`` bypasses
    it, and only for exactly ``POST /aml/system/reboot`` (hard-coded below).
    """

    def reboot(self) -> ApplianceResponse:
        """POST the reboot; a dropped connection counts as accepted (status 200)."""
        req = urllib.request.Request(  # noqa: S310 - base URL is operator-set rig config, scheme checked in fixture
            self.base_url.rstrip("/") + REBOOT_PATH,
            data=json.dumps({"delay": 0, "force": False}).encode(),
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with self._opener.open(req, timeout=30) as resp:
                return ApplianceResponse(status=resp.status, body=None)
        except urllib.error.HTTPError as exc:
            return ApplianceResponse(status=exc.code, body=None)
        except _DROPPED:
            return ApplianceResponse(status=200, body=None)

    def login_ok(self) -> bool:
        """True iff a login succeeds now; an unreachable appliance is False."""
        try:
            return self.login().status == 200
        except _DROPPED:
            return False


@pytest.fixture
def reboot_tests_enabled() -> None:
    if os.environ.get("OPENBLADE_REBOOT_TESTS") != "enabled":
        pytest.skip("OPENBLADE_REBOOT_TESTS must be set to 'enabled'")


@pytest.fixture
def reboot_client(real_hardware_guard, reboot_tests_enabled) -> RebootClient:
    del real_hardware_guard, reboot_tests_enabled
    url = os.environ.get("OPENBLADE_APPLIANCE_URL", "")
    user = os.environ.get("OPENBLADE_APPLIANCE_USER", "")
    password = os.environ.get("OPENBLADE_APPLIANCE_PASSWORD", "")
    if not (url and user and password):
        pytest.skip(
            "Appliance AML API not configured: set OPENBLADE_APPLIANCE_URL, "
            "OPENBLADE_APPLIANCE_USER and OPENBLADE_APPLIANCE_PASSWORD"
        )
    if not url.startswith(("https://", "http://")):
        pytest.fail(f"OPENBLADE_APPLIANCE_URL must be http(s)://, got scheme of {url[:8]!r}")
    insecure = os.environ.get("OPENBLADE_APPLIANCE_INSECURE") == "true"
    return RebootClient(base_url=url, user=user, password=password, insecure=insecure)


def _wait_for(predicate: Any, timeout_s: float, what: str) -> None:
    deadline = time.monotonic() + timeout_s
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail(f"appliance {what} within {timeout_s:.0f}s")
        time.sleep(POLL_INTERVAL_S)


def _state() -> dict[str, Any]:
    context = create_context(load_config())
    leases = sorted(
        (lease.id, lease.drive_id, lease.job_id, lease.barcode, lease.physical_drive_id)
        for lease in context.catalog.live_leases()
    )
    return {"snapshot": capture_snapshot(context.library), "leases": leases}


def _assert_quiescent(state: dict[str, Any]) -> None:
    assert state["leases"] == [], f"refusing to reboot with live leases: {state['leases']}"
    busy = [
        d
        for d in state["snapshot"]["drives"]
        if d["drive_state"] != "empty" or d["mount_state"] != "unmounted" or d["barcode"]
    ]
    assert not busy, f"refusing to reboot with a loaded or mounted drive: {busy}"


def test_reboot_client_request_keeps_read_only_guard() -> None:
    """No hardware: the bypass is ``reboot()`` only; ``request`` refuses writes."""
    client = RebootClient(base_url="https://appliance.invalid", user="u", password="p")
    with pytest.raises(ReadOnlyViolation):
        client.request("POST", "/aml/media/move")
    with pytest.raises(ReadOnlyViolation):
        client.request("POST", REBOOT_PATH)


@pytest.mark.real_hardware
def test_reboot_preserves_inventory_and_leases(reboot_client: RebootClient) -> None:
    before = _state()
    _assert_quiescent(before)

    assert reboot_client.login().status == 200, "appliance login failed before reboot"
    resp = reboot_client.reboot()
    assert 200 <= resp.status < 300, f"POST {REBOOT_PATH} rejected: HTTP {resp.status}"

    # Seeing the API go down first is what makes the later login prove a reboot
    # happened, rather than racing the scheduled reboot and passing vacuously.
    _wait_for(lambda: not reboot_client.login_ok(), DOWN_TIMEOUT_S, "never went down")
    _wait_for(reboot_client.login_ok, UP_TIMEOUT_S, "did not accept a login again")
    reboot_client.logout()

    after = _state()
    diff = diff_snapshots(before["snapshot"], after["snapshot"])
    assert not diff["changed"], f"inventory/drive state changed across reboot: {diff}"
    assert after["leases"] == before["leases"], "live drive leases changed across reboot"
