"""Bearer-token authentication for the OpenBlade-native REST surface.

Scope is deliberately narrow. This module guards the **OpenBlade-native**
surface only (``/inventory``, ``/jobs``, ``/archive``, ``/restore``, ``/ltfs``,
``/api/*``, ``/docs`` ...). The Quantum AML emulator surface (``/aml/*`` and
``/iblade/*``) is a wire contract exercised by ``tests/i3`` and keeps its own
cookie/session authentication in :mod:`openblade.api.routes_aml_auth` — it is
not touched here.

Configuration (env):

``OPENBLADE_API_TOKEN``
    The bearer token. Unset/empty => auth **disabled** (current behaviour is
    preserved for every existing workflow) with a loud startup warning.
``OPENBLADE_API_TOKEN_FILE``
    Path to a file containing the token. Takes precedence over
    ``OPENBLADE_API_TOKEN`` when both are set. A file mode looser than ``0600``
    produces a warning; an unreadable/empty file is a fatal configuration error
    (fail closed — an operator who asked for auth must never silently get none).

Enforcement lives in exactly ONE chokepoint, :func:`api_auth_middleware`,
registered on the app in :mod:`openblade.api.main`. It is intentionally not a
per-route dependency: a route added tomorrow is protected by default, and a
missed decorator cannot become a shipped hole.
"""

from __future__ import annotations

import logging
import os
import secrets
import stat
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field

from fastapi import Request, Response
from fastapi.responses import JSONResponse

_log = logging.getLogger(__name__)

API_TOKEN_ENV_VAR = "OPENBLADE_API_TOKEN"
API_TOKEN_FILE_ENV_VAR = "OPENBLADE_API_TOKEN_FILE"

#: Path prefixes belonging to the Quantum AML emulator surface. These keep their
#: own session auth and are never gated by the native bearer token.
AML_SURFACE_PREFIXES: tuple[str, ...] = ("/aml", "/iblade")

#: Paths that sit under an AML prefix but are NOT part of the emulator wire
#: contract -- OpenBlade-native features that merely happen to be mounted there.
#: They ARE gated; the prefix is a mount point, not a statement about ownership.
#:
#: /aml/proxy (routes_proxy.py) opens an outbound HTTP connection to an
#: operator-supplied host:port and relays a username/password to it. It is not a
#: matrix endpoint (it 404s in scalar_api_only mode), and the only thing standing
#: in front of it is the AML session -- whose shipped default credential is
#: admin/password. An SSRF-and-credential-relay endpoint behind a default
#: password is exactly what this token layer exists to cover.
NATIVE_PATHS_UNDER_AML_PREFIX: tuple[str, ...] = ("/aml/proxy",)

#: Native paths that stay open even when auth is enabled, so external monitors
#: and container health checks keep working without a credential. Kept as small
#: as possible: liveness/readiness only, no inventory or configuration data.
OPEN_PATHS: frozenset[str] = frozenset({"/health", "/healthz", "/readyz"})

#: Curated 401 body. It never echoes the supplied credential.
UNAUTHORIZED_BODY: dict[str, str] = {
    "error": "Unauthorized",
    "detail": (
        "This endpoint requires a bearer token. Send "
        "'Authorization: Bearer <token>' using the token configured via "
        f"{API_TOKEN_ENV_VAR} or {API_TOKEN_FILE_ENV_VAR}."
    ),
}


class ApiAuthConfigError(RuntimeError):
    """Raised when the API token configuration cannot be honoured."""


@dataclass(frozen=True)
class ApiTokenResolution:
    """The resolved native-API credential and where it came from."""

    token: str | None = None
    source: str = "unset"  # "file" | "env" | "unset"
    path: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def enabled(self) -> bool:
        return self.token is not None


def _read_token_file(path: str) -> tuple[str, tuple[str, ...]]:
    warnings: list[str] = []
    try:
        file_stat = os.stat(path)
    except OSError as exc:
        raise ApiAuthConfigError(
            f"{API_TOKEN_FILE_ENV_VAR} points at {path!r}, which cannot be read "
            f"({exc.strerror}). Fix the path or unset the variable."
        ) from exc

    mode = stat.S_IMODE(file_stat.st_mode)
    if mode & 0o077:
        warnings.append(
            f"API token file {path!r} has mode {mode:04o}, which is looser than 0600. "
            "Run 'chmod 600' on it: any local user can currently read the token."
        )

    try:
        with open(path, encoding="utf-8") as handle:
            raw = handle.read()
    except OSError as exc:
        raise ApiAuthConfigError(
            f"{API_TOKEN_FILE_ENV_VAR} points at {path!r}, which cannot be read "
            f"({exc.strerror}). Fix the path or unset the variable."
        ) from exc

    token = raw.strip()
    if not token:
        raise ApiAuthConfigError(
            f"{API_TOKEN_FILE_ENV_VAR} points at {path!r}, which is empty. "
            "Write the token to it or unset the variable."
        )
    return token, tuple(warnings)


