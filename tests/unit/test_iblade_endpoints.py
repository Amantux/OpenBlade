from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from openblade.api import aml_state
from openblade.api.main import app
from openblade.bootstrap import get_context


@pytest.fixture()
def client() -> TestClient:
    aml_state.ensure_initialized(get_context().config.db_url, force_reset=True)
    with TestClient(app) as test_client:
        yield test_client



def _login(client: TestClient) -> None:
    response = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert response.status_code == 200


@pytest.mark.parametrize(
    ("path", "expected_keys", "requires_auth"),
    [
        ("/iblade/states", {"code", "description"}, False),
        ("/iblade/states/READY", {"code", "description"}, False),
        ("/iblade/volstates", {"code", "description"}, False),
        ("/iblade/volstates/SCRATCH", {"code", "description"}, False),
        ("/iblade/vgstates", {"code", "description"}, False),
        ("/iblade/vgstates/READY", {"code", "description"}, False),
        ("/iblade/jobstates", {"code", "description"}, False),
        ("/iblade/opstates", {"code", "description"}, False),
        ("/iblade/opstates/queued", {"code", "description"}, False),
        ("/iblade/reasons", {"code", "description"}, False),
        ("/iblade/reasons/NONE", {"code", "description"}, False),
        ("/iblade/vgreasons", {"code", "description"}, False),
        ("/iblade/vgreasons/NONE", {"code", "description"}, False),
        ("/iblade/product", {"product", "model", "serial", "firmware", "software", "vendor", "build"}, False),
        ("/iblade/product/model", {"element", "value"}, False),
        ("/iblade/messages", {"id", "code", "severity", "summary", "description", "action", "created_at", "acknowledged"}, True),
        ("/iblade/messages/MSG-001", {"id", "code", "severity", "summary", "description", "action", "created_at", "acknowledged"}, True),
        ("/iblade/nas-drives", {"serialNumber", "model", "status", "state"}, True),
        ("/iblade/lto-media", {"barcode", "type", "state"}, True),
        ("/iblade/lto_media/VOL001L9", {"barcode", "type", "state"}, True),
        ("/iblade/hosts", {"id", "hostname", "ip", "wwn", "connection_type", "state"}, True),
        ("/iblade/network", {"hostname", "management_ip", "subnet_mask", "gateway", "dns", "mtu", "vlan", "bondMode"}, True),
        ("/iblade/reports/configuration", {"generated_at", "items", "summary"}, True),
        ("/iblade/reports/media", {"generated_at", "items", "summary"}, True),
        ("/iblade/reports/media-count", {"generated_at", "items", "summary"}, True),
        ("/iblade/reports/volume-groups", {"generated_at", "items", "summary"}, True),
        ("/iblade/status/io", {"activeTransfers", "queueDepth", "throughputMBps", "activeDrives"}, True),
        ("/iblade/status/open-messages", {"id", "code", "severity", "summary", "description", "action", "created_at", "acknowledged"}, True),
        ("/iblade/status/system/open-messages", {"id", "code", "severity", "summary", "description", "action", "created_at", "acknowledged"}, True),
        ("/iblade/system/settings", {"autoDiscovery", "defaultVolumeGroup", "exportPolicy", "ioThrottle", "retentionLock", "serviceMode", "snapshotRetention"}, True),
        ("/iblade/system/settings/autoDiscovery", {"name", "value"}, True),
        ("/iblade/system/extended-snapshot", {"generated_at", "items", "summary"}, True),
        ("/iblade/volume-groups", {"index", "name", "state", "reason", "mediaCount", "policy", "tapes"}, True),
        ("/iblade/volume-groups/1", {"index", "name", "state", "reason", "mediaCount", "policy", "tapes"}, True),
    ],
)
def test_iblade_get_endpoints_return_expected_schema(
    client: TestClient,
    path: str,
    expected_keys: set[str],
    requires_auth: bool,
) -> None:
    if requires_auth:
        _login(client)
    response = client.get(path)
    assert response.status_code == 200
    payload = response.json()
    if isinstance(payload, list):
        assert payload
        assert expected_keys.issubset(payload[0].keys())
    else:
        assert expected_keys.issubset(payload.keys())


@pytest.mark.parametrize(
    "path",
    [
        "/iblade/system/snapshot",
        "/iblade/system/save-configuration",
        "/iblade/system/restore-configuration",
        "/iblade/system/fwupgrade",
        "/iblade/system/reboot",
        "/iblade/system/clear-to-ship",
        "/iblade/system/factory-defaults",
        "/iblade/reports/configuration/email",
        "/iblade/reports/media/email",
        "/iblade/reports/media-count/email",
        "/iblade/reports/volume-groups/email",
        "/iblade/operations/assignment",
        "/iblade/operations/merge",
        "/iblade/operations/prepare-export",
        "/iblade/operations/volume-groups/assign",
        "/iblade/operations/volume-groups/merge",
        "/iblade/operations/volume-groups/prepare-export",
        "/iblade/operations/volume-groups/repair",
        "/iblade/operations/replicate",
        "/iblade/operations/volume-groups/replicate",
        "/iblade/operations/safe-repair",
        "/iblade/operations/volume-groups/safe-repair",
    ],
)
def test_iblade_post_operation_endpoints_queue_jobs(client: TestClient, path: str) -> None:
    _login(client)
    response = client.post(path, json={})
    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["job_id"]
    assert payload["message"]


def test_iblade_volume_groups_create_and_delete(client: TestClient) -> None:
    _login(client)
    create_response = client.post(
        "/iblade/volume_groups",
        json={"name": "qa-vg", "state": "READY", "reason": "NONE", "policy": "balanced", "tapes": ["VOL001L9"]},
    )
    assert create_response.status_code == 201
    created = create_response.json()
    assert created["name"] == "qa-vg"

    delete_response = client.delete(f"/iblade/volume-groups/{created['index']}")
    assert delete_response.status_code == 200
    deleted = delete_response.json()
    assert deleted["index"] == created["index"]


@pytest.mark.parametrize(
    "path",
    [
        "/iblade/states",
        "/iblade/volstates",
        "/iblade/vgstates",
        "/iblade/jobstates",
        "/iblade/reasons",
        "/iblade/vgreasons",
    ],
)
def test_iblade_enumerations_are_non_empty(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, list)
    assert payload


@pytest.mark.parametrize("path", ["/iblade/product/unknown", "/iblade/messages/DOES-NOT-EXIST"])
def test_iblade_errors_use_ws_result_code_format(client: TestClient, path: str) -> None:
    if path.startswith("/iblade/messages"):
        _login(client)
    response = client.get(path)
    assert response.status_code == 404
    payload = response.json()
    assert set(payload) == {"code", "summary", "description", "action", "customCode"}
    assert payload["code"] == "AML_NOT_FOUND"
