import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'controller-isolation.db'}"))
    reset_context(context)
    return TestClient(app)


@pytest.fixture()
def admin_auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert response.status_code == 200
    session_id = response.cookies.get("sessionID")
    assert session_id is not None
    return {"Cookie": f"sessionID={session_id}"}


def _merge_headers(*header_sets: dict[str, str]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for header_set in header_sets:
        merged.update(header_set)
    return merged


def _find_direct_simulator_imports(paths: list[Path], repo_root: Path) -> list[str]:
    patterns = [
        re.compile(r"^\s*from\s+openblade\.(?:simulator|emulator)\b", re.MULTILINE),
        re.compile(r"^\s*import\s+openblade\.(?:simulator|emulator)\b", re.MULTILINE),
    ]
    violations: list[str] = []
    for path in sorted(paths):
        source = path.read_text(encoding="utf-8")
        if any(pattern.search(source) for pattern in patterns):
            violations.append(path.relative_to(repo_root).as_posix())
    return violations


def test_moveMedium_requires_service_token(client: TestClient) -> None:
    """moveMedium must be rejected without internal service token"""
    response = client.post(
        "/aml/media/move",
        json={"move": {"barcode": "VOL001L9", "destination": "1,1,11"}},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN_CONTROLLER_ONLY"


def test_moveMedium_rejected_with_user_token(client: TestClient, admin_auth_headers: dict[str, str]) -> None:
    """moveMedium must be rejected even with valid admin user token"""
    response = client.post(
        "/aml/media/move",
        json={"move": {"barcode": "VOL001L9", "destination": "1,1,11"}},
        headers=admin_auth_headers,
    )

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN_CONTROLLER_ONLY"


def test_moveMedium_accepted_with_service_token(
    client: TestClient,
    admin_auth_headers: dict[str, str],
    service_token_headers: dict[str, str],
) -> None:
    """moveMedium accepted only with service token"""
    response = client.post(
        "/aml/media/move",
        json={"move": {"barcode": "VOL001L9", "destination": "1,1,11"}},
        headers=_merge_headers(admin_auth_headers, service_token_headers),
    )

    assert response.status_code in {200, 202}
    assert response.status_code != 403


def test_wrong_service_token_rejected(client: TestClient, admin_auth_headers: dict[str, str]) -> None:
    """Wrong service token value must be rejected even with correct header name"""
    response = client.post(
        "/aml/media/move",
        json={"move": {"barcode": "VOL001L9", "destination": "1,1,11"}},
        headers={**admin_auth_headers, "X-Openblade-Service-Token": "wrong-token-value"},
    )

    assert response.status_code == 403


def test_format_confirm_requires_service_token(client: TestClient, admin_auth_headers: dict[str, str]) -> None:
    """format-confirm must require service token even for admin users"""
    response = client.post(
        "/cartridges/format/confirm",
        json={"barcode": "VOL001L9", "token": "any-token"},
        headers=admin_auth_headers,
    )

    assert response.status_code == 403


def test_mount_requires_service_token(client: TestClient, admin_auth_headers: dict[str, str]) -> None:
    """mount/load operations must require service token"""
    response = client.post(
        "/aml/mount",
        json={"mount": {"drive": "DRV-002", "barcode": "VOL002L9"}},
        headers=admin_auth_headers,
    )

    assert response.status_code == 403


def test_public_api_cannot_import_emulator_adapters() -> None:
    """No public API route file may import simulator/emulator modules directly"""
    repo_root = Path(__file__).resolve().parents[2]
    route_files = list((repo_root / "openblade" / "api").glob("routes_*.py"))

    violations = _find_direct_simulator_imports(route_files, repo_root)

    assert violations == []


def test_sftp_gateway_cannot_import_emulator_adapters() -> None:
    """Protocol gateway must not import simulator/emulator modules"""
    repo_root = Path(__file__).resolve().parents[2]
    sftp_files = list((repo_root / "openblade" / "sftp").rglob("*.py"))

    violations = _find_direct_simulator_imports(sftp_files, repo_root)

    assert violations == []
