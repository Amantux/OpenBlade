"""Command compatibility matrix for real Quantum hardware smoke validation."""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class SmokeCommandProbe:
    id: str
    method: str
    path: str
    payload: dict[str, Any] | None
    requires_auth: bool
    expected_statuses: tuple[int, ...]
    covered_by: tuple[str, ...]


@dataclass(frozen=True)
class SmokeCommandResult:
    id: str
    method: str
    path: str
    status_code: int
    matched: bool
    expected_statuses: tuple[int, ...]
    covered_by: tuple[str, ...]


def _extract_token(payload: dict[str, Any]) -> str | None:
    for key in ("token", "access_token", "sessionToken"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _auth_header_basic(username: str, password: str) -> dict[str, str]:
    raw = f"{username}:{password}".encode()
    encoded = base64.b64encode(raw).decode("ascii")
    return {"Authorization": f"Basic {encoded}"}


def negotiate_auth_header(
    client: httpx.Client,
    username: str,
    password: str,
) -> tuple[dict[str, str], str]:
    attempts: list[tuple[str, dict[str, str]]] = [
        ("/aml/auth/login", {"username": username, "password": password}),
        ("/aml/users/login", {"name": username, "password": password}),
    ]
    for endpoint, payload in attempts:
        response = client.post(endpoint, json=payload)
        if response.status_code != 200:
            continue
        token = _extract_token(response.json())
        if token:
            return {"Authorization": f"Bearer {token}"}, f"bearer:{endpoint}"

    return _auth_header_basic(username, password), "basic"


def smoke_command_probes(
    *,
    include_motion: bool,
    include_control_plane: bool,
) -> list[SmokeCommandProbe]:
    probes: list[SmokeCommandProbe] = [
        SmokeCommandProbe(
            id="aml-library",
            method="GET",
            path="/aml/library",
            payload=None,
            requires_auth=True,
            expected_statuses=(200, 207),
            covered_by=("test_01_auth.py", "test_12_multi_library.py"),
        ),
        SmokeCommandProbe(
            id="aml-inventory-get",
            method="GET",
            path="/aml/library/inventory",
            payload=None,
            requires_auth=True,
            expected_statuses=(200,),
            covered_by=("test_02_inventory.py", "test_03_changer.py"),
        ),
        SmokeCommandProbe(
            id="aml-physical-map",
            method="GET",
            path="/aml/library/physical",
            payload=None,
            requires_auth=True,
            expected_statuses=(200,),
            covered_by=("test_02_inventory.py",),
        ),
        SmokeCommandProbe(
            id="aml-media-list",
            method="GET",
            path="/aml/media",
            payload=None,
            requires_auth=True,
            expected_statuses=(200,),
            covered_by=("test_02_inventory.py",),
        ),
        SmokeCommandProbe(
            id="aml-inventory-scan",
            method="POST",
            path="/aml/operations/inventory",
            payload={},
            requires_auth=True,
            expected_statuses=(200, 202, 400, 409, 422),
            covered_by=("test_02_inventory.py",),
        ),
    ]

    if include_motion:
        probes.append(
            SmokeCommandProbe(
                id="aml-move-command",
                method="POST",
                path="/aml/operations/move",
                payload={"sourceSlot": -1, "targetDrive": -1},
                requires_auth=True,
                expected_statuses=(200, 202, 400, 409, 422),
                covered_by=("test_01_auth.py", "test_03_changer.py"),
            )
        )

    if include_control_plane:
        probes.append(
            SmokeCommandProbe(
                id="openblade-libraries-list",
                method="GET",
                path="/api/libraries",
                payload=None,
                requires_auth=True,
                expected_statuses=(200,),
                covered_by=("test_12_multi_library.py",),
            )
        )

    return probes


def run_smoke_command_matrix(
    client: httpx.Client,
    *,
    username: str,
    password: str,
    include_motion: bool,
    include_control_plane: bool,
) -> dict[str, Any]:
    auth_headers, authentication_mode = negotiate_auth_header(client, username, password)
    probes = smoke_command_probes(
        include_motion=include_motion,
        include_control_plane=include_control_plane,
    )

    results: list[SmokeCommandResult] = []
    for probe in probes:
        headers = auth_headers if probe.requires_auth else {}
        response = client.request(probe.method, probe.path, json=probe.payload, headers=headers)
        results.append(
            SmokeCommandResult(
                id=probe.id,
                method=probe.method,
                path=probe.path,
                status_code=response.status_code,
                matched=response.status_code in probe.expected_statuses,
                expected_statuses=probe.expected_statuses,
                covered_by=probe.covered_by,
            )
        )

    failed = [result for result in results if not result.matched]
    return {
        "authentication_mode": authentication_mode,
        "total": len(results),
        "matched": len(results) - len(failed),
        "failed": len(failed),
        "results": [asdict(result) for result in results],
    }
