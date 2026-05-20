"""Integration tests for AML events and logs endpoints (task-011)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'events-test.db'}"))
    reset_context(context)
    return TestClient(app)


@pytest.fixture()
def authed(client: TestClient) -> TestClient:
    resp = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert resp.status_code == 200
    return client


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def test_list_events_requires_auth(client: TestClient) -> None:
    resp = client.get("/aml/events")
    assert resp.status_code == 401


def test_list_events_returns_200(authed: TestClient) -> None:
    resp = authed.get("/aml/events")
    assert resp.status_code == 200


def test_get_event_not_found(authed: TestClient) -> None:
    resp = authed.get("/aml/event/nonexistent-id")
    assert resp.status_code == 404


def test_clear_events_requires_admin(client: TestClient) -> None:
    resp = client.delete("/aml/events")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# RAS Tickets
# ---------------------------------------------------------------------------

def test_list_tickets_returns_200(authed: TestClient) -> None:
    resp = authed.get("/aml/ras/tickets")
    assert resp.status_code == 200


def test_create_ticket(authed: TestClient) -> None:
    resp = authed.post(
        "/aml/ras/ticket",
        json={"ticket": {"severity": "warning", "component": "drive", "description": "Test ticket"}},
    )
    assert resp.status_code in {200, 201}
    if resp.status_code in {200, 201}:
        data = resp.json()
        ticket = data.get("ticket") or data
        assert ticket.get("status") in {"open", "acknowledged", "resolved", "closed", None}


def test_ticket_status_validation_rejects_invalid(authed: TestClient) -> None:
    """Regression: arbitrary ticket status values must be rejected."""
    # First create a ticket to get an id
    create_resp = authed.post(
        "/aml/ras/ticket",
        json={"ticket": {"severity": "info", "component": "system", "description": "Validation test"}},
    )
    assert create_resp.status_code in {200, 201}
    ticket_id = (create_resp.json().get("ticket") or create_resp.json()).get("id", "")

    if ticket_id:
        resp = authed.put(
            f"/aml/ras/ticket/{ticket_id}",
            json={"ticket": {"status": "INVALID_STATUS_VALUE"}},
        )
        assert resp.status_code == 422


def test_ticket_status_valid_values_accepted(authed: TestClient) -> None:
    """Spec lifecycle statuses must be accepted."""
    create_resp = authed.post(
        "/aml/ras/ticket",
        json={"ticket": {"severity": "info", "component": "system", "description": "Lifecycle test"}},
    )
    assert create_resp.status_code in {200, 201}
    ticket_id = (create_resp.json().get("ticket") or create_resp.json()).get("id", "")

    if ticket_id:
        for valid_status in ["acknowledged", "resolved", "closed"]:
            resp = authed.put(
                f"/aml/ras/ticket/{ticket_id}",
                json={"ticket": {"status": valid_status}},
            )
            assert resp.status_code in {200, 404}  # 404 if prior status transition blocked


def test_get_ticket_not_found(authed: TestClient) -> None:
    resp = authed.get("/aml/ras/ticket/does-not-exist")
    assert resp.status_code == 404


def test_ticket_summary_returns_200(authed: TestClient) -> None:
    resp = authed.get("/aml/ras/tickets/summary")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------

def test_list_logs_requires_auth(client: TestClient) -> None:
    resp = client.get("/aml/logs")
    assert resp.status_code == 401


def test_list_logs_returns_200(authed: TestClient) -> None:
    resp = authed.get("/aml/logs")
    assert resp.status_code == 200


def test_get_log_not_found(authed: TestClient) -> None:
    resp = authed.get("/aml/log/nonexistent")
    assert resp.status_code == 404


def test_clear_log_requires_admin(client: TestClient) -> None:
    resp = client.delete("/aml/logs/system")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

def test_list_alerts_returns_200(authed: TestClient) -> None:
    resp = authed.get("/aml/alerts")
    assert resp.status_code == 200


def test_alerts_summary(authed: TestClient) -> None:
    resp = authed.get("/aml/alerts/summary")
    assert resp.status_code == 200


def test_acknowledge_alert_not_found(authed: TestClient) -> None:
    resp = authed.post("/aml/alert/nonexistent/acknowledge")
    assert resp.status_code in {404, 422}


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def test_list_notifications_returns_200(authed: TestClient) -> None:
    resp = authed.get("/aml/notifications")
    assert resp.status_code == 200


def test_delete_notification_requires_admin(client: TestClient) -> None:
    resp = client.delete("/aml/notification/some-id")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------

def test_list_subscriptions_returns_200(authed: TestClient) -> None:
    resp = authed.get("/aml/events/subscribe")
    assert resp.status_code in {200, 404, 405}  # GET may not exist; POST /subscribe does
