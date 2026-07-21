"""Tests for the OpenBlade AI assistant (OpenAI-compatible helper)."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from openblade.api import aml_state
from openblade.api.main import app
from openblade.api.routes_assistant import get_assistant_service
from openblade.assistant.client import AssistantClientError
from openblade.assistant.config import AssistantConfig, load_assistant_config
from openblade.assistant.service import AssistantService
from openblade.bootstrap import get_context

_ASSISTANT_ENV = [
    "OPENBLADE_ASSISTANT_BASE_URL",
    "OPENBLADE_ASSISTANT_MODEL",
    "OPENBLADE_ASSISTANT_API_KEY",
    "OPENBLADE_ASSISTANT_ENABLED",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
    "OPENAI_API_KEY",
]


def _config(**overrides: Any) -> AssistantConfig:
    base: dict[str, Any] = {
        "enabled": True,
        "base_url": "http://llm.local/v1",
        "api_key": "",
        "model": "test-model",
        "timeout_seconds": 5.0,
        "temperature": 0.0,
        "max_tool_iterations": 3,
        "system_prompt": "system",
    }
    base.update(overrides)
    return AssistantConfig(**base)


class FakeClient:
    """Scripted OpenAI-compatible client for the service tests."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def chat_completions(
        self, *, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        self.calls.append({"model": model, "messages": messages, "tools": tools})
        return self._responses.pop(0)


def _msg(content: str | None = None, tool_calls: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {"model": "test-model", "choices": [{"message": message}]}


# --- config -----------------------------------------------------------------

def test_assistant_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ASSISTANT_ENV:
        monkeypatch.delenv(name, raising=False)

    config = load_assistant_config()

    assert config.enabled is False  # no explicit setup -> off, never auto-calls OpenAI


def test_assistant_enables_when_api_key_present(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ASSISTANT_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    config = load_assistant_config()

    assert config.enabled is True
    assert config.configured is True
    assert "openai.com" not in config.api_key  # sanity: key isn't the host


# --- service ----------------------------------------------------------------

def test_service_returns_plain_reply() -> None:
    client = FakeClient([_msg("archive is healthy")])
    service = AssistantService(_config(), client, snapshot_fn=lambda: {"ok": True})

    reply = service.chat([{"role": "user", "content": "status?"}])

    assert reply.content == "archive is healthy"
    assert reply.tools_used == []


def test_service_runs_tool_call_loop() -> None:
    tool_call = {"id": "c1", "type": "function", "function": {"name": "get_library_inventory"}}
    client = FakeClient([_msg(None, [tool_call]), _msg("You have 20 slots.")])
    service = AssistantService(
        _config(),
        client,
        snapshot_fn=lambda: {},
        execute_fn=lambda name: {"slots_total": 20},
    )

    reply = service.chat([{"role": "user", "content": "how many slots?"}])

    assert reply.tools_used == ["get_library_inventory"]
    assert "20 slots" in reply.content
    # the tool result was fed back to the model on the second call
    assert any(m.get("role") == "tool" for m in client.calls[1]["messages"])


def test_service_falls_back_when_tools_unsupported() -> None:
    class ToolIntolerantClient:
        def __init__(self) -> None:
            self.saw_tools = False

        def chat_completions(self, *, model: str, messages: list[dict[str, Any]], tools=None):
            if tools is not None:
                self.saw_tools = True
                raise AssistantClientError("tools unsupported", status_code=400)
            return _msg("grounded answer without tools")

    client = ToolIntolerantClient()
    service = AssistantService(_config(), client, snapshot_fn=lambda: {})

    reply = service.chat([{"role": "user", "content": "hi"}])

    assert client.saw_tools is True
    assert reply.content == "grounded answer without tools"


def test_service_requires_a_user_message() -> None:
    service = AssistantService(_config(), FakeClient([]), snapshot_fn=lambda: {})

    with pytest.raises(ValueError):
        service.chat([{"role": "assistant", "content": "hi"}])


# --- routes -----------------------------------------------------------------

@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    for name in _ASSISTANT_ENV:
        monkeypatch.delenv(name, raising=False)
    aml_state.ensure_initialized(get_context().config.db_url, force_reset=True)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_assistant_service, None)


def _login(client: TestClient) -> None:
    assert client.post("/aml/users/login", json={"name": "admin", "password": "password"}).status_code == 200


def test_status_reports_disabled_without_config(client: TestClient) -> None:
    _login(client)

    body = client.get("/assistant/status").json()

    assert body["enabled"] is False
    assert "get_library_inventory" in body["tools"]


def test_chat_returns_503_when_not_configured(client: TestClient) -> None:
    _login(client)

    response = client.post("/assistant/chat", json={"messages": [{"role": "user", "content": "hi"}]})

    assert response.status_code == 503


def test_chat_returns_reply_with_injected_service(client: TestClient) -> None:
    _login(client)
    fake = AssistantService(
        _config(), FakeClient([_msg("Two drives are loaded.")]), snapshot_fn=lambda: {}
    )
    app.dependency_overrides[get_assistant_service] = lambda: fake

    response = client.post(
        "/assistant/chat", json={"messages": [{"role": "user", "content": "drive status?"}]}
    )

    assert response.status_code == 200
    assert response.json()["reply"] == "Two drives are loaded."


def test_chat_requires_auth(client: TestClient) -> None:
    response = client.post("/assistant/chat", json={"messages": [{"role": "user", "content": "hi"}]})

    assert response.status_code == 401
