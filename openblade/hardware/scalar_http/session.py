"""Cookie-session transport for a Quantum AML Web Services endpoint.

Wraps an injected ``httpx.Client`` so tests can pass a FastAPI ``TestClient``
(in-process against the OpenBlade emulator) and production can pass a networked
``httpx.Client`` pointed at ``https://<library>/aml``. The real i3 authenticates
with ``POST /aml/users/login`` and returns a session cookie that must accompany
every subsequent request (Web Services Guide Rev D, section 2.5); the cookie is
persisted automatically by the client's cookie jar. A 401 (expired session)
triggers a single transparent re-login and retry.
"""

from __future__ import annotations

from typing import Any

import httpx

from openblade.hardware.scalar_http.errors import ScalarHttpError

_JSON_HEADERS = {"Accept": "application/json"}


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        parsed = response.json()
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


class ScalarHttpSession:
    """Authenticated request helper for the AML Web Services surface."""

    def __init__(
        self,
        client: httpx.Client,
        *,
        username: str,
        password: str,
        login_path: str = "/aml/users/login",
    ) -> None:
        self._client = client
        self._username = username
        self._password = password
        self._login_path = login_path
        self._token: str | None = None
        self._logged_in = False

    def login(self) -> None:
        """Establish a session. Raises ``ScalarHttpError`` on failure.

        The AML login accepts a JSON body and aliases ``username`` to ``name``; the
        session cookie lands in the client cookie jar and authenticates every
        subsequent request. The emulator also returns a token in the body — retained
        for diagnostics only, NOT sent as a Bearer header (see ``_headers``).
        """
        response = self._client.post(
            self._login_path,
            json={"name": self._username, "password": self._password},
            headers=_JSON_HEADERS,
        )
        if response.status_code != 200:
            raise ScalarHttpError.from_response(response, action="login")
        token = _safe_json(response).get("token")
        self._token = str(token) if token else None
        self._logged_in = True

    def _headers(self) -> dict[str, str]:
        # Cookie-session only, to match the real i3: authentication rides on the
        # ``sessionID`` cookie persisted in the client's cookie jar from login. We do
        # NOT send an ``Authorization: Bearer`` header — that is an emulator-only
        # artifact the appliance does not accept, and relying on it would let the
        # client "work" against the emulator while diverging from the real contract.
        return dict(_JSON_HEADERS)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> httpx.Response:
        """Issue an authenticated request, re-logging in once on a 401."""
        if not self._logged_in:
            self.login()
        response = self._client.request(
            method, path, params=params, json=json, headers=self._headers()
        )
        if response.status_code == 401:
            self.login()
            response = self._client.request(
                method, path, params=params, json=json, headers=self._headers()
            )
        return response

    def get_json(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.request("GET", path, params=params)
        if response.status_code >= 400:
            raise ScalarHttpError.from_response(response, action=f"GET {path}")
        return _safe_json(response)

    def post_json(self, path: str, *, json: Any = None) -> dict[str, Any]:
        response = self.request("POST", path, json=json)
        if response.status_code >= 400:
            raise ScalarHttpError.from_response(response, action=f"POST {path}")
        return _safe_json(response)
