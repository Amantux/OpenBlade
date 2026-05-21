"""Internal service authentication for controller-only endpoints."""

from __future__ import annotations

import os
import secrets
from typing import Any

from fastapi import Request

SERVICE_TOKEN_ENV_VAR = "OPENBLADE_SERVICE_TOKEN"
DEFAULT_SERVICE_TOKEN = "openblade-controller-dev-token-do-not-expose"
_CONTROLLER_ONLY_ERROR = {
    "code": "FORBIDDEN_CONTROLLER_ONLY",
    "summary": "This endpoint requires internal service credentials",
    "description": "Only the OpenBlade worker/orchestrator service may call this endpoint.",
    "action": "Route your request through the OpenBlade job queue instead.",
    "customCode": None,
}


class ServiceTokenForbiddenError(Exception):
    """Raised when a controller-only endpoint is called without service credentials."""


def get_controller_service_token() -> str:
    return os.environ.get(SERVICE_TOKEN_ENV_VAR, DEFAULT_SERVICE_TOKEN)


def controller_only_error() -> dict[str, Any]:
    return dict(_CONTROLLER_ONLY_ERROR)


async def require_service_token(request: Request) -> None:
    token = request.headers.get("X-Openblade-Service-Token")
    if token is None or not secrets.compare_digest(token, get_controller_service_token()):
        raise ServiceTokenForbiddenError()
