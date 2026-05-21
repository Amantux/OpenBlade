"""Internal service authentication for controller-only endpoints."""

from __future__ import annotations

import logging
import os
import secrets
from typing import Any

from fastapi import Request

SERVICE_TOKEN_ENV_VAR = "OPENBLADE_SERVICE_TOKEN"
_DEFAULT_SERVICE_TOKEN = "openblade-controller-dev-token-do-not-expose"
_CONTROLLER_ONLY_ERROR = {
    "code": "FORBIDDEN_CONTROLLER_ONLY",
    "summary": "This endpoint requires internal service credentials",
    "description": "Only the OpenBlade worker/orchestrator service may call this endpoint.",
    "action": "Route your request through the OpenBlade job queue instead.",
    "customCode": None,
}

_log = logging.getLogger(__name__)


class ServiceTokenForbiddenError(Exception):
    """Raised when a controller-only endpoint is called without service credentials."""


def get_controller_service_token() -> str:
    token = os.environ.get(SERVICE_TOKEN_ENV_VAR)
    if token:
        return token
    # In production mode, refuse to start with the default insecure token.
    if os.environ.get("OPENBLADE_ENV", "development").lower() == "production":
        raise RuntimeError(
            f"OPENBLADE_ENV=production but {SERVICE_TOKEN_ENV_VAR} is not set. "
            "Set a strong secret token before starting in production."
        )
    _log.warning(
        "Using default insecure service token. "
        "Set %s env var before deploying to production.",
        SERVICE_TOKEN_ENV_VAR,
    )
    return _DEFAULT_SERVICE_TOKEN


def controller_only_error() -> dict[str, Any]:
    return dict(_CONTROLLER_ONLY_ERROR)


async def require_service_token(request: Request) -> None:
    token = request.headers.get("X-Openblade-Service-Token")
    if token is None or not secrets.compare_digest(token, get_controller_service_token()):
        raise ServiceTokenForbiddenError()
