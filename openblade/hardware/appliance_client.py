"""Read-only cookie-session client for the real appliance's AML HTTP API.

Shared by the read-only hardware lane (``tests/hardware/readonly``) and the
day-one preflight tool (``tools/hardware/preflight.py``). Every request passes
through :func:`check_appliance_request`, which refuses anything but a GET or
the login/logout handshake, so neither caller can mutate the rig.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.cookiejar import CookieJar
from typing import Any


class ReadOnlyViolation(RuntimeError):
    """A read-only-lane test attempted an HTTP request or command that could mutate the rig."""


# The only non-GET requests the read-only lane may send: the session handshake.
AUTH_REQUESTS = frozenset({("POST", "/aml/users/login"), ("POST", "/aml/auth/logout")})


def check_appliance_request(method: str, path: str) -> None:
    """Raise ReadOnlyViolation unless ``method path`` is a GET or the login/logout call.

    The method is matched exactly (HTTP methods are case-sensitive), so ``"get"``
    is refused rather than normalised.
    """
    verb = method
    if not path.startswith("/"):
        raise ReadOnlyViolation(f"appliance path must be absolute, got {path!r}")
    if verb == "GET" or (verb, path) in AUTH_REQUESTS:
        return
    raise ReadOnlyViolation(f"read-only lane refuses {verb} {path}")


@dataclass
class ApplianceResponse:
    status: int
    body: Any


@dataclass
class ApplianceClient:
    """Minimal cookie-session HTTP client for the real appliance's AML API."""

    base_url: str
    user: str
    password: str
    insecure: bool = False
    timeout_s: float = 30.0
    _opener: urllib.request.OpenerDirector = field(init=False)

    def __post_init__(self) -> None:
        context = ssl.create_default_context()
        if self.insecure:
            # Opt-in via OPENBLADE_APPLIANCE_INSECURE=true for a rig whose
            # appliance only has its factory self-signed certificate.
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar()),
            urllib.request.HTTPSHandler(context=context),
        )

    def request(self, method: str, path: str, json_body: Any = None) -> ApplianceResponse:
        check_appliance_request(method, path)
        data = None if json_body is None else json.dumps(json_body).encode()
        req = urllib.request.Request(  # noqa: S310 - base URL is operator-set rig config; both callers check the scheme before constructing the client
            self.base_url.rstrip("/") + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with self._opener.open(req, timeout=self.timeout_s) as resp:
                status, raw = resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        try:
            body: Any = json.loads(raw) if raw else None
        except ValueError:
            body = None
        return ApplianceResponse(status=status, body=body)

    def login(self) -> ApplianceResponse:
        return self.request(
            "POST", "/aml/users/login", {"name": self.user, "password": self.password}
        )

    def logout(self) -> ApplianceResponse:
        return self.request("POST", "/aml/auth/logout")
