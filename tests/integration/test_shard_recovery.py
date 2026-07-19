from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, get_context, reset_context
from openblade.config import OpenBladeConfig


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'shard-recovery.db'}"))
    reset_context(context)
    return TestClient(app, raise_server_exceptions=False)


def _data_barcodes(limit: int) -> list[str]:
    context = get_context()
    return [
        str(slot.barcode)
        for slot in context.library.inventory().slots
        if slot.barcode is not None and not str(slot.barcode).startswith("CLN")
    ][:limit]


def _admin_auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert response.status_code == 200
    session_id = response.cookies.get("sessionID")
    assert session_id is not None
    return {"Cookie": f"sessionID={session_id}"}


def _format_tape(client: TestClient, barcode: str) -> None:
    auth_headers = _admin_auth_headers(client)
    dry_run = client.post(f"/cartridges/{barcode}/format/dry-run", headers=auth_headers)
    assert dry_run.status_code == 200
    token = dry_run.json()["token"]
    assert (
        client.post(
            "/cartridges/format/confirm",
            json={"barcode": barcode, "token": token},
            headers={**auth_headers, "X-Openblade-Service-Token": "openblade-controller-dev-token-do-not-expose"},
        ).status_code
        == 200
    )


def _prepare_sharded_archive(client: TestClient, tmp_path: Path) -> tuple[dict[str, object], list[dict[str, object]]]:
    barcodes = _data_barcodes(limit=2)
    assert len(barcodes) == 2
    assert client.post("/volume-groups/", json={"name": "photos"}).status_code == 201
    for barcode in barcodes:
        assert client.post("/volume-groups/photos/assign", json={"barcode": barcode}).status_code == 200
        _format_tape(client, barcode)

    source = tmp_path / "source"
    source.mkdir()
    archived_file = source / "feature.bin"
    archived_file.write_bytes(bytes(index % 251 for index in range(2_400_000)))

    archive_response = client.post(
        "/archive/sharded",
        json={
            "source_path": str(source),
            "volume_group": "photos",
            "lane_barcodes": barcodes,
            "mode": "block_stripe",
            "block_size_mb": 1,
        },
    )
    assert archive_response.status_code == 202

    catalog_response = client.get("/catalog/")
    assert catalog_response.status_code == 200
    parent = catalog_response.json()["files"][0]

    shards_response = client.get(f"/catalog/{parent['id']}/shards")
    assert shards_response.status_code == 200
    return parent, shards_response.json()


def test_block_stripe_archive_creates_shard_catalog_entries(client: TestClient, tmp_path: Path) -> None:
    parent, shards = _prepare_sharded_archive(client, tmp_path)

    assert parent["shard_count"] == 2
    assert parent["shard_profile"] == "block_stripe"
    assert parent["block_size"] == 1024 * 1024
    assert parent["parent_id"] is None

    assert len(shards) == 2
    assert [shard["shard_index"] for shard in shards] == [0, 1]
    assert all(shard["shard_count"] == 2 for shard in shards)
    assert all(shard["block_size"] == 1024 * 1024 for shard in shards)
    assert all(shard["shard_profile"] == "block_stripe" for shard in shards)
    assert all(shard["parent_id"] == parent["id"] for shard in shards)
    assert {shard["instances"][0]["barcode"] for shard in shards} == set(_data_barcodes(limit=2))

    context = get_context()
    shard_records = context.catalog.list_shard_records(parent["id"])
    assert len(shard_records) == 2
    assert [record.shard_index for record in shard_records] == [0, 1]
    assert all(record.shard_count == 2 for record in shard_records)
    assert all(record.block_size == 1024 * 1024 for record in shard_records)
    assert all(record.shard_profile == "block_stripe" for record in shard_records)
    assert all(record.parent_id == parent["id"] for record in shard_records)


def test_restore_fails_gracefully_when_shard_entries_are_missing(client: TestClient, tmp_path: Path) -> None:
    parent, shards = _prepare_sharded_archive(client, tmp_path)
    assert len(shards) == 2

    context = get_context()
    for shard_record in context.catalog.list_shard_records(parent["id"]):
        context.catalog.session.delete(shard_record)
    context.catalog.session.commit()

    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()
    response = client.post(
        "/restore/",
        json={"catalog_path": parent["source_path"], "dest_path": str(restore_dir)},
    )

    assert response.status_code == 404
    assert "Missing shard catalog entries" in response.json()["detail"]
