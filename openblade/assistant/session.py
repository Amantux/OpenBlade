"""The bounded tool loop, and the confirmation gate in front of tier-1 actions.

One :class:`AssistantSession` is one conversation. ``ask()`` runs at most
``config.max_rounds`` model turns; each turn either produces prose (done) or asks
for tools, which are executed from the read-only registry and fed back.

This module has no write path of its own: it imports no subprocess module, touches
no database session, and can only call handlers that
:func:`openblade.assistant.tools.build_registry` accepted. A tool that raises is
reported back to the model as an error payload rather than aborting the turn —
except registry and read-only violations, which are bugs in the tool layer and must
surface loudly.

Tier-1 setup tools (:mod:`openblade.assistant.setup_tools`) are the one thing the
loop may execute, and they are gated here:

* A tier-1 call never runs on arrival. It is validated against live state, turned
  into a :class:`~openblade.assistant.setup_tools.PendingAction` with a
  human-readable preview, and handed to ``confirm`` — the REPL's ``[y/N]`` prompt.
* No ``confirm`` callback (one-shot mode, or any non-interactive embedding) means no
  tier-1 schemas are offered to the model at all, and any such call is declined.
* A decline is fed back to the model as a result, not an error, so it can adapt.
  Re-proposing the *identical* declined action is answered from the decline cache
  without asking the operator again — one refusal is an answer, two is nagging.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from openblade.assistant.config import AssistantConfig
from openblade.assistant.errors import (
    AssistantError,
    AssistantLoopLimitError,
    SetupRefusedError,
    ToolNotFoundError,
)
from openblade.assistant.prompts import system_message
from openblade.assistant.provider import ChatReply, OllamaClient, ToolCall
from openblade.assistant.setup_facade import SetupFacade
from openblade.assistant.setup_tools import (
    ConfirmCallback,
    PendingAction,
    SetupToolRegistry,
    log_action,
)
from openblade.assistant.tools import ToolContext, ToolRegistry, render_result

ToolObserver = Callable[[str, dict[str, Any]], None]

# Maximum tool calls honoured from a single model reply.
MAX_CALLS_PER_ROUND = 8

# How many times the operator is asked about one identical action. The first
# proposal asks; a repeat with the same arguments is answered from the decline
# cache. Anything higher is a model nagging a human who already said no.
MAX_CONFIRMATION_PROMPTS_PER_ACTION = 1


def _refusal(exc: SetupRefusedError, *, confirmed: bool) -> str:
    """Serialize a refused setup action for the model.

    ``confirmed`` says whether the operator had already said yes. It is not a
    formality: an action refused *after* a yes is the house rule in action —
    confirmation is not a licence to guess — and the model must say so rather than
    reporting success.
    """
    return render_result(
        {
            "executed": False,
            "status": "refused",
            "code": exc.code,
            "error": str(exc),
            "candidates": list(exc.candidates),
            "confirmedByOperator": confirmed,
            "guidance": (
                "Nothing was changed. Ask the operator which of the candidates they "
                "meant; do not pick one for them."
            ),
        }
    )


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
    #: Tier-1 actions the operator confirmed during this turn, in order.
    executed_actions: tuple[str, ...] = ()


@dataclass
class AssistantSession:
    """A conversation with the operator assistant."""

    client: OllamaClient
    registry: ToolRegistry
    context: ToolContext
    config: AssistantConfig
    messages: list[dict[str, Any]] = field(default_factory=list)
    #: Tier-1 registry. ``None`` (the default) is read-only mode: the model is never
    #: shown a setup tool, so one-shot use cannot execute anything.
    setup_registry: SetupToolRegistry | None = None
    #: The narrow write facade the tier-1 tools act through.
    setup: SetupFacade | None = None
    #: Asks the operator to confirm one action. Without it, tier 1 is off.
    confirm: ConfirmCallback | None = None
    _declined: dict[str, int] = field(default_factory=dict, init=False, repr=False)
    _executed: list[str] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.messages:
            self.messages.append(system_message(setup_enabled=self.setup_enabled))

    @property
    def setup_enabled(self) -> bool:
        """Tier 1 needs all three parts: a registry, a facade, and a way to ask.

        Missing any one of them means no setup schema is offered to the model, so
        the failure mode of a half-wired session is "read-only", not "unconfirmed
        writes".
        """
        return (
            self.setup_registry is not None
            and self.setup is not None
            and self.confirm is not None
        )

    def reset(self) -> None:
        """Drop the conversation history, keeping the system prompt."""
        self.messages = [system_message(setup_enabled=self.setup_enabled)]
        self._declined.clear()

    # -- tier 1 -------------------------------------------------------------

    def _decline(self, action: PendingAction, reason: str, *, repeated: bool = False) -> str:
        self._declined[action.key] = self._declined.get(action.key, 0) + 1
        log_action(action, outcome="declined", detail={"reason": reason})
        return render_result(
            {
                "executed": False,
                "status": "declined_by_operator",
                "action": action.tool,
                "preview": action.preview,
                "reason": reason,
                "repeatedProposal": repeated,
                "guidance": (
                    "The operator declined. Do not propose this same action again: "
                    "acknowledge it, ask what they would prefer, or continue with "
                    "something else."
                ),
            }
        )

    def _run_setup_tool(self, call: ToolCall) -> str:
        """Plan, confirm, and only then execute one tier-1 action."""
        registry = self.setup_registry
        facade = self.setup
        confirm = self.confirm
        if registry is None or facade is None or confirm is None:  # pragma: no cover - guarded
            return render_result(
                {
                    "executed": False,
                    "status": "unavailable",
                    "error": (
                        "Setup actions need the interactive REPL, where the operator "
                        "can confirm them. Propose the command instead."
                    ),
                }
            )

        try:
            action = registry.plan(call.name, facade, call.arguments)
        except SetupRefusedError as exc:
            # Ambiguity refuses BEFORE the operator is asked: a confirmation
            # prompt for an action that cannot name its target is worse than none.
            return _refusal(exc, confirmed=False)

        if self._declined.get(action.key, 0) >= MAX_CONFIRMATION_PROMPTS_PER_ACTION:
            return self._decline(
                action, "the operator already declined this exact action", repeated=True
            )

        try:
            approved = bool(confirm(action))
        except (EOFError, KeyboardInterrupt):
            # Ctrl-C / Ctrl-D at the prompt is a no, not a crash and never a yes.
            approved = False

        if not approved:
            return self._decline(action, "the operator answered no")

        try:
            result = registry.perform(action, facade)
        except SetupRefusedError as exc:
            # Re-validated inside the write path. A yes cannot buy a guess.
            log_action(action, outcome="refused", detail={"code": exc.code})
            return _refusal(exc, confirmed=True)
        except Exception as exc:  # noqa: BLE001 - curated below, never echoed raw
            # A database error here must not take the REPL down mid-conversation,
            # and its text must not reach the model: a DSN can appear in it.
            log_action(action, outcome="error", detail={"type": type(exc).__name__})
            return render_result(
                {
                    "executed": False,
                    "status": "error",
                    "action": action.tool,
                    "error": (
                        f"{action.tool} was confirmed but could not be completed "
                        f"({type(exc).__name__}). Nothing further was attempted."
                    ),
                }
            )
        self._executed.append(action.tool)
        return render_result({"executed": True, "action": action.tool, "result": result})

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
            # ``setup_enabled`` is checked first, so a tier-1 name in read-only mode
            # falls through to the read registry and comes back as "no such tool".
            if self.setup_enabled and self.setup_registry is not None and call.name in self.setup_registry:
                content = self._run_setup_tool(call)
            else:
                content = self._run_tool(call)
            self.messages.append({"role": "tool", "name": call.name, "content": content})

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

    def _schemas(self) -> list[dict[str, Any]]:
        """Tool definitions offered to the model this turn.

        Tier-1 schemas are added only when the session can actually confirm an
        action. In one-shot mode the model is not told the setup tools exist, so it
        proposes commands instead of asking for an execution that cannot happen.
        """
        schemas = self.registry.schemas()
        if self.setup_enabled and self.setup_registry is not None:
            schemas.extend(self.setup_registry.schemas())
        return schemas

    def _ask(self, question: str, on_tool: ToolObserver | None) -> AssistantTurn:
        # Drop cached rows first, so this turn sees what other processes committed.
        self.context.refresh()
        self.messages.append({"role": "user", "content": question})
        schemas: Sequence[dict[str, Any]] = self._schemas()
        used: list[str] = []
        self._executed.clear()

        for round_number in range(1, self.config.max_rounds + 1):
            reply = self.client.chat(self.messages, schemas)
            if not reply.wants_tools:
                self.messages.append({"role": "assistant", "content": reply.content})
                return AssistantTurn(
                    reply=reply.content,
                    tool_calls=tuple(used),
                    rounds=round_number,
                    executed_actions=tuple(self._executed),
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