def resolve_api_token(env: Mapping[str, str] | None = None) -> ApiTokenResolution:
    """Resolve the native-API token from ``env``.

    Pure apart from reading the token file named by the environment: it takes no
    global state and performs no side effects (no logging, no caching, no
    directory creation), so tests can resolve many configurations in one
    process.

    Precedence: token file > token env var > unset. An empty string is "unset"
    on both, matching the rest of the project's env handling.
    """
    environ: Mapping[str, str] = os.environ if env is None else env

    token_file = environ.get(API_TOKEN_FILE_ENV_VAR, "").strip()
    inline_token = environ.get(API_TOKEN_ENV_VAR, "").strip()

    if token_file:
        token, warnings = _read_token_file(token_file)
        extra: tuple[str, ...] = ()
        if inline_token:
            extra = (
                f"Both {API_TOKEN_ENV_VAR} and {API_TOKEN_FILE_ENV_VAR} are set; "
                f"the file wins and {API_TOKEN_ENV_VAR} is ignored.",
            )
        return ApiTokenResolution(
            token=token, source="file", path=token_file, warnings=warnings + extra
        )

    if inline_token:
        return ApiTokenResolution(token=inline_token, source="env")

    return ApiTokenResolution()


# ---------------------------------------------------------------------------
# Process-wide resolution (cached; the environment does not change per request)
# ---------------------------------------------------------------------------
_RESOLUTION: ApiTokenResolution | None = None


def get_api_token_resolution() -> ApiTokenResolution:
    """Return the process-wide resolution, resolving on first use."""
    global _RESOLUTION
    if _RESOLUTION is None:
        _RESOLUTION = resolve_api_token()
    return _RESOLUTION


def reset_api_auth_cache() -> None:
    """Drop the cached resolution so the next call re-reads the environment."""
    global _RESOLUTION
    _RESOLUTION = None


def log_api_auth_status() -> None:
    """Emit the startup banner for the native API auth configuration."""
    resolution = get_api_token_resolution()
    for warning in resolution.warnings:
        _log.warning("%s", warning)
    if not resolution.enabled:
        _log.warning(
            "!!! NATIVE API AUTHENTICATION IS DISABLED !!! Every OpenBlade-native "
            "endpoint (including /jobs, /ltfs/format and /api/test-runner) is "
            "reachable by anyone who can reach this port. Set %s or %s to require "
            "'Authorization: Bearer <token>'.",
            API_TOKEN_ENV_VAR,
            API_TOKEN_FILE_ENV_VAR,
        )
        return
    if resolution.source == "file":
        _log.info(
            "Native API authentication is ENABLED (token loaded from %s=%s).",
            API_TOKEN_FILE_ENV_VAR,
            _scrub(resolution.path or ""),
        )
    else:
        _log.info("Native API authentication is ENABLED (token loaded from %s).", API_TOKEN_ENV_VAR)


# ---------------------------------------------------------------------------
# Path classification
# ---------------------------------------------------------------------------
def _has_prefix(path: str, prefix: str) -> bool:
    # Segment-aware so "/amlfoo" is not mistaken for the "/aml" surface.
    return path == prefix or path.startswith(prefix + "/")


def strip_forwarded_prefix(path: str, forwarded_prefix: str) -> str:
    """Remove a reverse-proxy mount prefix from ``path`` for classification.

    Only used when the ASGI server did not put the mount prefix in
    ``scope["root_path"]`` -- see :func:`classification_path`, which prefers the
    scope. ``apply_forwarded_root_path`` in ``main`` sets ``root_path`` from
    ``X-Forwarded-Prefix``, but it runs *inside* this middleware, so at our point
    in the stack the header is still the only signal in that deployment.

    The header is client-controlled; stripping can only ever *shorten* the path,
    and no native route is mounted at a path whose tail is ``/aml`` or
    ``/iblade``, so this cannot be used to reach a native route while the
    classifier sees an AML one. ``test_no_prefix_strip_turns_a_gated_path_into_
    an_exempt_one`` pins that invariant against the real route table rather than
    leaving it as an argument in a docstring.
    """
    prefix = forwarded_prefix.strip().rstrip("/")
    if not prefix.startswith("/") or not _has_prefix(path, prefix):
        return path
    return path[len(prefix) :] or "/"


