"""Minimal OpenAI-compatible chat-completions client (httpx, no SDK).

Works against any endpoint that implements POST {base_url}/chat/completions in
the OpenAI format — OpenAI, Ollama, vLLM, LM Studio, LocalAI, etc.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx


class AssistantClientError(Exception):
    """The chat-completions endpoint returned an error or was unreachable."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ChatClient(Protocol):
    """The surface the service depends on (so tests can inject a fake)."""

    def chat_completions(
        self, *, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]: ...


class OpenAICompatibleClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str = "",
        timeout_seconds: float = 60.0,
        temperature: float = 0.2,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._temperature = temperature

    def chat_completions(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": self._temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.post(
                    f"{self._base_url}/chat/completions", json=payload, headers=headers
                )
        except httpx.HTTPError as error:
            raise AssistantClientError(f"Endpoint unreachable: {error}") from error
        if response.status_code >= 400:
            detail = _error_detail(response)
            raise AssistantClientError(
                f"Endpoint returned {response.status_code}: {detail}",
                status_code=response.status_code,
            )
        try:
            return dict(response.json())
        except ValueError as error:
            raise AssistantClientError("Endpoint returned a non-JSON response") from error


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error)
        if error:
            return str(error)
    return str(body)[:200]
