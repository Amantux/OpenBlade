"""test_12_multi_library.py — Multi-library routing via X-OpenBlade-Library-Id header."""
from __future__ import annotations

import pytest
import httpx

pytestmark = pytest.mark.i3


class TestLibraryEndpoints:
    def test_libraries_list_is_reachable(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/api/libraries", headers=auth_headers)
        assert resp.status_code == 200

    def test_libraries_list_is_not_empty(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/api/libraries", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        libraries = data if isinstance(data, list) else data.get("libraries") or []
        assert len(libraries) >= 1, "At least one library must be configured"

    def test_library_has_required_fields(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/api/libraries", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        libraries = data if isinstance(data, list) else data.get("libraries") or []
        for lib in libraries:
            assert lib.get("id") is not None, f"Library missing id: {lib}"
            assert lib.get("name") is not None, f"Library missing name: {lib}"


class TestLibraryHeaderRouting:
    def test_library_id_header_is_accepted(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        """AML requests with X-OpenBlade-Library-Id header should be accepted."""
        headers = {**auth_headers, "X-OpenBlade-Library-Id": "1"}
        resp = i3_client.get("/aml/library", headers=headers)
        assert resp.status_code in (200, 207), f"Header routing failed: {resp.status_code}"

    def test_different_library_headers_both_accepted(
        self, i3_client: httpx.Client, auth_headers: dict[str, str]
    ) -> None:
        """Both library 1 and library 2 headers should be accepted without error."""
        for lib_id in ("1", "2"):
            headers = {**auth_headers, "X-OpenBlade-Library-Id": lib_id}
            resp = i3_client.get("/aml/library", headers=headers)
            assert resp.status_code in (200, 207), (
                f"Library {lib_id} header rejected: {resp.status_code}"
            )

    def test_invalid_library_id_header_handled_gracefully(
        self, i3_client: httpx.Client, auth_headers: dict[str, str]
    ) -> None:
        """An invalid library ID should not cause a 500 error."""
        headers = {**auth_headers, "X-OpenBlade-Library-Id": "99999"}
        resp = i3_client.get("/aml/library", headers=headers)
        assert resp.status_code in (200, 207, 400, 404), (
            f"Invalid library header caused server error: {resp.status_code}"
        )


class TestLibraryCRUD:
    def test_create_and_delete_library(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        # Create
        create_resp = i3_client.post(
            "/api/libraries",
            headers=auth_headers,
            json={
                "name": "i3-test-library",
                "aml_url": "http://localhost:8082",
                "role": "secondary",
                "enabled": True,
            },
        )
        assert create_resp.status_code in (200, 201), f"Create failed: {create_resp.status_code}"
        lib_id = create_resp.json().get("id")
        if not lib_id:
            pytest.skip("Created library has no ID")

        # Delete
        del_resp = i3_client.delete(f"/api/libraries/{lib_id}", headers=auth_headers)
        assert del_resp.status_code in (200, 204), f"Delete failed: {del_resp.status_code}"

    def test_cannot_delete_last_library(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/api/libraries", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        libraries = data if isinstance(data, list) else data.get("libraries") or []
        if len(libraries) != 1:
            pytest.skip("Need exactly one library to test delete-last guard")
        lib_id = libraries[0]["id"]
        del_resp = i3_client.delete(f"/api/libraries/{lib_id}", headers=auth_headers)
        assert del_resp.status_code in (400, 409, 422), (
            f"Should not be able to delete the last library: {del_resp.status_code}"
        )