def classification_path(scope: Mapping[str, object], forwarded_prefix: str = "") -> str:
    """The path to classify: exactly what Starlette's router will match on.

    Starlette routes on ``scope["path"]`` minus ``scope["root_path"]``. Deriving
    the mount prefix any other way lets the classifier and the router disagree --
    under ``uvicorn --root-path /ob`` the old header-only version classified
    ``/ob/health`` and ``/ob/aml/...`` as gated native paths, which fails closed
    but silently breaks the health exemption and the AML wire contract.
    """
    raw_path = scope.get("path")
    path = raw_path if isinstance(raw_path, str) and raw_path else "/"
    raw_root = scope.get("root_path")
    root_path = raw_root if isinstance(raw_root, str) else ""
    if root_path and _has_prefix(path, root_path.rstrip("/")):
        return path[len(root_path.rstrip("/")) :] or "/"
    return strip_forwarded_prefix(path, forwarded_prefix)


def is_aml_surface_path(path: str) -> bool:
    """True for the Quantum AML emulator surface, which this module never gates.

    The native exceptions are checked first: a path under ``/aml`` is only the
    emulator's if it is not one of OpenBlade's own routes mounted there.
    """
    if any(_has_prefix(path, prefix) for prefix in NATIVE_PATHS_UNDER_AML_PREFIX):
        return False
    return any(_has_prefix(path, prefix) for prefix in AML_SURFACE_PREFIXES)


def is_open_path(path: str) -> bool:
    """True for native paths that stay reachable without a credential."""
    normalized = path.rstrip("/") or "/"
    return normalized in OPEN_PATHS


def requires_api_token(path: str) -> bool:
    """True when ``path`` is a native route gated by the bearer token."""
    return not is_aml_surface_path(path) and not is_open_path(path)


# ---------------------------------------------------------------------------
# Request handling
# ---------------------------------------------------------------------------
def extract_bearer_token(authorization: str | None) -> str | None:
    """Return the token from an ``Authorization: Bearer <token>`` header."""
    if not authorization:
        return None
    scheme, separator, value = authorization.partition(" ")
    if not separator or scheme.strip().lower() != "bearer":
        return None
    token = value.strip()
    return token or None


def _scrub(value: str) -> str:
    """Strip CR/LF (and other control characters) before logging attacker input."""
    return "".join(char if char.isprintable() else "?" for char in value)[:200]


def tokens_match(supplied: str, expected: str) -> bool:
    """Constant-time credential comparison (bytes, so non-ASCII tokens work)."""
    return secrets.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


def unauthorized_response() -> JSONResponse:
    """The curated 401 — never contains the supplied token.

    Carries the security headers itself. ``add_security_headers`` in ``main`` is
    registered earlier and is therefore *inside* this middleware, so it never
    sees a short-circuited 401; without these the rejection would be the only
    response in the app missing nosniff/X-Frame-Options.
    """
    return JSONResponse(
        status_code=401,
        content=dict(UNAUTHORIZED_BODY),
        headers={
            "WWW-Authenticate": "Bearer",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "strict-origin-when-cross-origin",
        },
    )


def _client_description(request: Request) -> str:
    client = request.client
    host = client.host if client is not None else "unknown"
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return f"{_scrub(host)} (x-forwarded-for: {_scrub(forwarded)})"
    return _scrub(host)


async def api_auth_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """The single chokepoint guarding every OpenBlade-native route.

    Registered in ``openblade.api.main`` with ``@app.middleware("http")``.
    """
    if request.method.upper() == "OPTIONS":
        # CORS preflight carries no credentials by design.
        return await call_next(request)

    # One place decides "is auth on?", so there is no second, unreachable
    # fail-open branch further down that a later edit could make reachable.
    expected = get_api_token_resolution().token
    if expected is None:
        return await call_next(request)

    path = classification_path(request.scope, request.headers.get("x-forwarded-prefix", ""))
    if not requires_api_token(path):
        return await call_next(request)

    supplied = extract_bearer_token(request.headers.get("authorization"))
    if supplied is None or not tokens_match(supplied, expected):
        reason = "missing" if supplied is None else "invalid"
        _log.warning(
            "Rejected unauthenticated native API request: %s %s from %s (%s bearer token)",
            _scrub(request.method),
            _scrub(request.url.path),
            _client_description(request),
            reason,
        )
        return unauthorized_response()

    return await call_next(request)
