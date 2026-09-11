"""Ollama chat client.

Non-streaming ``POST /api/chat`` with tool definitions. Works against a local
Ollama (``http://localhost:11434``) and against ollama.com cloud, which needs an
API key sent as a Bearer token.

Error handling policy: every failure mode — DNS, connect refused, timeout, non-2xx,
unparseable body, missing fields — is normalized to
:class:`AssistantUpstreamError` with a message written here. Raw socket text and
provider response bodies are never propagated: they leak hostnames and, on a cloud
endpoint, can echo request headers.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from openblade.assistant.config import AssistantConfig
from openblade.assistant.errors import AssistantDisabledError, AssistantUpstreamError

JSONDict = dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    """A tool invocation requested by the model."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChatReply:
    """One assistant turn: prose, tool calls, or both."""

    content: str
    tool_calls: tuple[ToolCall, ...] = ()

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


def _coerce_arguments(raw: Any) -> dict[str, Any]:
    """Normalize tool arguments.

    Ollama returns a JSON object, but smaller models routinely emit a JSON *string*
    instead. Both are accepted; anything else becomes an empty mapping so the tool
    can report its own missing-argument error rather than the loop crashing.
    """
    if isinstance(raw, Mapping):
        return {str(key): value for key, value in raw.items()}
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, Mapping):
            return {str(key): value for key, value in parsed.items()}
    return {}


def _parse_tool_calls(raw_calls: Any) -> tuple[ToolCall, ...]:
    if not isinstance(raw_calls, list):
        return ()
    calls: list[ToolCall] = []
    for entry in raw_calls:
        if not isinstance(entry, Mapping):
            continue
        function = entry.get("function")
        if not isinstance(function, Mapping):
            continue
        name = function.get("name")
        if not isinstance(name, str) or not name:
            continue
        calls.append(ToolCall(name=name, arguments=_coerce_arguments(function.get("arguments"))))
    return tuple(calls)


class OllamaClient:
    """Minimal, synchronous Ollama chat client."""

    def __init__(self, config: AssistantConfig, *, client: httpx.Client | None = None) -> None:
        if config.base_url is None:
            raise AssistantDisabledError("OPENBLADE_OLLAMA_URL is not set")
        self._config = config
        self._base_url = config.base_url
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=config.timeout_seconds)

    @property
    def model(self) -> str:
        return self._config.model

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        return headers

    def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]] = (),
    ) -> ChatReply:
        payload: JSONDict = {
            "model": self._config.model,
            "messages": [dict(message) for message in messages],
            "stream": False,
        }
        if tools:
            payload["tools"] = [dict(tool) for tool in tools]

        try:
            response = self._client.post(
                f"{self._base_url}/api/chat",
                json=payload,
                headers=self._headers(),
                timeout=self._config.timeout_seconds,
            )
        except httpx.TimeoutException:
            raise AssistantUpstreamError(
                "The Ollama endpoint did not respond in time. It may be loading the "
                f"model {self._config.model!r}; retry, or raise OPENBLADE_OLLAMA_TIMEOUT."
            ) from None
        except (httpx.InvalidURL, httpx.UnsupportedProtocol):
            # InvalidURL is NOT an httpx.HTTPError, so without this branch a
            # scheme-less OPENBLADE_OLLAMA_URL escapes as a raw traceback past
            # every `except AssistantError` handler in the CLI.
            raise AssistantUpstreamError(
                "OPENBLADE_OLLAMA_URL is not a usable URL. It needs a scheme, "
                "for example http://localhost:11434 or https://ollama.com."
            ) from None
        except httpx.HTTPError:
            raise AssistantUpstreamError(
                "Could not reach the Ollama endpoint configured in OPENBLADE_OLLAMA_URL. "
                "Check that Ollama is running and the URL is correct."
            ) from None

        self._raise_for_status(response)

        try:
            body = response.json()
        except ValueError:
            raise AssistantUpstreamError(
                "The Ollama endpoint returned a response that was not JSON. "
                "Check that OPENBLADE_OLLAMA_URL points at an Ollama server."
            ) from None
        return self._parse_body(body)

    def _raise_for_status(self, response: httpx.Response) -> None:
        status = response.status_code
        if status < 400:
            return
        if status in (401, 403):
            raise AssistantUpstreamError(
                "The Ollama endpoint rejected the credentials. Set or correct "
                "OPENBLADE_OLLAMA_API_KEY (required for ollama.com cloud models)."
            )
        if status == 404:
            raise AssistantUpstreamError(
                f"The Ollama endpoint does not have the model {self._config.model!r}. "
                f"Pull it (`ollama pull {self._config.model}`) or set OPENBLADE_OLLAMA_MODEL."
            )
        if status == 429:
            raise AssistantUpstreamError(
                "The Ollama endpoint is rate limiting requests. Wait and retry."
            )
        if status >= 500:
            raise AssistantUpstreamError(
                "The Ollama endpoint reported a server error. Check its logs; the "
                "assistant does not retry automatically."
            )
        raise AssistantUpstreamError(
            f"The Ollama endpoint rejected the request (HTTP {status}). "
            "Check OPENBLADE_OLLAMA_URL and OPENBLADE_OLLAMA_MODEL."
        )

    def _parse_body(self, body: Any) -> ChatReply:
        if not isinstance(body, Mapping):
            raise AssistantUpstreamError(
                "The Ollama endpoint returned an unexpected response shape."
            )
        if "error" in body:
            raise AssistantUpstreamError(
                "The Ollama endpoint reported an error for this request. Verify the "
                f"model {self._config.model!r} exists and supports tool calling."
            )
        message = body.get("message")
        if not isinstance(message, Mapping):
            raise AssistantUpstreamError(
                "The Ollama response contained no assistant message. The model may "
                "not support the /api/chat tool interface."
            )
        content = message.get("content")
        return ChatReply(
            content=content if isinstance(content, str) else "",
            tool_calls=_parse_tool_calls(message.get("tool_calls")),
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> OllamaClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
