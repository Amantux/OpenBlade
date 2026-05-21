from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, get_context, reset_context
from openblade.config import OpenBladeConfig


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'ltfs-browse.db'}"))
    reset_context(context)
    return TestClient(app)


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


def _format_and_assign(client: TestClient, volume_group: str, barcode: str) -> None:
    auth_headers = _admin_auth_headers(client)
    assert client.post("/volume-groups/", json={"name": volume_group}).status_code == 201
    assert client.post(f"/volume-groups/{volume_group}/assign", json={"barcode": barcode}).status_code == 200
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


def _archive_one_file(client: TestClient, source_dir: Path, volume_group: str, file_name: str, content: str) -> None:
    source_dir.mkdir()
    (source_dir / file_name).write_text(content)
    response = client.post(
        "/archive/",
        json={"source_path": str(source_dir), "volume_group": volume_group},
    )
    assert response.status_code == 202


def test_ltfs_browse_and_tapes_return_empty_lists_for_empty_catalog(client: TestClient) -> None:
    browse_response = client.get("/ltfs/browse")
    assert browse_response.status_code == 200
    assert browse_response.json() == []

    tapes_response = client.get("/ltfs/tapes")
    assert tapes_response.status_code == 200
    assert tapes_response.json() == []


def test_ltfs_browse_filters_by_tape_and_path_prefix_and_tapes_are_unique(client: TestClient, tmp_path: Path) -> None:
    barcodes = _data_barcodes(limit=2)
    assert len(barcodes) == 2
    _format_and_assign(client, "alpha", barcodes[0])
    _format_and_assign(client, "beta", barcodes[1])

    _archive_one_file(client, tmp_path / "alpha-source", "alpha", "a.txt", "alpha browse")
    _archive_one_file(client, tmp_path / "beta-source", "beta", "b.txt", "beta browse")

    browse_by_tape = client.get("/ltfs/browse", params={"tape_barcode": barcodes[0]})
    assert browse_by_tape.status_code == 200
    assert [item["path"] for item in browse_by_tape.json()] == ["/alpha/a.txt"]
    assert all(item["tape_barcode"] == barcodes[0] for item in browse_by_tape.json())

    browse_by_prefix = client.get("/ltfs/browse", params={"path_prefix": "/beta"})
    assert browse_by_prefix.status_code == 200
    assert [item["path"] for item in browse_by_prefix.json()] == ["/beta/b.txt"]

    tapes_response = client.get("/ltfs/tapes")
    assert tapes_response.status_code == 200
    assert tapes_response.json() == sorted(barcodes)
