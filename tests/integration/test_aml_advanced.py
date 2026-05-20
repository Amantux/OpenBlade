from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api import aml_state
from openblade.api.main import app
from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'advanced-test.db'}"))
    reset_context(context)
    return TestClient(app)


@pytest.fixture()
def authed(client: TestClient) -> TestClient:
    resp = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert resp.status_code == 200
    return client


@pytest.fixture()
def service_client(client: TestClient) -> TestClient:
    resp = client.post("/aml/users/login", json={"name": "service", "password": "service123"})
    assert resp.status_code == 200
    return client


@pytest.mark.parametrize(
    "path",
    [
        "/aml/system/ha/config",
        "/aml/system/ekm/keys",
        "/aml/system/sharing/config",
        "/aml/system/remoteLibraries",
        "/aml/system/supportedMedia",
        "/aml/devices/blades/ltfs",
        "/aml/devices/blades/fibreChannel/ports",
    ],
)
def test_advanced_get_endpoints_require_auth(client: TestClient, path: str) -> None:
    resp = client.get(path)
    assert resp.status_code == 401


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        (
            "put",
            "/aml/system/ha/config",
            {"config": {"enabled": True, "mode": "activeStandby", "clusterName": "cluster-b", "heartbeatInterval": 10, "autoFailback": True}},
        ),
        (
            "post",
            "/aml/system/remoteLibraries",
            {"remoteLibrary": {"name": "remote-a", "host": "remote-a.example.com", "model": "Scalar i6000", "status": "connected", "protocol": "FC", "sharedSlots": 12}},
        ),
        ("delete", "/aml/system/remoteLibrary/rlib-missing", None),
    ],
)
def test_advanced_mutations_require_admin(
    service_client: TestClient, method: str, path: str, payload: dict[str, object] | None
) -> None:
    request = getattr(service_client, method)
    resp = request(path, json=payload) if payload is not None else request(path)
    assert resp.status_code == 403


def test_ha_config_get_put_happy_path(authed: TestClient) -> None:
    resp = authed.get("/aml/system/ha/config")
    assert resp.status_code == 200
    assert resp.json()["config"]["enabled"] is False

    update = {
        "config": {
            "enabled": True,
            "mode": "activeStandby",
            "clusterName": "cluster-b",
            "heartbeatInterval": 10,
            "autoFailback": True,
        }
    }
    put_resp = authed.put("/aml/system/ha/config", json=update)
    assert put_resp.status_code == 200

    follow_up = authed.get("/aml/system/ha/config")
    assert follow_up.status_code == 200
    assert follow_up.json()["config"] == update["config"]


def test_ekm_keys_get_returns_200(authed: TestClient) -> None:
    resp = authed.get("/aml/system/ekm/keys")
    assert resp.status_code == 200
    assert isinstance(resp.json()["keyList"]["key"], list)


def test_sharing_config_get_returns_200(authed: TestClient) -> None:
    resp = authed.get("/aml/system/sharing/config")
    assert resp.status_code == 200
    assert "config" in resp.json()


def test_remote_libraries_list_post_delete_happy_path(authed: TestClient) -> None:
    initial = authed.get("/aml/system/remoteLibraries")
    assert initial.status_code == 200
    assert initial.json()["remoteLibraryList"]["remoteLibrary"] == []

    create_resp = authed.post(
        "/aml/system/remoteLibraries",
        json={
            "remoteLibrary": {
                "name": "remote-a",
                "host": "remote-a.example.com",
                "model": "Scalar i6000",
                "status": "connected",
                "protocol": "FC",
                "sharedSlots": 12,
            }
        },
    )
    assert create_resp.status_code == 200

    after_create = authed.get("/aml/system/remoteLibraries")
    assert after_create.status_code == 200
    libraries = after_create.json()["remoteLibraryList"]["remoteLibrary"]
    created = next(item for item in libraries if item["name"] == "remote-a")
    assert created["host"] == "remote-a.example.com"

    delete_resp = authed.delete(f"/aml/system/remoteLibrary/{created['id']}")
    assert delete_resp.status_code == 200

    after_delete = authed.get("/aml/system/remoteLibraries")
    assert after_delete.status_code == 200
    remaining = after_delete.json()["remoteLibraryList"]["remoteLibrary"]
    assert all(item["id"] != created["id"] for item in remaining)


def test_ltfs_config_get_returns_200(authed: TestClient) -> None:
    resp = authed.get("/aml/devices/blades/ltfs")
    assert resp.status_code == 200
    assert isinstance(resp.json()["sectionList"]["section"], list)


def test_fc_config_get_returns_200(authed: TestClient) -> None:
    resp = authed.get("/aml/devices/blades/fibreChannel/ports")
    assert resp.status_code == 200
    assert isinstance(resp.json()["portList"]["port"], list)


def test_supported_media_get_returns_200(authed: TestClient) -> None:
    resp = authed.get("/aml/system/supportedMedia")
    assert resp.status_code == 200
    assert isinstance(resp.json()["supportedMediaList"]["mediaType"], list)


@pytest.mark.parametrize(
    "path",
    [
        "/aml/system/remoteLibrary/does-not-exist",
        "/aml/devices/blade/ltfs/999",
        "/aml/devices/blade/iSCSI/missing/config",
    ],
)
def test_missing_advanced_resources_return_404(authed: TestClient, path: str) -> None:
    resp = authed.get(path)
    assert resp.status_code == 404


def test_reset_context_clears_advanced_state(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'advanced-reset.db'}"
    first_context = create_context(OpenBladeConfig(db_url=db_url))
    reset_context(first_context)
    aml_state.set_aml_advanced_ha_config({"enabled": True, "clusterName": "mutated-cluster"})
    aml_state.create_aml_remote_library({"name": "remote-b", "host": "remote-b.example.com", "model": "Scalar i6000"})

    second_context = create_context(OpenBladeConfig(db_url=db_url))
    reset_context(second_context)

    assert aml_state.get_aml_advanced_ha_config()["enabled"] is False
    assert aml_state.get_aml_advanced_ha_config()["clusterName"] == "OpenBlade-HA"
    assert aml_state.list_aml_remote_libraries() == []
