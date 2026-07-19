from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree

import pytest
from fastapi.testclient import TestClient

from openblade.api import aml_state
from openblade.api.main import app
from openblade.bootstrap import create_context, get_context, reset_context
from openblade.config import IBladeCompatibilityMode, OpenBladeConfig


@pytest.fixture()
def client() -> TestClient:
    aml_state.ensure_initialized(get_context().config.db_url, force_reset=True)
    with TestClient(app) as test_client:
        yield test_client


def _login(client: TestClient) -> None:
    response = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert response.status_code == 200


def _strict_iblade_path(path: str, *, blade_type: str, section_number: int = 1) -> str:
    suffix = path.removeprefix("/iblade")
    normalized_suffix = suffix if suffix.startswith("/") else f"/{suffix}"
    return f"/aml/devices/blade/{blade_type}/{section_number}{normalized_suffix}"


def _normalize_parity_payload(payload: object) -> object:
    if isinstance(payload, dict):
        return {
            key: _normalize_parity_payload(value)
            for key, value in payload.items()
            if key not in {"generated_at"}
        }
    if isinstance(payload, list):
        return [_normalize_parity_payload(item) for item in payload]
    return payload


def _client_with_mode(tmp_path: Path, mode: IBladeCompatibilityMode) -> TestClient:
    context = create_context(
        OpenBladeConfig(
            db_url=f"sqlite:///{tmp_path / f'iblade-{mode.value}.db'}",
            iblade_compat_mode=mode,
        )
    )
    reset_context(context)
    return TestClient(app)


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
        ("/iblade/jobstates/queued", {"code", "description"}, False),
        ("/iblade/opstates", {"code", "description"}, False),
        ("/iblade/opstates/queued", {"code", "description"}, False),
        ("/iblade/reasons", {"code", "description"}, False),
        ("/iblade/reasons/NONE", {"code", "description"}, False),
        ("/iblade/vgreasons", {"code", "description"}, False),
        ("/iblade/vgreasons/NONE", {"code", "description"}, False),
        (
            "/iblade/product",
            {"product", "model", "serial", "firmware", "software", "vendor", "build"},
            False,
        ),
        ("/iblade/product/model", {"element", "value"}, False),
        (
            "/iblade/messages",
            {
                "id",
                "code",
                "severity",
                "summary",
                "description",
                "action",
                "created_at",
                "acknowledged",
            },
            True,
        ),
        (
            "/iblade/messages/MSG-001",
            {
                "id",
                "code",
                "severity",
                "summary",
                "description",
                "action",
                "created_at",
                "acknowledged",
            },
            True,
        ),
        ("/iblade/nas-drives", {"serialNumber", "model", "status", "state"}, True),
        ("/iblade/lto-media", {"barcode", "type", "state"}, True),
        ("/iblade/lto_media/VOL001L9", {"barcode", "type", "state"}, True),
        ("/iblade/hosts", {"id", "hostname", "ip", "wwn", "connection_type", "state"}, True),
        (
            "/iblade/network",
            {
                "hostname",
                "management_ip",
                "subnet_mask",
                "gateway",
                "dns",
                "mtu",
                "vlan",
                "bondMode",
            },
            True,
        ),
        ("/iblade/reports/configuration", {"generated_at", "items", "summary"}, True),
        ("/iblade/reports/media", {"generated_at", "items", "summary"}, True),
        ("/iblade/reports/media-count", {"generated_at", "items", "summary"}, True),
        ("/iblade/reports/volume-groups", {"generated_at", "items", "summary"}, True),
        (
            "/iblade/status/io",
            {"activeTransfers", "queueDepth", "throughputMBps", "activeDrives"},
            True,
        ),
        (
            "/iblade/status/open-messages",
            {
                "id",
                "code",
                "severity",
                "summary",
                "description",
                "action",
                "created_at",
                "acknowledged",
            },
            True,
        ),
        (
            "/iblade/status/system/open-messages",
            {
                "id",
                "code",
                "severity",
                "summary",
                "description",
                "action",
                "created_at",
                "acknowledged",
            },
            True,
        ),
        (
            "/iblade/system/settings",
            {
                "autoDiscovery",
                "defaultVolumeGroup",
                "exportPolicy",
                "ioThrottle",
                "retentionLock",
                "serviceMode",
                "snapshotRetention",
            },
            True,
        ),
        ("/iblade/system/settings/autoDiscovery", {"name", "value"}, True),
        ("/iblade/system/extended-snapshot", {"generated_at", "items", "summary"}, True),
        (
            "/iblade/volume-groups",
            {"index", "name", "state", "reason", "mediaCount", "policy", "tapes"},
            True,
        ),
        (
            "/iblade/volume-groups/1",
            {"index", "name", "state", "reason", "mediaCount", "policy", "tapes"},
            True,
        ),
    ],
)
@pytest.mark.parametrize("blade_type", ["ltfs", "windows"])
def test_iblade_get_endpoints_return_expected_schema(
    client: TestClient,
    path: str,
    expected_keys: set[str],
    requires_auth: bool,
    blade_type: str,
) -> None:
    if requires_auth:
        _login(client)
    alias_response = client.get(path)
    assert alias_response.status_code == 200
    strict_response = client.get(_strict_iblade_path(path, blade_type=blade_type))
    assert strict_response.status_code == 200

    alias_payload = alias_response.json()
    payload = strict_response.json()
    assert _normalize_parity_payload(payload) == _normalize_parity_payload(alias_payload)
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
        "/iblade/operations/repair",
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
@pytest.mark.parametrize("blade_type", ["ltfs", "windows"])
def test_iblade_post_operation_endpoints_queue_jobs(
    client: TestClient, path: str, blade_type: str
) -> None:
    def _operation_payload() -> dict[str, object]:
        if path.endswith("/assignment") or path.endswith("/assign"):
            return {"index": 1}
        if path.endswith("/merge"):
            return {"source": 1, "destination": 2}
        if "prepare-export" in path:
            group = client.get("/iblade/volume-groups/1")
            assert group.status_code == 200
            for barcode in group.json()["tapes"]:
                assert client.put(f"/iblade/lto-media/{barcode}", json={"state": "sequestered"}).status_code == 200
                assert client.put(f"/iblade/lto-media/{barcode}", json={"state": "formatted"}).status_code == 200
            return {"index": 1}
        if path.endswith("/repair") or path.endswith("/safe-repair"):
            assert aml_state.update_iblade_volume_group(1, {"state": "DEGRADED", "reason": "REPAIR_REQUIRED"})
            return {"index": 1}
        if "replicate" in path:
            return {"source": 1, "destination": 2}
        return {}

    def _post_with_reset(target_path: str) -> object:
        aml_state.ensure_initialized(get_context().config.db_url, force_reset=True)
        _login(client)
        payload = _operation_payload()
        return client.post(target_path, json=payload)

    alias_response = _post_with_reset(path)
    assert alias_response.status_code == 202
    alias_payload = alias_response.json()
    assert alias_payload["status"] == "queued"
    assert alias_payload["job_id"]
    assert alias_payload["message"]

    strict_response = _post_with_reset(_strict_iblade_path(path, blade_type=blade_type))
    assert strict_response.status_code == 202
    strict_payload = strict_response.json()
    assert strict_payload["status"] == "queued"
    assert strict_payload["job_id"]
    assert strict_payload["message"]


