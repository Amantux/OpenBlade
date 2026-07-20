"""End-to-end NAS user journey through the pool API.

Walks the experience a NAS client actually has:
upload -> download(online) -> file goes offline on tape -> download(409, hydrate
hint) -> request+run a restore job -> download(online again) with matching bytes.

This proves the pool/upload/download/restore surfaces are wired together, not just
individually functional.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api import routes_upload as ru
from openblade.api.main import app
from openblade.bootstrap import create_context, get_context, reset_context
from openblade.config import OpenBladeConfig

CONTENT = b"the quick brown fox jumps over the lazy dog" * 4
CHECKSUM = hashlib.sha256(CONTENT).hexdigest()
POOL = "journey-pool"


@pytest.fixture(autouse=True)
def _ctx(tmp_path: Path) -> None:
    reset_context(create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'journey.db'}")))


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def auth(client: TestClient) -> dict[str, str]:
    resp = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert resp.status_code == 200
    return {"Cookie": f"sessionID={resp.cookies.get('sessionID')}"}


def _drop_local_copies(file_id: str) -> None:
    record = get_context().catalog.get_nas_file_record(file_id)
    candidates = [ru._safe_resolve(ru._staging_dir(), file_id), ru._safe_resolve(ru._restore_dir(), file_id)]
    candidates += [Path(str(record[k])) for k in ("cache_path", "source_path") if record and record.get(k)]
    for candidate in candidates:
        if candidate.exists():
            candidate.unlink()


def _upload_then_offline(client: TestClient, auth: dict[str, str]) -> str:
    created = client.post("/nas/pools", json={"id": POOL, "name": "Journey Pool"}, headers=auth)
    assert created.status_code in (200, 201), created.text
    up = client.post(
        f"/api/pools/{POOL}/upload",
        files={"file": ("journey.bin", CONTENT, "application/octet-stream")},
        headers=auth,
    )
    assert up.status_code == 200, up.text
    file_id = up.json()["file_id"]
    _drop_local_copies(file_id)  # file is now offline on tape (record remains)
    return file_id


def test_nas_journey_upload_download_and_offline_detection(
    client: TestClient, auth: dict[str, str]
) -> None:
    """The wired part of the journey: create pool -> upload -> download online ->
    file goes offline -> download reports 409 with a hydrate hint (not a 404)."""
    created = client.post("/nas/pools", json={"id": POOL, "name": "Journey Pool"}, headers=auth)
    assert created.status_code in (200, 201), created.text
    up = client.post(
        f"/api/pools/{POOL}/upload",
        files={"file": ("journey.bin", CONTENT, "application/octet-stream")},
        headers=auth,
    )
    assert up.status_code == 200, up.text
    file_id = up.json()["file_id"]

    dl = client.get(f"/api/files/{file_id}/download", headers=auth)
    assert dl.status_code == 200 and dl.content == CONTENT

    _drop_local_copies(file_id)
    offline = client.get(f"/api/files/{file_id}/download", headers=auth)
    assert offline.status_code == 409
    assert "hydrate" in offline.json()["detail"].lower()


@pytest.mark.xfail(
    reason=(
        "KNOWN GAP: the NAS restore executor (HydrationExecutor) is a record-state "
        "simulator — it marks the file online_cached but does not materialize a "
        "downloadable copy (_simulate_content returns placeholder bytes and writes no "
        "file), so download stays 409 after a 'successful' restore. Flip to a real "
        "assertion when restore materializes content from tape."
    ),
    strict=True,
)
def test_nas_journey_restore_makes_file_downloadable(
    client: TestClient, auth: dict[str, str]
) -> None:
    """The full journey: after a restore/hydration job runs, the offline file must be
    downloadable again with the ORIGINAL bytes. Currently xfails at the last step."""
    file_id = _upload_then_offline(client, auth)
    rel_path = get_context().catalog.get_nas_file_record(file_id)["relative_path"]

    req = client.post(
        f"/nas/pools/{POOL}/request-restore",
        json={"paths": [rel_path], "destination": "/restore"},
        headers=auth,
    )
    assert req.status_code in (200, 201), req.text
    run = client.post(f"/nas/restore-jobs/{req.json()['id']}/run", headers=auth)
    assert run.status_code in (200, 202), run.text

    dl = client.get(f"/api/files/{file_id}/download", headers=auth)
    assert dl.status_code == 200, dl.text
    assert hashlib.sha256(dl.content).hexdigest() == CHECKSUM
