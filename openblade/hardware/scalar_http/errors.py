"""Error types for the Scalar i3 HTTP Web Services client."""

from __future__ import annotations

from typing import Any

import httpx

from openblade.domain.errors import OpenBladeError


class ScalarHttpError(OpenBladeError):
    """A Quantum AML Web Services request failed.

    Carries the HTTP status plus the AML ``WSResultCode`` fields (``code`` and the
    numeric ``customCode``) when the response body provides them, so callers can
    map robotics faults (e.g. customCode 11024 "robotics not ready") to behaviour.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        code: str | None = None,
        custom_code: int | None = None,
        action: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.custom_code = custom_code
        self.action = action

    @classmethod
    def from_response(cls, response: httpx.Response, *, action: str) -> ScalarHttpError:
        """Build an error from a non-2xx AML response, tolerating XML/plain bodies."""
        body: dict[str, Any] = {}
        try:
            parsed = response.json()
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            body = parsed

        code = body.get("code")
        custom_code_raw = body.get("customCode")
        custom_code = custom_code_raw if isinstance(custom_code_raw, int) else None
        description = (
            body.get("description")
            or body.get("detail")
            or body.get("summary")
            or response.text[:200]
        )
        message = f"{action} failed [{response.status_code}]: {description}".strip()
        return cls(
            message,
            status_code=response.status_code,
            code=str(code) if code is not None else None,
            custom_code=custom_code,
            action=action,
        )