@pytest.mark.parametrize("blade_type", ["ltfs", "windows"])
def test_strict_iblade_paths_validate_section_number(client: TestClient, blade_type: str) -> None:
    _login(client)
    invalid_section = client.get(f"/aml/devices/blade/{blade_type}/not-a-number/states")
    assert invalid_section.status_code == 422
    invalid_payload = invalid_section.json()
    assert invalid_payload["code"] == "AML_VALIDATION_ERROR"

    missing_section = client.get(f"/aml/devices/blade/{blade_type}/999/states")
    assert missing_section.status_code == 404
    missing_payload = missing_section.json()
    assert missing_payload["code"] == "AML_NOT_FOUND"


def test_strict_iblade_gateway_preserves_existing_advanced_ltfs_routes(client: TestClient) -> None:
    _login(client)
    response = client.get("/aml/devices/blade/ltfs/1/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"]["sectionNumber"] == 1


@pytest.mark.parametrize("blade_type", ["ltfs", "windows"])
def test_strict_iblade_host_crud_supports_get_post_delete(
    client: TestClient, blade_type: str
) -> None:
    _login(client)
    host_path = _strict_iblade_path("/iblade/hosts/192.168.10.77", blade_type=blade_type)
    create_response = client.post(
        host_path,
        json={
            "hostname": "backup-c",
            "wwn": "10:00:00:00:00:00:00:77",
            "connection_type": "ethernet",
            "state": "connected",
        },
    )
    assert create_response.status_code == 200
    created = create_response.json()
    assert created["ip"] == "192.168.10.77"

    get_response = client.get(host_path)
    assert get_response.status_code == 200
    fetched = get_response.json()
    assert fetched["id"] == created["id"]

    delete_response = client.delete(host_path)
    assert delete_response.status_code == 200
    deleted = delete_response.json()
    assert deleted["id"] == created["id"]


@pytest.mark.parametrize("blade_type", ["ltfs", "windows"])
def test_strict_iblade_job_update_supports_put(client: TestClient, blade_type: str) -> None:
    _login(client)
    queue_response = client.post(
        _strict_iblade_path("/iblade/system/snapshot", blade_type=blade_type), json={}
    )
    assert queue_response.status_code == 202
    job_id = queue_response.json()["job_id"]

    update_response = client.put(
        _strict_iblade_path(f"/iblade/jobs/{job_id}", blade_type=blade_type),
        json={"job_state": "cancelled"},
    )
    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["id"] == job_id
    assert updated["status"] == "cancelled"


@pytest.mark.parametrize("blade_type", ["ltfs", "windows"])
def test_strict_iblade_auth_requirements_match_alias_routes(
    client: TestClient, blade_type: str
) -> None:
    alias_response = client.get("/iblade/messages")
    strict_response = client.get(_strict_iblade_path("/iblade/messages", blade_type=blade_type))

    for response in (alias_response, strict_response):
        assert response.status_code == 401
        payload = response.json()
        assert payload["code"] == "AML_AUTH_REQUIRED"


@pytest.mark.parametrize("blade_type", ["ltfs", "windows"])
def test_strict_iblade_method_not_allowed_matches_alias(
    client: TestClient, blade_type: str
) -> None:
    alias_response = client.post("/iblade/states", json={})
    strict_response = client.post(
        _strict_iblade_path("/iblade/states", blade_type=blade_type), json={}
    )

    assert alias_response.status_code == 405
    assert strict_response.status_code == 405
    assert alias_response.json() == {"detail": "Method Not Allowed"}
    assert strict_response.json() == {"detail": "Method Not Allowed"}


