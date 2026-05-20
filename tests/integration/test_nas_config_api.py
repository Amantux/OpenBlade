from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_app_context(tmp_path: Path) -> None:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'nas-config-api.db'}"))
    reset_context(context)


def test_list_policies_returns_seeded_defaults() -> None:
    response = client.get("/nas/policies")

    assert response.status_code == 200
    assert len(response.json()) >= 3


def test_create_and_get_policy() -> None:
    payload = {
        "id": "custom-policy",
        "name": "Custom Policy",
        "policy_type": "balanced",
        "copies_required": 2,
        "allow_sharding": True,
        "max_parallelism": 3,
        "shard_strategy": "capacity_weighted",
    }

    create_response = client.post("/nas/policies", json=payload)
    get_response = client.get(f"/nas/policies/{payload['id']}")

    assert create_response.status_code in {200, 201}
    assert get_response.status_code == 200
    assert get_response.json()["id"] == payload["id"]
    assert get_response.json()["copies_required"] == payload["copies_required"]
    assert get_response.json()["shard_strategy"] == payload["shard_strategy"]


def test_delete_policy_not_found() -> None:
    response = client.delete("/nas/policies/missing-policy")

    assert response.status_code == 404


def test_delete_policy_blocked_when_referenced() -> None:
    policy_payload = {
        "id": "referenced-policy",
        "name": "Referenced Policy",
        "policy_type": "balanced",
    }
    share_payload = {
        "path": "/openblade/referenced-share",
        "name": "Referenced Share",
        "share_type": "pool",
        "default_policy_id": policy_payload["id"],
        "writable": True,
        "description": "Share backed by a custom policy.",
    }

    assert client.post("/nas/policies", json=policy_payload).status_code in {200, 201}
    assert client.post("/nas/shares", json=share_payload).status_code in {200, 201}

    response = client.delete(f"/nas/policies/{policy_payload['id']}")

    assert response.status_code == 400
    assert policy_payload["id"] in response.json()["detail"]


def test_list_cache_drives() -> None:
    response = client.get("/nas/cache-drives")

    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_create_cache_drive() -> None:
    payload = {
        "id": "cache-a",
        "name": "Cache A",
        "root_path": "/srv/cache-a",
        "max_bytes": 1000000,
        "min_free_bytes": 100000,
        "support_reflink_or_hardlink": True,
    }

    response = client.post("/nas/cache-drives", json=payload)

    assert response.status_code in {200, 201}
    assert response.json()["id"] == payload["id"]
    assert response.json()["root_path"] == payload["root_path"]


def test_get_source_stream_config() -> None:
    response = client.get("/nas/source-stream")

    assert response.status_code == 200
    assert response.json()["enabled"] is True
    assert response.json()["checksum_mode"] == "precompute_and_post_verify"


def test_update_source_stream_config() -> None:
    payload = {
        "enabled": True,
        "require_source_online_for_entire_job": True,
        "preflight_read_check": True,
        "checksum_mode": "streaming",
        "retry_policy": "linear",
        "max_retries": 5,
        "fail_on_source_change": True,
        "snapshot_required": False,
        "source_change_detection": "size_mtime_checksum",
        "allow_partial_dataset_success": True,
    }

    response = client.put("/nas/source-stream", json=payload)

    assert response.status_code == 200
    assert response.json()["checksum_mode"] == payload["checksum_mode"]
    assert response.json()["max_retries"] == payload["max_retries"]
    assert response.json()["allow_partial_dataset_success"] is True


def test_list_shares_returns_seeded_defaults() -> None:
    response = client.get("/nas/shares")

    assert response.status_code == 200
    assert len(response.json()) >= 6


def test_create_share_with_invalid_policy_returns_400() -> None:
    payload = {
        "path": "/openblade/invalid-share",
        "name": "Invalid Share",
        "share_type": "pool",
        "default_policy_id": "missing-policy",
        "writable": True,
    }

    response = client.post("/nas/shares", json=payload)

    assert response.status_code == 400
    assert "missing-policy" in response.json()["detail"]
