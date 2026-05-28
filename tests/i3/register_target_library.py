"""Register or update a real hardware target in OpenBlade library instances."""

from __future__ import annotations

import argparse
import base64
import json
from typing import Any

import httpx


def _extract_token(payload: dict[str, Any]) -> str | None:
    for key in ("token", "access_token", "sessionToken"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _basic_auth_header(username: str, password: str) -> dict[str, str]:
    encoded = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    return {"Authorization": f"Basic {encoded}"}


def _negotiate_auth_headers(client: httpx.Client, username: str, password: str) -> dict[str, str]:
    login_attempts: list[tuple[str, dict[str, str]]] = [
        ("/aml/auth/login", {"username": username, "password": password}),
        ("/aml/users/login", {"name": username, "password": password}),
    ]
    for endpoint, payload in login_attempts:
        response = client.post(endpoint, json=payload)
        if response.status_code != 200:
            continue
        token = _extract_token(response.json())
        if token:
            return {"Authorization": f"Bearer {token}"}
    return _basic_auth_header(username, password)


def _find_existing_library(
    libraries: list[dict[str, Any]],
    *,
    target_name: str,
    target_url: str,
    target_serial: str | None,
) -> dict[str, Any] | None:
    for library in libraries:
        if target_serial and library.get("serial_number") == target_serial:
            return library
        if library.get("emulator_url") == target_url:
            return library
        if library.get("name") == target_name:
            return library
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openblade-url", required=True)
    parser.add_argument("--target-aml-url", required=True)
    parser.add_argument("--target-name", required=True)
    parser.add_argument("--target-model", required=True)
    parser.add_argument("--target-serial", default="")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    args = parser.parse_args()

    base_url = args.openblade_url.rstrip("/")
    target_serial = args.target_serial.strip() or None

    payload = {
        "name": args.target_name.strip(),
        "aml_url": args.target_aml_url.strip(),
        "serial_number": target_serial,
        "model": args.target_model.strip(),
        "enabled": True,
        "role": "primary",
        "sort_order": 0,
    }

    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        auth_headers = _negotiate_auth_headers(client, args.username, args.password)

        list_response = client.get("/api/libraries", headers=auth_headers)
        list_response.raise_for_status()
        data = list_response.json()
        libraries = data if isinstance(data, list) else list(data.get("libraries") or [])

        existing = _find_existing_library(
            libraries,
            target_name=payload["name"],
            target_url=payload["aml_url"],
            target_serial=target_serial,
        )

        if existing is None:
            response = client.post("/api/libraries", headers=auth_headers, json=payload)
            response.raise_for_status()
            result = {"action": "created", "library": response.json()}
        else:
            library_id = int(existing["id"])
            response = client.put(f"/api/libraries/{library_id}", headers=auth_headers, json=payload)
            response.raise_for_status()
            result = {"action": "updated", "library": response.json()}

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
