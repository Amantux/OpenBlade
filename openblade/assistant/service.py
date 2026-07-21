"""Chat orchestration for the OpenBlade assistant (grounding + tool loop)."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from openblade.assistant.client import AssistantClientError, ChatClient
from openblade.assistant.config import AssistantConfig
from openblade.assistant.tools import build_grounding_snapshot, execute_tool, tool_specs

_ALLOWED_ROLES = {"system", "user", "assistant", "tool"}


@dataclass
class AssistantReply:
    content: str
    tools_used: list[str] = field(default_factory=list)
    model: str = ""


class AssistantService:
    def __init__(
        self,
        config: AssistantConfig,
        client: ChatClient,
        *,
        snapshot_fn: Callable[[], dict[str, Any]] = build_grounding_snapshot,
        tool_specs_fn: Callable[[], list[dict[str, Any]]] = tool_specs,
        execute_fn: Callable[[str], dict[str, Any]] = execute_tool,
    ) -> None:
        self._config = config
        self._client = client
        self._snapshot_fn = snapshot_fn
        self._tool_specs_fn = tool_specs_fn
        self._execute_fn = execute_fn

    def _system_content(self) -> str:
        try:
            snapshot = json.dumps(self._snapshot_fn(), default=str)
        except Exception:  # noqa: BLE001 - grounding is best-effort
            snapshot = "{}"
        return (
            f"{self._config.system_prompt}\n\n"
            f"Live OpenBlade state snapshot (read-only, may be stale):\n{snapshot}\n"
            "Call the read-only tools for anything not in the snapshot."
        )

    @staticmethod
    def _sanitize(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cleaned: list[dict[str, Any]] = []
        for message in messages:
            role = str(message.get("role", "")).strip()
            content = message.get("content")
            if role in _ALLOWED_ROLES and role != "system" and isinstance(content, str):
                cleaned.append({"role": role, "content": content})
        return cleaned

    def chat(self, messages: list[dict[str, Any]]) -> AssistantReply:
        conversation: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_content()},
            *self._sanitize(messages),
        ]
        if not any(m["role"] == "user" for m in conversation):
            raise ValueError("at least one user message is required")
        try:
            return self._run(conversation, self._tool_specs_fn())
        except AssistantClientError as error:
            # Some OpenAI-compatible endpoints reject the `tools` field; the
            # grounding snapshot still lets the model answer, so retry tool-free.
            if error.status_code == 400:
                return self._run(conversation, None)
            raise

    def _run(
        self, conversation: list[dict[str, Any]], tools: list[dict[str, Any]] | None
    ) -> AssistantReply:
        used: list[str] = []
        last_model = ""
        for _ in range(self._config.max_tool_iterations):
            response = self._client.chat_completions(
                model=self._config.model, messages=conversation, tools=tools
            )
            last_model = str(response.get("model") or self._config.model)
            message = _extract_message(response)
            tool_calls = message.get("tool_calls") if tools else None
            if isinstance(tool_calls, list) and tool_calls:
                conversation.append(message)
                for call in tool_calls:
                    name = str(((call or {}).get("function") or {}).get("name") or "")
                    result = self._execute_fn(name)
                    used.append(name)
                    conversation.append(
                        {
                            "role": "tool",
                            "tool_call_id": str((call or {}).get("id") or name),
                            "name": name,
                            "content": json.dumps(result, default=str),
                        }
                    )
                continue
            return AssistantReply(
                content=str(message.get("content") or "").strip(),
                tools_used=used,
                model=last_model,
            )
        return AssistantReply(
            content="I gathered system data but reached the tool-call limit before answering. "
            "Please narrow the question.",
            tools_used=used,
            model=last_model,
        )


def _extract_message(response: dict[str, Any]) -> dict[str, Any]:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise AssistantClientError("Endpoint returned no choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise AssistantClientError("Endpoint returned a malformed message")
    return message