@pytest.mark.parametrize("blade_type", ["ltfs", "windows"])
def test_iblade_get_routes_support_xml_accept_for_alias_and_strict_paths(
    client: TestClient, blade_type: str
) -> None:
    alias_response = client.get("/iblade/product", headers={"Accept": "application/xml"})
    strict_response = client.get(
        _strict_iblade_path("/iblade/product", blade_type=blade_type),
        headers={"Accept": "application/xml"},
    )

    for response in (alias_response, strict_response):
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/xml")
        root = ElementTree.fromstring(response.content)
        assert root.tag == "response"
        assert root.findtext("product") == "OpenBlade iBlade"


@pytest.mark.parametrize("blade_type", ["ltfs", "windows"])
def test_iblade_mutating_routes_require_json_content_type(
    client: TestClient, blade_type: str
) -> None:
    _login(client)
    alias_response = client.post(
        "/iblade/operations/assignment",
        content="{}",
        headers={"Content-Type": "text/plain"},
    )
    strict_response = client.post(
        _strict_iblade_path("/iblade/operations/assignment", blade_type=blade_type),
        content="{}",
        headers={"Content-Type": "text/plain"},
    )

    for response in (alias_response, strict_response):
        assert response.status_code == 415
        payload = response.json()
        assert payload["code"] == "AML_ERROR"


@pytest.mark.parametrize("blade_type", ["ltfs", "windows"])
def test_iblade_post_routes_use_controlled_xml_fallback(
    client: TestClient, blade_type: str
) -> None:
    _login(client)
    alias_response = client.post(
        "/iblade/system/snapshot",
        headers={"Accept": "application/xml"},
        json={},
    )
    strict_response = client.post(
        _strict_iblade_path("/iblade/system/snapshot", blade_type=blade_type),
        headers={"Accept": "application/xml"},
        json={},
    )

    for response in (alias_response, strict_response):
        assert response.status_code == 202
        assert response.headers["content-type"].startswith("application/json")
        assert (
            response.headers["X-OpenBlade-Content-Negotiation"]
            == "json-fallback; reason=xml-write-not-supported"
        )


@pytest.mark.parametrize("blade_type", ["ltfs", "windows"])
def test_iblade_nas_drives_empty_behavior_matches_alias_and_strict(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, blade_type: str
) -> None:
    _login(client)
    monkeypatch.setattr(aml_state, "list_aml_drives", lambda: [])
    alias_response = client.get("/iblade/nas-drives")
    strict_response = client.get(_strict_iblade_path("/iblade/nas-drives", blade_type=blade_type))
    assert alias_response.status_code == 200
    assert strict_response.status_code == 200
    assert alias_response.json() == []
    assert strict_response.json() == []


@pytest.mark.parametrize("blade_type", ["ltfs", "windows"])
def test_iblade_lto_media_batch_updates_are_atomic_on_transition_errors(
    client: TestClient, blade_type: str
) -> None:
    _login(client)
    before = client.get("/iblade/lto_media/VOL001L9")
    assert before.status_code == 200
    payload = {
        "lto_media": [
            {"barcode": "VOL001L9", "state": "sequestered"},
            {"barcode": "VOL002L9", "state": "exported"},
        ]
    }
    alias_response = client.put("/iblade/lto-media", json=payload)
    strict_response = client.put(
        _strict_iblade_path("/iblade/lto-media", blade_type=blade_type),
        json=payload,
    )
    for response in (alias_response, strict_response):
        assert response.status_code == 409
        assert response.json()["code"] == "AML_CONFLICT"

    after = client.get("/iblade/lto_media/VOL001L9")
    assert after.status_code == 200
    assert after.json() == before.json()


@pytest.mark.parametrize("blade_type", ["ltfs", "windows"])
def test_iblade_lto_media_requires_documented_state_transition_order(
    client: TestClient, blade_type: str
) -> None:
    _login(client)
    alias_invalid = client.put("/iblade/lto-media/VOL001L9", json={"state": "exported"})
    strict_invalid = client.put(
        _strict_iblade_path("/iblade/lto-media/VOL001L9", blade_type=blade_type),
        json={"state": "exported"},
    )
    for response in (alias_invalid, strict_invalid):
        assert response.status_code == 409
        assert response.json()["code"] == "AML_CONFLICT"

    sequester = client.put("/iblade/lto-media/VOL001L9", json={"state": "sequestered"})
    assert sequester.status_code == 200
    formatted = client.put(
        _strict_iblade_path("/iblade/lto-media/VOL001L9", blade_type=blade_type),
        json={"state": "formatted"},
    )
    assert formatted.status_code == 200
    exported = client.put("/iblade/lto-media/VOL001L9", json={"state": "exported"})
    assert exported.status_code == 200
    assert exported.json()["state"] == "exported"


