from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, get_context, reset_context
from openblade.config import OpenBladeConfig


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'catalog-test.db'}"))
    reset_context(context)
    return TestClient(app)


def _first_data_barcode() -> str:
    context = get_context()
    return next(
        str(slot.barcode)
        for slot in context.library.inventory().slots
        if slot.barcode is not None and not str(slot.barcode).startswith("CLN")
    )


def _format_first_tape(client: TestClient) -> tuple[str, str]:
    barcode = _first_data_barcode()
    response = client.post(f"/cartridges/{barcode}/format/dry-run")
    assert response.status_code == 200
    return barcode, response.json()["token"]


def test_catalog_list_empty_returns_200(client: TestClient) -> None:
    response = client.get("/catalog/")

    assert response.status_code == 200
    assert response.json() == {"files": [], "total": 0}


def test_catalog_list_after_archive(client: TestClient, tmp_path: Path) -> None:
    barcode, token = _format_first_tape(client)
    assert client.post("/volume-groups/", json={"name": "photos"}).status_code == 201
    assert client.post("/volume-groups/photos/assign", json={"barcode": barcode}).status_code == 200
    assert (
        client.post("/cartridges/format/confirm", json={"barcode": barcode, "token": token}).status_code
        == 200
    )

    source = tmp_path / "source"
    source.mkdir()
    archived_file = source / "a.txt"
    archived_file.write_text("catalog route test")

    archive_response = client.post(
        "/archive/",
        json={"source_path": str(source), "volume_group": "photos"},
    )
    assert archive_response.status_code == 202

    catalog_response = client.get("/catalog/")
    assert catalog_response.status_code == 200

    payload = catalog_response.json()
    assert payload["total"] == 1
    assert len(payload["files"]) == 1
    file_record = payload["files"][0]
    assert file_record["source_path"] == "/photos/a.txt"
    assert file_record["size_bytes"] == archived_file.stat().st_size
    assert file_record["instance_count"] == 1
    assert file_record["shard_count"] == 1

    detail_response = client.get(f"/catalog/{file_record['id']}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["source_path"] == "/photos/a.txt"
    assert len(detail["instances"]) == 1
    assert detail["instances"][0]["barcode"] == barcode

    ltfs_tapes_response = client.get("/ltfs/tapes")
    assert ltfs_tapes_response.status_code == 200
    assert ltfs_tapes_response.json() == [barcode]

    ltfs_browse_response = client.get(
        "/ltfs/browse",
        params={"tape_barcode": barcode, "path_prefix": "/photos"},
    )
    assert ltfs_browse_response.status_code == 200
    browse_payload = ltfs_browse_response.json()
    assert browse_payload == [
        {
            "path": "/photos/a.txt",
            "size": archived_file.stat().st_size,
            "tape_barcode": barcode,
            "archived_at": browse_payload[0]["archived_at"],
            "shard_count": 1,
        }
    ]
    assert browse_payload[0]["archived_at"] is not None
