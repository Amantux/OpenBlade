"""The bounded tool loop.

One :class:`AssistantSession` is one conversation. ``ask()`` runs at most
``config.max_rounds`` model turns; each turn either produces prose (done) or asks
for tools, which are executed from the read-only registry and fed back.

This module has no write path of its own: it imports no subprocess module, touches
no database session, and can only call handlers that
:func:`openblade.assistant.tools.build_registry` accepted. A tool that raises is
reported back to the model as an error payload rather than aborting the turn —
except registry and read-only violations, which are bugs in the tool layer and must
surface loudly.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from openblade.assistant.config import AssistantConfig
from openblade.assistant.errors import (
    AssistantError,
    AssistantLoopLimitError,
    ToolNotFoundError,
)
from openblade.assistant.prompts import system_message
from openblade.assistant.provider import ChatReply, OllamaClient, ToolCall
from openblade.assistant.tools import ToolContext, ToolRegistry, render_result

ToolObserver = Callable[[str, dict[str, Any]], None]

# Maximum tool calls honoured from a single model reply.
MAX_CALLS_PER_ROUND = 8


def _cap_tool_calls(reply: ChatReply) -> ChatReply:
    if len(reply.tool_calls) <= MAX_CALLS_PER_ROUND:
        return reply
    return ChatReply(content=reply.content, tool_calls=reply.tool_calls[:MAX_CALLS_PER_ROUND])


@dataclass(frozen=True)
class AssistantTurn:
    """The result of one ``ask()``."""

    reply: str
    tool_calls: tuple[str, ...] = ()
    rounds: int = 1


@dataclass
class AssistantSession:
    """A conversation with the operator assistant."""

    client: OllamaClient
    registry: ToolRegistry
    context: ToolContext
    config: AssistantConfig
    messages: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.messages:
            self.messages.append(system_message())

    def reset(self) -> None:
        """Drop the conversation history, keeping the system prompt."""
        self.messages = [system_message()]

    def _run_tool(self, call: ToolCall) -> str:
        """Execute one read-only tool and serialize its result for the model."""
        try:
            result = self.registry.call(call.name, self.context, call.arguments)
        except ToolNotFoundError:
            return render_result(
                {
                    "error": (
                        f"There is no tool named {call.name!r}. The assistant is "
                        "read-only; available tools are: " + ", ".join(sorted(self.registry.names))
                    )
                }
            )
        except AssistantError:
            # Registry / read-only violations are defects in the tool layer, not
            # something to hand back to the model as if it were data.
            raise
        except Exception as exc:  # noqa: BLE001 - curated below, never echoed raw
            return render_result(
                {"error": f"{call.name} could not be completed ({type(exc).__name__})."}
            )
        return render_result(result)

    def _append_tool_round(self, reply: ChatReply, observer: ToolObserver | None) -> None:
        self.messages.append(
            {
                "role": "assistant",
                "content": reply.content,
                "tool_calls": [
                    {"function": {"name": call.name, "arguments": call.arguments}}
                    for call in reply.tool_calls
                ],
            }
        )
        for call in reply.tool_calls:
            if observer is not None:
                observer(call.name, dict(call.arguments))
            self.messages.append(
                {"role": "tool", "name": call.name, "content": self._run_tool(call)}
            )

    def ask(self, question: str, *, on_tool: ToolObserver | None = None) -> AssistantTurn:
        """Ask a question and run the tool loop until the model answers in prose.

        On failure the transcript is rewound to where this turn started. Otherwise a
        turn that died mid-round would leave an assistant message carrying unanswered
        ``tool_calls`` plus a dangling ``tool`` message, and the operator's next
        question would be appended onto that malformed history with no sign of it.
        """
        checkpoint = len(self.messages)
        try:
            return self._ask(question, on_tool)
        except AssistantError:
            del self.messages[checkpoint:]
            raise

    def _ask(self, question: str, on_tool: ToolObserver | None) -> AssistantTurn:
        # Drop cached rows first, so this turn sees what other processes committed.
        self.context.refresh()
        self.messages.append({"role": "user", "content": question})
        schemas: Sequence[dict[str, Any]] = self.registry.schemas()
        used: list[str] = []

        for round_number in range(1, self.config.max_rounds + 1):
            reply = self.client.chat(self.messages, schemas)
            if not reply.wants_tools:
                self.messages.append({"role": "assistant", "content": reply.content})
                return AssistantTurn(
                    reply=reply.content, tool_calls=tuple(used), rounds=round_number
                )
            # A confused model can emit hundreds of calls in one reply. Rounds are
            # bounded; without this, the work inside one round is not.
            reply = _cap_tool_calls(reply)
            used.extend(call.name for call in reply.tool_calls)
            self._append_tool_round(reply, on_tool)

        raise AssistantLoopLimitError(
            f"The assistant kept requesting tools past {self.config.max_rounds} rounds "
            "without producing an answer. Try a narrower question, or raise "
            "OPENBLADE_ASSISTANT_MAX_ROUNDS."
        )