@pytest.mark.parametrize("blade_type", ["ltfs", "windows"])
def test_iblade_messages_put_close_requires_closed_by_and_keeps_delete_alias(
    client: TestClient, blade_type: str
) -> None:
    _login(client)
    alias_missing = client.put("/iblade/messages/MSG-001", json={})
    strict_missing = client.put(
        _strict_iblade_path("/iblade/messages/MSG-001", blade_type=blade_type),
        json={},
    )
    for response in (alias_missing, strict_missing):
        assert response.status_code == 400
        assert response.json()["code"] == "AML_BAD_REQUEST"

    close_response = client.put("/iblade/messages/MSG-001", json={"closed_by": "ops-user"})
    assert close_response.status_code == 200
    closed_payload = close_response.json()
    assert closed_payload["acknowledged"] is True
    assert closed_payload["closed_by"] == "ops-user"

    delete_response = client.delete(
        _strict_iblade_path("/iblade/messages/MSG-001", blade_type=blade_type)
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["acknowledged"] is True


@pytest.mark.parametrize("blade_type", ["ltfs", "windows"])
def test_iblade_messages_bulk_put_is_atomic_and_supports_alias_fields(
    client: TestClient, blade_type: str
) -> None:
    _login(client)
    reopened = aml_state.update_iblade_message("MSG-002", {"acknowledged": False})
    assert reopened is not None

    invalid_payload = {"closed_by": "batch-operator", "ids": ["MSG-001", "DOES-NOT-EXIST"]}
    alias_invalid = client.put("/iblade/messages", json=invalid_payload)
    strict_invalid = client.put(
        _strict_iblade_path("/iblade/messages", blade_type=blade_type),
        json=invalid_payload,
    )
    for response in (alias_invalid, strict_invalid):
        assert response.status_code == 404
        assert response.json()["code"] == "AML_NOT_FOUND"

    still_open = client.get("/iblade/messages/MSG-001")
    assert still_open.status_code == 200
    assert still_open.json()["acknowledged"] is False

    close_all_response = client.put(
        "/iblade/messages",
        json={"closedBy": "batch-operator", "closeAll": True},
    )
    assert close_all_response.status_code == 200
    closed = close_all_response.json()
    assert closed
    assert all(item["acknowledged"] for item in closed)
    assert {item["closed_by"] for item in closed} == {"batch-operator"}

    strict_open_messages = client.get(
        _strict_iblade_path("/iblade/status/open-messages", blade_type=blade_type)
    )
    assert strict_open_messages.status_code == 200
    assert strict_open_messages.json() == []


@pytest.mark.parametrize("blade_type", ["ltfs", "windows"])
def test_iblade_network_configuration_path_updates_and_validates(
    client: TestClient, blade_type: str
) -> None:
    _login(client)
    payload = {
        "hostname": "iblade-updated",
        "management_ip": "192.168.10.55",
        "subnet_mask": "255.255.255.0",
        "gateway": "192.168.10.1",
        "dns": ["192.168.10.2"],
        "mtu": 9000,
    }
    alias_response = client.put("/iblade/network/configuration/mgmt0/2", json=payload)
    strict_response = client.put(
        _strict_iblade_path("/iblade/network/configuration/mgmt0/2", blade_type=blade_type),
        json=payload,
    )
    for response in (alias_response, strict_response):
        assert response.status_code == 200
        body = response.json()
        assert body["hostname"] == "iblade-updated"
        assert body["configurationPort"] == "mgmt0"
        assert body["configurationVersion"] == 2

    invalid_alias = client.put("/iblade/network/configuration/mgmt0/0", json=payload)
    invalid_strict = client.put(
        _strict_iblade_path("/iblade/network/configuration/mgmt0/0", blade_type=blade_type),
        json=payload,
    )
    for response in (invalid_alias, invalid_strict):
        assert response.status_code == 400
        assert response.json()["code"] == "AML_BAD_REQUEST"


@pytest.mark.parametrize(
    ("path", "payload", "status_code", "error_code"),
    [
        ("/iblade/operations/assignment", {"index": 999}, 404, "AML_NOT_FOUND"),
        ("/iblade/operations/merge", {"source": 1, "destination": 1}, 400, "AML_BAD_REQUEST"),
        ("/iblade/operations/prepare-export", {"index": 1}, 409, "AML_CONFLICT"),
        ("/iblade/operations/repair", {"index": 1}, 409, "AML_CONFLICT"),
        ("/iblade/operations/replicate", {"source": 1, "destination": 1}, 400, "AML_BAD_REQUEST"),
        ("/iblade/operations/safe-repair", {"index": 2}, 409, "AML_CONFLICT"),
    ],
)
@pytest.mark.parametrize("blade_type", ["ltfs", "windows"])
def test_iblade_operation_preconditions_are_enforced_on_alias_and_strict_paths(
    client: TestClient,
    path: str,
    payload: dict[str, object],
    status_code: int,
    error_code: str,
    blade_type: str,
) -> None:
    _login(client)
    alias_response = client.post(path, json=payload)
    strict_response = client.post(_strict_iblade_path(path, blade_type=blade_type), json=payload)
    for response in (alias_response, strict_response):
        assert response.status_code == status_code
        assert response.json()["code"] == error_code


def test_iblade_volume_groups_create_and_delete(client: TestClient) -> None:
    _login(client)
    create_response = client.post(
        "/iblade/volume_groups",
        json={
            "name": "qa-vg",
            "state": "READY",
            "reason": "NONE",
            "policy": "balanced",
            "tapes": [],
        },
    )
    assert create_response.status_code == 201
    created = create_response.json()
    assert created["name"] == "qa-vg"

    delete_response = client.delete(f"/iblade/volume-groups/{created['index']}")
    assert delete_response.status_code == 200
    deleted = delete_response.json()
    assert deleted["index"] == created["index"]


def test_iblade_host_ip_crud(client: TestClient) -> None:
    _login(client)
    create_response = client.post(
        "/iblade/hosts/192.168.10.77",
        json={
            "hostname": "backup-c",
            "wwn": "10:00:00:00:00:00:00:77",
            "connection_type": "ethernet",
            "state": "connected",
        },
    )
    assert create_response.status_code == 200
    created = create_response.json()
    assert created["ip"] == "192.168.10.77"
    assert created["hostname"] == "backup-c"
    # iBlade WS Rev A: a host add/update requires a reboot to take effect.
    assert created["reboot_required"] is True

    get_response = client.get("/iblade/hosts/192.168.10.77")
    assert get_response.status_code == 200
    fetched = get_response.json()
    assert fetched["id"] == created["id"]
    # Reads do not signal a pending reboot.
    assert fetched["reboot_required"] is False

    delete_response = client.delete("/iblade/hosts/192.168.10.77")
    assert delete_response.status_code == 200
    deleted = delete_response.json()
    assert deleted["id"] == created["id"]

    missing_response = client.get("/iblade/hosts/192.168.10.77")
    assert missing_response.status_code == 404
    error = missing_response.json()
    assert error["code"] == "AML_NOT_FOUND"


def test_iblade_host_put_signals_reboot_required(client: TestClient) -> None:
    _login(client)
    put_response = client.put(
        "/iblade/hosts",
        json={
            "hosts": [
                {
                    "id": "HOST-050",
                    "hostname": "alpha",
                    "ip": "192.168.10.51",
                    "wwn": "",
                    "connection_type": "ethernet",
                    "state": "connected",
                }
            ]
        },
    )
    assert put_response.status_code == 200
    hosts = put_response.json()
    # PUT overwrites the allowed-host list; per iBlade WS Rev A this requires a
    # reboot, signalled on every host in the response.
    assert hosts, "expected at least one host in the response"
    assert all(host["reboot_required"] is True for host in hosts)

    # A subsequent read reports no pending reboot on the response objects.
    list_response = client.get("/iblade/hosts")
    assert list_response.status_code == 200
    assert all(host["reboot_required"] is False for host in list_response.json())


def test_iblade_host_reboot_required_is_not_persisted(client: TestClient) -> None:
    _login(client)
    # A client that injects reboot_required=True must not have it stored: it is a
    # response-only signal, so a subsequent read reports False.
    post_response = client.post(
        "/iblade/hosts/192.168.10.61",
        json={"hostname": "inject", "reboot_required": True},
    )
    assert post_response.status_code == 200
    assert post_response.json()["reboot_required"] is True  # signalled on the mutation

    get_response = client.get("/iblade/hosts/192.168.10.61")
    assert get_response.status_code == 200
    assert get_response.json()["reboot_required"] is False  # not persisted


def test_iblade_host_post_uses_next_available_host_id(client: TestClient) -> None:
    _login(client)
    delete_seed_response = client.delete("/iblade/hosts/192.168.10.21")
    assert delete_seed_response.status_code == 200

    create_response = client.post("/iblade/hosts/192.168.10.88", json={"hostname": "backup-new"})
    assert create_response.status_code == 200
    created = create_response.json()
    assert created["id"] == "HOST-003"

    preserved_response = client.get("/iblade/hosts/192.168.10.22")
    assert preserved_response.status_code == 200
    preserved = preserved_response.json()
    assert preserved["id"] == "HOST-002"


def test_iblade_hosts_put_rejects_non_object_entries_without_partial_updates(
    client: TestClient,
) -> None:
    _login(client)
    before_response = client.get("/iblade/hosts")
    assert before_response.status_code == 200
    before_hosts = before_response.json()

    update_response = client.put(
        "/iblade/hosts",
        json={"hosts": [before_hosts[0], "bad-entry"]},
    )
    assert update_response.status_code == 400
    error = update_response.json()
    assert error["code"] == "AML_BAD_REQUEST"

    after_response = client.get("/iblade/hosts")
    assert after_response.status_code == 200
    assert after_response.json() == before_hosts


def test_iblade_messages_put_supports_close_all_and_single_close(client: TestClient) -> None:
    _login(client)
    bulk_close = client.put("/iblade/messages", json={"closed_by": "ops", "close_all": True})
    assert bulk_close.status_code == 200
    closed_messages = bulk_close.json()
    assert closed_messages
    assert all(item["acknowledged"] for item in closed_messages)
    assert all(item.get("closed_by") == "ops" for item in closed_messages)

    open_messages_after_bulk = client.get("/iblade/messages")
    assert open_messages_after_bulk.status_code == 200
    assert open_messages_after_bulk.json() == []

    aml_state.update_iblade_message("MSG-001", {"acknowledged": False, "closed_by": None, "closed_at": None})
    single_close = client.put("/iblade/messages/MSG-001", json={"closed_by": "security"})
    assert single_close.status_code == 200
    payload = single_close.json()
    assert payload["id"] == "MSG-001"
    assert payload["acknowledged"] is True
    assert payload["closed_by"] == "security"


def test_iblade_messages_put_requires_ids_or_close_all(client: TestClient) -> None:
    _login(client)
    response = client.put("/iblade/messages", json={"closed_by": "ops"})
    assert response.status_code == 400
    payload = response.json()
    assert payload["code"] == "AML_BAD_REQUEST"


def test_iblade_lto_media_bulk_put_is_atomic_when_transition_validation_fails(
    client: TestClient,
) -> None:
    _login(client)
    aml_state.update_aml_media("VOL001L9", {"state": "home"})
    aml_state.update_aml_media("VOL002L9", {"state": "loaded"})

    response = client.put(
        "/iblade/lto-media",
        json=[
            {"barcode": "VOL001L9", "state": "stored"},
            {"barcode": "VOL002L9", "state": "sequestered"},
        ],
    )
    assert response.status_code == 409
    payload = response.json()
    assert payload["code"] == "AML_CONFLICT"

    medium_one = client.get("/iblade/lto_media/VOL001L9")
    medium_two = client.get("/iblade/lto_media/VOL002L9")
    assert medium_one.status_code == 200
    assert medium_two.status_code == 200
    assert medium_one.json()["state"] == "home"
    assert medium_two.json()["state"] == "loaded"


def test_iblade_network_configuration_path_updates_versioned_metadata(client: TestClient) -> None:
    _login(client)
    response = client.put(
        "/iblade/network/configuration/mgmt0/3",
        json={"hostname": "iblade-updated", "management_ip": "192.168.10.77"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["hostname"] == "iblade-updated"
    assert payload["management_ip"] == "192.168.10.77"
    assert payload["configurationPort"] == "mgmt0"
    assert payload["configurationVersion"] == 3
    assert "configurationUpdatedAt" in payload


def test_iblade_prepare_export_requires_formatted_media(client: TestClient) -> None:
    _login(client)
    group = client.get("/iblade/volume-groups/1")
    assert group.status_code == 200
    barcodes = group.json()["tapes"]
    assert barcodes
    for barcode in barcodes:
        aml_state.update_aml_media(barcode, {"state": "home"})

    response = client.post("/iblade/operations/prepare-export", json={"index": 1})
    assert response.status_code == 409
    payload = response.json()
    assert payload["code"] == "AML_CONFLICT"

    for barcode in barcodes:
        aml_state.update_aml_media(barcode, {"state": "formatted"})
    success = client.post("/iblade/operations/prepare-export", json={"index": 1})
    assert success.status_code == 202
    job_id = success.json()["job_id"]
    job = aml_state.get_aml_job(job_id)
    assert job is not None
    assert job.get("metadata", {}).get("tapes") == barcodes


def test_iblade_replicate_operation_requires_source_media(client: TestClient) -> None:
    _login(client)
    aml_state.update_iblade_volume_group(2, {"tapes": []})
    response = client.post("/iblade/operations/replicate", json={"source": 2, "destination": 1})
    assert response.status_code == 409
    payload = response.json()
    assert payload["code"] == "AML_CONFLICT"


def test_iblade_nas_drives_supports_empty_inventory(monkeypatch, client: TestClient) -> None:
    _login(client)
    monkeypatch.setattr("openblade.api.routes_iblade.aml_state.list_aml_drives", lambda: [])
    response = client.get("/iblade/nas-drives")
    assert response.status_code == 200
    assert response.json() == []


def test_iblade_nas_drives_reflect_mixed_drive_states(client: TestClient) -> None:
    _login(client)
    aml_state.update_aml_drive("DRV-001", {"status": "online", "state": "idle"})
    aml_state.update_aml_drive("DRV-002", {"status": "maintenance", "state": "loaded"})
    response = client.get("/iblade/nas-drives")
    assert response.status_code == 200
    drives = response.json()
    assert any(item["status"] == "online" and item["state"] == "idle" for item in drives)
    assert any(item["status"] == "maintenance" and item["state"] == "loaded" for item in drives)


def test_iblade_jobs_list_and_update(client: TestClient) -> None:
    _login(client)
    op_response = client.post("/iblade/system/snapshot", json={})
    assert op_response.status_code == 202
    queued = op_response.json()
    job_id = queued["job_id"]

    list_response = client.get("/iblade/jobs")
    assert list_response.status_code == 200
    jobs = list_response.json()
    assert any(item["id"] == job_id for item in jobs)

    update_response = client.put(f"/iblade/jobs/{job_id}", json={"job_state": "cancelled"})
    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["id"] == job_id
    assert updated["status"] == "cancelled"
    assert updated["closed"] is not None


def test_iblade_jobs_list_completed_window_filters_terminal_jobs(client: TestClient) -> None:
    _login(client)
    now = datetime.now(timezone.utc)
    aml_state.set_aml_job(
        "JOB-ACTIVE",
        {
            "type": "snapshot",
            "status": "active",
            "requestedAt": now.isoformat().replace("+00:00", "Z"),
            "result": "active job",
        },
    )
    aml_state.set_aml_job(
        "JOB-RECENT",
        {
            "type": "snapshot",
            "status": "completed",
            "requestedAt": (now - timedelta(days=2)).isoformat().replace("+00:00", "Z"),
            "closedAt": (now - timedelta(days=1)).isoformat().replace("+00:00", "Z"),
            "result": "recent job",
        },
    )
    aml_state.set_aml_job(
        "JOB-OLD",
        {
            "type": "snapshot",
            "status": "completed",
            "requestedAt": (now - timedelta(days=12)).isoformat().replace("+00:00", "Z"),
            "closedAt": (now - timedelta(days=10)).isoformat().replace("+00:00", "Z"),
            "result": "old job",
        },
    )

    list_response = client.get("/iblade/jobs?completed=7")
    assert list_response.status_code == 200
    ids = {item["id"] for item in list_response.json()}
    assert "JOB-ACTIVE" in ids
    assert "JOB-RECENT" in ids
    assert "JOB-OLD" not in ids


def test_iblade_jobs_put_is_atomic_when_transition_validation_fails(client: TestClient) -> None:
    _login(client)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    aml_state.set_aml_job("JOB-QUEUE", {"type": "snapshot", "status": "queued", "requestedAt": now})
    aml_state.set_aml_job(
        "JOB-DONE",
        {
            "type": "snapshot",
            "status": "completed",
            "requestedAt": now,
            "closedAt": now,
        },
    )

    update_response = client.put(
        "/iblade/jobs",
        json={
            "jobs": [
                {"id": "JOB-QUEUE", "job_state": "cancelled"},
                {"id": "JOB-DONE", "job_state": "cancelled"},
            ]
        },
    )
    assert update_response.status_code == 400
    error = update_response.json()
    assert error["code"] == "AML_BAD_REQUEST"

    queued = aml_state.get_aml_job("JOB-QUEUE")
    done = aml_state.get_aml_job("JOB-DONE")
    assert queued is not None and queued["status"] == "queued"
    assert done is not None and done["status"] == "completed"


def test_iblade_jobs_put_rejects_duplicate_job_ids(client: TestClient) -> None:
    _login(client)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    aml_state.set_aml_job("JOB-DUP", {"type": "snapshot", "status": "queued", "requestedAt": now})

    response = client.put(
        "/iblade/jobs",
        json={
            "jobs": [
                {"id": "JOB-DUP", "job_state": "cancelled"},
                {"id": "JOB-DUP", "job_state": "completed"},
            ]
        },
    )
    assert response.status_code == 400
    error = response.json()
    assert error["code"] == "AML_BAD_REQUEST"
    assert "Duplicate job id" in error["summary"]

    stored = aml_state.get_aml_job("JOB-DUP")
    assert stored is not None and stored["status"] == "queued"


def test_iblade_job_update_rejects_terminal_to_active_transition(client: TestClient) -> None:
    _login(client)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    aml_state.set_aml_job(
        "JOB-TERMINAL",
        {
            "type": "snapshot",
            "status": "completed",
            "requestedAt": now,
            "closedAt": now,
        },
    )

    response = client.put("/iblade/jobs/JOB-TERMINAL", json={"job_state": "active"})
    assert response.status_code == 400
    error = response.json()
    assert error["code"] == "AML_BAD_REQUEST"


def test_iblade_job_update_accepts_numeric_cancel_state(client: TestClient) -> None:
    _login(client)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    aml_state.set_aml_job("JOB-NUM", {"type": "snapshot", "status": "queued", "requestedAt": now})

    response = client.put("/iblade/jobs/JOB-NUM", json={"job_state": 3})
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "cancelled"
    assert payload["closed"] is not None


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


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/iblade/operations/assignment", {"index": "not-a-number", "tapes": ["VOL001L9"]}),
        ("/iblade/operations/merge", {"source": "left", "destination": 2}),
        ("/iblade/operations/merge", {"source": 1, "destination": "right"}),
        ("/iblade/operations/repair", {"index": "oops"}),
        ("/iblade/operations/volume-groups/repair", {"index": "oops"}),
        ("/iblade/operations/safe-repair", {"index": True}),
    ],
)
def test_iblade_operations_reject_non_integer_group_indices(
    client: TestClient,
    path: str,
    payload: dict[str, object],
) -> None:
    _login(client)
    response = client.post(path, json=payload)
    assert response.status_code == 400
    error = response.json()
    assert error["code"] == "AML_BAD_REQUEST"
    assert "must be an integer" in error["summary"]


@pytest.mark.parametrize("blade_type", ["ltfs", "windows"])
def test_iblade_reports_support_report_criteria_and_csv(
    client: TestClient, blade_type: str
) -> None:
    _login(client)
    params = {"reportCriteria": '{"state":"home"}'}
    alias_response = client.get(
        "/iblade/reports/media",
        params=params,
        headers={"Accept": "text/csv"},
    )
    strict_response = client.get(
        _strict_iblade_path("/iblade/reports/media", blade_type=blade_type),
        params=params,
        headers={"Accept": "text/csv"},
    )
    for response in (alias_response, strict_response):
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert "barcode" in response.text


def test_iblade_report_email_preserves_report_criteria_metadata(client: TestClient) -> None:
    _login(client)
    response = client.post(
        "/iblade/reports/media/email",
        json={"recipients": ["ops@example.com"], "reportCriteria": {"state": "SCRATCH"}},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    job = aml_state.get_aml_job(job_id)
    assert job is not None
    metadata = job.get("metadata", {})
    assert metadata["reportCriteria"]["state"] == "SCRATCH"


@pytest.mark.parametrize("blade_type", ["ltfs", "windows"])
def test_iblade_status_io_reflects_active_jobs_and_loaded_drives(
    client: TestClient, blade_type: str
) -> None:
    _login(client)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    aml_state.set_aml_job(
        "JOB-IO-ACTIVE",
        {"type": "iblade-replicate", "status": "active", "requestedAt": now},
    )
    aml_state.set_aml_job(
        "JOB-IO-QUEUED",
        {"type": "iblade-snapshot", "status": "queued", "requestedAt": now},
    )
    aml_state.update_aml_drive("DRV-001", {"loadedMedia": "VOL001L9"})

    alias_response = client.get("/iblade/status/io")
    strict_response = client.get(_strict_iblade_path("/iblade/status/io", blade_type=blade_type))
    assert alias_response.status_code == 200
    assert strict_response.status_code == 200
    assert strict_response.json() == alias_response.json()

    payload = alias_response.json()
    assert payload["queueDepth"] >= 2
    assert payload["activeTransfers"] >= 1
    assert payload["activeDrives"]


@pytest.mark.parametrize("blade_type", ["ltfs", "windows"])
def test_iblade_status_open_messages_tracks_ack_flow(client: TestClient, blade_type: str) -> None:
    _login(client)
    before = client.get("/iblade/status/open-messages")
    strict_before = client.get(
        _strict_iblade_path("/iblade/status/open-messages", blade_type=blade_type)
    )
    assert before.status_code == 200
    assert strict_before.status_code == 200
    assert len(before.json()) == 1

    ack_response = client.delete("/iblade/messages/MSG-001")
    assert ack_response.status_code == 200

    after = client.get("/iblade/status/open-messages")
    strict_after = client.get(
        _strict_iblade_path("/iblade/status/open-messages", blade_type=blade_type)
    )
    strict_system_after = client.get(
        _strict_iblade_path("/iblade/status/system/open-messages", blade_type=blade_type)
    )
    assert after.status_code == 200
    assert strict_after.status_code == 200
    assert strict_system_after.status_code == 200
    assert after.json() == []
    assert strict_after.json() == []
    assert strict_system_after.json() == []


def test_iblade_volume_groups_enforce_protected_group_delete_constraint(client: TestClient) -> None:
    _login(client)
    response = client.delete("/iblade/volume-groups/1")
    assert response.status_code == 400
    error = response.json()
    assert error["code"] == "AML_BAD_REQUEST"


def test_iblade_volume_groups_create_rejects_duplicate_tape_assignment(client: TestClient) -> None:
    _login(client)
    existing = client.get("/iblade/volume-groups/1")
    assert existing.status_code == 200
    barcode = existing.json()["tapes"][0]

    response = client.post(
        "/iblade/volume_groups", json={"name": "dup-assignment", "tapes": [barcode]}
    )
    assert response.status_code == 400
    error = response.json()
    assert error["code"] == "AML_BAD_REQUEST"


def test_iblade_volume_groups_bulk_put_is_atomic_on_duplicate_tapes(client: TestClient) -> None:
    _login(client)
    before = client.get("/iblade/volume-groups")
    assert before.status_code == 200
    groups = before.json()
    duplicate_tape = groups[0]["tapes"][0]

    response = client.put(
        "/iblade/volume-groups",
        json=[
            {**groups[0], "tapes": [duplicate_tape]},
            {**groups[1], "tapes": [duplicate_tape]},
        ],
    )
    assert response.status_code == 400
    error = response.json()
    assert error["code"] == "AML_BAD_REQUEST"

    after = client.get("/iblade/volume-groups")
    assert after.status_code == 200
    assert after.json() == groups


def test_strict_windows_section_validation_differs_from_ltfs(client: TestClient) -> None:
    ltfs_response = client.get(
        _strict_iblade_path("/iblade/states", blade_type="ltfs", section_number=2)
    )
    windows_response = client.get(
        _strict_iblade_path("/iblade/states", blade_type="windows", section_number=2)
    )
    assert ltfs_response.status_code == 200
    assert windows_response.status_code == 404
    error = windows_response.json()
    assert error["code"] == "AML_NOT_FOUND"


def test_windows_blade_section_endpoints_support_get_and_delete(client: TestClient) -> None:
    _login(client)
    get_response = client.get("/aml/devices/blade/windows/1")
    assert get_response.status_code == 200
    payload = get_response.json()["windowsBlade"]
    assert payload["sectionNumber"] == 1
    assert payload["status"] == "online"

    delete_response = client.delete("/aml/devices/blade/windows/1")
    assert delete_response.status_code == 200
    assert delete_response.json()["code"] == 0

    updated_response = client.get("/aml/devices/blade/windows/1")
    assert updated_response.status_code == 200
    updated_payload = updated_response.json()["windowsBlade"]
    assert updated_payload["enabled"] is False
    assert updated_payload["status"] == "offline"


def test_iblade_mode_controls_switch_strict_vs_extended_behavior(tmp_path: Path) -> None:
    with _client_with_mode(tmp_path, IBladeCompatibilityMode.STRICT) as strict_client:
        _login(strict_client)
        strict_alias = strict_client.get("/iblade/system/extended-snapshot")
        strict_path = strict_client.get(
            _strict_iblade_path("/iblade/system/extended-snapshot", blade_type="windows")
        )
        assert strict_alias.status_code == 404
        assert strict_path.status_code == 404

        strict_settings = strict_client.put(
            "/iblade/system/settings",
            json={"openbladeExtraSetting": True},
        )
        assert strict_settings.status_code == 400

    with _client_with_mode(tmp_path, IBladeCompatibilityMode.EXTENDED) as extended_client:
        _login(extended_client)
        extended_alias = extended_client.get("/iblade/system/extended-snapshot")
        extended_path = extended_client.get(
            _strict_iblade_path("/iblade/system/extended-snapshot", blade_type="windows")
        )
        assert extended_alias.status_code == 200
        assert extended_path.status_code == 200

        update_response = extended_client.put(
            "/iblade/system/settings",
            json={"openbladeExtraSetting": True},
        )
        assert update_response.status_code == 200
        settings_response = extended_client.get("/iblade/system/settings")
        assert settings_response.status_code == 200
        assert settings_response.json()["openbladeExtraSetting"] is True
