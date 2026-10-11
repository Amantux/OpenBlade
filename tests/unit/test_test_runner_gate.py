"""The test-runner route is dev-only and never targets an off-host AML (SSRF)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openblade.api import routes_test_runner
from openblade.api.routes_test_runner import aml_url_allowed


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(routes_test_runner.router)
    return TestClient(app)


@pytest.mark.parametrize(
    ("env", "enabled"),
    [
        ("development", None),
        ("development", "false"),
        ("production", "true"),
    ],
)
def test_run_is_404_unless_enabled_outside_production(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, env: str, enabled: str | None
) -> None:
    monkeypatch.setenv("OPENBLADE_ENV", env)
    if enabled is None:
        monkeypatch.delenv("OPENBLADE_ENABLE_TEST_RUNNER", raising=False)
    else:
        monkeypatch.setenv("OPENBLADE_ENABLE_TEST_RUNNER", enabled)
    resp = client.post("/api/test-runner/run", json={})
    assert resp.status_code == 404


@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.0.5:8000",
        "http://169.254.169.254/latest",
        "http://localhost.evil.example",
        "http://127.0.0.1@evil.example",
        "file:///etc/passwd",
        "gopher://localhost",
    ],
)
def test_run_rejects_off_host_aml_url_when_enabled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setenv("OPENBLADE_ENV", "development")
    monkeypatch.setenv("OPENBLADE_ENABLE_TEST_RUNNER", "true")
    resp = client.post("/api/test-runner/run", json={"i3_aml_url": url})
    assert resp.status_code == 400
    assert url not in resp.text


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8000",
        "http://127.0.0.1:8010",
        "http://[::1]:8000",
        "https://host.docker.internal:8443",
    ],
)
def test_loopback_and_docker_host_urls_allowed(url: str) -> None:
    assert aml_url_allowed(url)
