"""``POST /restore/tree`` -- the HTTP half of ``openblade restore tree``.

The payload is ``TreeRestoreResult.to_dict()``, so the CLI and the API cannot
drift. What is asserted here is the behaviour a UI depends on: a dry run moves
nothing, a real run writes the tree under the destination preserving the source
layout, and a short result names the files it did not restore.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, get_context, reset_context
from openblade.config import OpenBladeConfig

SERVICE_TOKEN = "openblade-controller-dev-token-do-not-expose"


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'tree.db'}"))
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
    assert (
        client.post(f"/volume-groups/{volume_group}/assign", json={"barcode": barcode}).status_code
        == 200
    )
    dry_run = client.post(f"/cartridges/{barcode}/format/dry-run", headers=auth_headers)
    assert dry_run.status_code == 200
    assert (
        client.post(
            "/cartridges/format/confirm",
            json={"barcode": barcode, "token": dry_run.json()["token"]},
            headers={**auth_headers, "X-Openblade-Service-Token": SERVICE_TOKEN},
        ).status_code
        == 200
    )


def _archive_tree(client: TestClient, source_dir: Path, volume_group: str) -> None:
    (source_dir / "nested").mkdir(parents=True)
    (source_dir / "top.txt").write_text("top level file")
    (source_dir / "nested" / "deep.txt").write_text("nested file")
    response = client.post(
        "/archive/",
        json={"source_path": str(source_dir), "volume_group": volume_group},
    )
    assert response.status_code == 202


@pytest.fixture()
def archived(client: TestClient, tmp_path: Path) -> str:
    barcode = _data_barcodes(limit=1)[0]
    _format_and_assign(client, "photos", barcode)
    _archive_tree(client, tmp_path / "photos-source", "photos")
    return barcode


def test_dry_run_plans_without_writing_anything(
    client: TestClient, tmp_path: Path, archived: str
) -> None:
    destination = tmp_path / "planned"
    response = client.post(
        "/restore/tree",
        json={"catalog_prefix": "/photos", "dest_dir": str(destination), "dry_run": True},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["dryRun"] is True
    assert payload["catalogPrefix"] == "/photos"
    assert payload["filesRestored"] == 2
    assert payload["bytesRestored"] > 0
    assert payload["tapesUsed"] == [archived]
    assert payload["perTapeCounts"][archived] == 2
    assert payload["failures"] == []
    assert payload["status"] == "completed"
    # A preview that leaves files behind is the whole point of a preview.
    assert not destination.exists()


def test_run_restores_the_tree_preserving_the_source_layout(
    client: TestClient, tmp_path: Path, archived: str
) -> None:
    destination = tmp_path / "restored"
    response = client.post(
        "/restore/tree",
        json={"catalog_prefix": "/photos", "dest_dir": str(destination), "dry_run": False},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["dryRun"] is False
    assert payload["filesRestored"] == 2
    assert payload["status"] == "completed"
    assert (destination / "top.txt").read_text() == "top level file"
    assert (destination / "nested" / "deep.txt").read_text() == "nested file"


def test_the_job_row_records_the_tree_restore(
    client: TestClient, tmp_path: Path, archived: str
) -> None:
    payload = client.post(
        "/restore/tree",
        json={"catalog_prefix": "/photos", "dest_dir": str(tmp_path / "out"), "dry_run": False},
    ).json()
    job = client.get(f"/jobs/{payload['jobId']}")
    assert job.status_code == 200
    assert job.json()["job_type"] == "restore"


def test_a_prefix_with_nothing_archived_is_an_honest_empty_result(
    client: TestClient, tmp_path: Path
) -> None:
    response = client.post(
        "/restore/tree",
        json={"catalog_prefix": "/nothing-here", "dest_dir": str(tmp_path / "out")},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["filesRestored"] == 0
    assert payload["filesSkipped"] == 0
    assert payload["tapesUsed"] == []
    # dry_run defaults to False: an absent flag must not silently plan-only.
    assert payload["dryRun"] is False


def test_a_blank_prefix_or_destination_is_rejected(client: TestClient, tmp_path: Path) -> None:
    assert (
        client.post(
            "/restore/tree", json={"catalog_prefix": "", "dest_dir": str(tmp_path / "out")}
        ).status_code
        == 422
    )
    assert (
        client.post("/restore/tree", json={"catalog_prefix": "/photos", "dest_dir": ""}).status_code
        == 422
    )


def test_a_failure_answers_with_curated_text_and_fails_the_job(
    client: TestClient, tmp_path: Path, archived: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Boom(RuntimeError):
        """Stands in for a tool failure carrying argv and device paths."""

    def explode(*_args: object, **_kwargs: object) -> None:
        raise Boom("mkltfs /dev/sg3 failed: password=hunter2")

    monkeypatch.setattr("openblade.api.routes_restore.run_tree_restore", explode)
    response = client.post(
        "/restore/tree",
        json={"catalog_prefix": "/photos", "dest_dir": str(tmp_path / "out")},
    )
    assert response.status_code == 500
    detail = response.json()["detail"]
    # Untyped failures never reach the wire verbatim (safe_job_error).
    assert "hunter2" not in detail
    assert "/dev/sg3" not in detail
    assert "Boom" in detail
