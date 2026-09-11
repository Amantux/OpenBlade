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

Tier-2 media tools (:mod:`openblade.assistant.media_tools`) are gated the same way
and more strongly. The differences, all of them deliberate:

* The confirmation callback returns *what the operator typed*, not a bool, and the
  registry decides whether that satisfies the action's grade. A ``y`` confirms a
  load; only the barcode confirms a format.
* ``perform`` re-verifies the authorization against the action before it calls
  anything, so "a media action cannot run without its strong confirmation" holds
  even if the prompt in the CLI is wrong.
* Archive and restore block for minutes. ``progress`` is called with one line
  before and one after, because a REPL that goes silent for four minutes looks
  hung, and an operator who thinks it is hung reaches for Ctrl-C mid-write.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from openblade.assistant.config import AssistantConfig
from openblade.assistant.errors import (
    AssistantError,
    AssistantLoopLimitError,
    MediaOperationFailedError,
    MediaRefusedError,
    SetupPartialWriteError,
    SetupRefusedError,
    ToolNotFoundError,
)
from openblade.assistant.media_facade import MediaFacade
from openblade.assistant.media_tools import (
    ConfirmationGrade,
    MediaConfirmCallback,
    MediaToolRegistry,
    PendingMediaAction,
    ProgressCallback,
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


def _refusal(exc: SetupRefusedError | MediaRefusedError, *, confirmed: bool) -> str:
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


def _unavailable(tool: str, what: str, exc: Exception) -> str:
    """Report a failure the operator was never asked about. Type name only."""
    return render_result(
        {
            "executed": False,
            "status": "unavailable",
            "action": tool,
            "error": (
                f"{tool} {what} ({type(exc).__name__}); nothing was changed and the "
                "operator was not asked. Report the failure and suggest they check "
                "the catalog."
            ),
        }
    )


def _completion_line(tool: str, result: Mapping[str, Any]) -> str:
    """One operator-facing line naming what actually happened.

    The job id lands here rather than on the start line because the archive and
    restore services create the job inside the same call that runs it — there is no
    id to print until it returns. Saying so here beats printing a placeholder.
    """
    parts = [f"{tool}: done"]
    if result.get("jobId"):
        parts.append(f"job {result['jobId']} {result.get('state', '')}".strip())
    if result.get("filesArchived") is not None:
        parts.append(
            f"{result['filesArchived']}/{result.get('filesExpected')} files, "
            f"{result.get('bytesArchived')} bytes in the catalog"
        )
    if result.get("bytesRestored") is not None:
        verified = "checksum verified" if result.get("checksumVerified") else "NOT verified"
        parts.append(f"{result['bytesRestored']} bytes restored, {verified}")
    if result.get("message"):
        parts.append(str(result["message"]))
    return " — ".join(parts)


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
    #: Tier-2 registry. ``None`` (the default) means media tools are not offered.
    media_registry: MediaToolRegistry | None = None
    #: The narrow media facade the tier-2 tools act through.
    media: MediaFacade | None = None
    #: Asks the operator to confirm one media action, returning what they typed.
    #: Without it, tier 2 is off — which is what makes one-shot mode read-only.
    confirm_media: MediaConfirmCallback | None = None
    #: One line before and after a long synchronous media operation.
    progress: ProgressCallback | None = None
    _declined: dict[str, int] = field(default_factory=dict, init=False, repr=False)
    _executed: list[str] = field(default_factory=list, init=False, repr=False)
    _possibly_partial: list[str] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.messages:
            self.messages.append(self._system_message())

    def _system_message(self) -> dict[str, Any]:
        return system_message(
            setup_enabled=self.setup_enabled, media_enabled=self.media_enabled
        )

    @property
    def setup_enabled(self) -> bool:
        """Tier 1 needs all three parts: a registry, a facade, and a way to ask.

        Missing any one of them means no setup schema is offered to the model, so
        the failure mode of a half-wired session is "read-only", not "unconfirmed
        writes".
        """
        return (
            self.setup_registry is not None and self.setup is not None and self.confirm is not None
        )

    @property
    def media_enabled(self) -> bool:
        """Tier 2 needs all three parts too: a registry, a facade, and a way to ask.

        Same failure mode as tier 1 by construction: a half-wired session offers no
        media schema at all rather than offering one it could not confirm.
        """
        # Tier 1 is required, not merely expected. The tier-2 system prompt
        # describes BOTH tiers, so a media-only session would advertise
        # create_volume_group and then answer that call with "there is no tool
        # named that". Enforcing the implication is cheaper than a fourth prompt,
        # and a session that can confirm a format can certainly confirm a pool.
        return (
            self.setup_enabled
            and self.media_registry is not None
            and self.media is not None
            and self.confirm_media is not None
        )

    def reset(self) -> None:
        """Drop the conversation history, keeping the system prompt."""
        self.messages = [self._system_message()]
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
        except AssistantError:
            # A facade or registry violation is a defect in the tool layer, not
            # data for the model. Same policy as ``_run_tool``.
            raise
        except Exception as exc:  # noqa: BLE001 - curated, never echoed raw
            # ``plan`` reads the live catalog, so a database failure lands here. It
            # must not take the REPL down mid-conversation and its text must not
            # reach the model: a DSN appears in psycopg/SQLAlchemy connect errors.
            return _unavailable(call.name, "could not be checked", exc)

        if self._declined.get(action.key, 0) >= MAX_CONFIRMATION_PROMPTS_PER_ACTION:
            return self._decline(
                action, "the operator already declined this exact action", repeated=True
            )

        try:
            approved = bool(confirm(action))
        except (EOFError, KeyboardInterrupt):
            # Ctrl-C / Ctrl-D at the prompt is a no, not a crash and never a yes.
            approved = False
        except Exception:  # noqa: BLE001 - a broken prompt is a no, not a yes
            # Fail closed: if we cannot establish that the operator agreed, they
            # did not. Recorded as a decline so the model stops proposing it.
            return self._decline(action, "the confirmation prompt failed")

        if not approved:
            return self._decline(action, "the operator answered no")

        try:
            result = registry.perform(action, facade)
        except SetupRefusedError as exc:
            # Re-validated inside the write path. A yes cannot buy a guess.
            log_action(action, outcome="refused", detail={"code": exc.code})
            return _refusal(exc, confirmed=True)
        except SetupPartialWriteError as exc:
            # The repository commits per barcode, so a mid-loop failure leaves real
            # rows behind. Report exactly which ones landed rather than the flat
            # "nothing happened" that would otherwise be a lie in the audit trail.
            log_action(
                action,
                outcome="partial",
                detail={"applied": list(exc.applied), "cause": exc.cause},
            )
            self._executed.append(action.tool)
            return render_result(
                {
                    "executed": False,
                    "status": "partially_applied",
                    "action": action.tool,
                    "applied": list(exc.applied),
                    "error": str(exc),
                    "guidance": (
                        "Tell the operator exactly what did land and that the rest "
                        "did not. Do not retry it yourself."
                    ),
                }
            )
        except AssistantError:
            raise
        except Exception as exc:  # noqa: BLE001 - curated below, never echoed raw
            log_action(action, outcome="error", detail={"type": type(exc).__name__})
            return render_result(
                {
                    "executed": False,
                    "status": "error",
                    "action": action.tool,
                    "error": (
                        f"{action.tool} was confirmed but could not be completed "
                        f"({type(exc).__name__}). Nothing was changed."
                    ),
                }
            )
        self._executed.append(action.tool)
        return render_result({"executed": True, "action": action.tool, "result": result})

    # -- tier 2 -------------------------------------------------------------

    def _decline_media(
        self, action: PendingMediaAction, reason: str, *, repeated: bool = False
    ) -> str:
        self._declined[action.key] = self._declined.get(action.key, 0) + 1
        log_action(action, outcome="declined", detail={"reason": reason})
        return render_result(
            {
                "executed": False,
                "status": "declined_by_operator",
                "action": action.tool,
                "preview": action.preview,
                "reason": reason,
                "confirmationGrade": action.grade.value,
                "repeatedProposal": repeated,
                "guidance": (
                    "The operator declined. Nothing was moved, written or erased. Do "
                    "not propose this same action again: acknowledge it, ask what they "
                    "would prefer, or continue with something else."
                ),
            }
        )

    def _decline_repeat(self, tool: str, key: str) -> str:
        """Answer a re-proposal from the cache, without planning it again.

        One refusal is an answer; two is nagging, and for a format the second
        planning would also mint a second live safety token.
        """
        self._declined[key] = self._declined.get(key, 0) + 1
        logger_action = PendingMediaAction(
            tool=tool, arguments={}, preview="", grade=ConfirmationGrade.YES_NO
        )
        log_action(logger_action, outcome="declined", detail={"reason": "already declined"})
        return render_result(
            {
                "executed": False,
                "status": "declined_by_operator",
                "action": tool,
                "reason": "the operator already declined this exact action",
                "repeatedProposal": True,
                "guidance": (
                    "The operator declined this already and was not asked again. "
                    "Nothing was changed. Do not propose it a third time."
                ),
            }
        )

    def _announce(self, line: str) -> None:
        """Tell the operator something long is happening. Never fails the action."""
        if self.progress is None:
            return
        # A broken printer must never abort or fail a confirmed media action.
        with contextlib.suppress(Exception):
            self.progress(line)

    def _run_media_tool(self, call: ToolCall) -> str:
        """Plan, strongly confirm, and only then execute one tier-2 media action."""
        registry = self.media_registry
        facade = self.media
        confirm = self.confirm_media
        if registry is None or facade is None or confirm is None:  # pragma: no cover - guarded
            return render_result(
                {
                    "executed": False,
                    "status": "unavailable",
                    "error": (
                        "Media actions need the interactive REPL, where the operator "
                        "can confirm them. Propose the command instead."
                    ),
                }
            )

        # The decline cache is consulted BEFORE planning, not after. Planning a
        # format runs the real dry run, which persists a live safety token; a model
        # that re-proposes a refused format ten times would otherwise leave ten
        # live authorizations behind for an action the operator explicitly refused.
        try:
            key = registry.action_key(call.name, call.arguments)
        except MediaRefusedError as exc:
            return _refusal(exc, confirmed=False)
        except AssistantError:
            raise
        except Exception as exc:  # noqa: BLE001 - curated, never echoed raw
            return _unavailable(call.name, "could not be read", exc)
        if self._declined.get(key, 0) >= MAX_CONFIRMATION_PROMPTS_PER_ACTION:
            return self._decline_repeat(call.name, key)

        try:
            action = registry.plan(call.name, facade, call.arguments)
        except MediaRefusedError as exc:
            # Ambiguity refuses BEFORE the operator is asked. An unknown barcode
            # must never reach a prompt that a reflex "y" could answer.
            return _refusal(exc, confirmed=False)
        except AssistantError:
            raise
        except Exception as exc:  # noqa: BLE001 - curated, never echoed raw
            return _unavailable(call.name, "could not be checked", exc)

        try:
            response = confirm(action)
        except (EOFError, KeyboardInterrupt):
            # Ctrl-C / Ctrl-D at the prompt is a no, not a crash and never a yes.
            response = None
        except Exception:  # noqa: BLE001 - a broken prompt is a no, not a yes
            return self._decline_media(action, "the confirmation prompt failed")

        authorization = registry.authorize(action, response)
        if authorization is None:
            reason = (
                "the operator did not type the exact confirmation this action requires"
                if action.grade is ConfirmationGrade.TYPED
                else "the operator answered no"
            )
            return self._decline_media(action, reason)

        tool = registry.get(action.tool)
        if tool.long_running:
            self._announce(f"{action.tool}: running now — this can take minutes.")
        try:
            result = registry.perform(action, facade, authorization)
        except MediaRefusedError as exc:
            # Re-resolved inside the write path. A confirmation cannot buy a guess:
            # if the library moved underneath us, this stops here.
            log_action(action, outcome="refused", detail={"code": exc.code})
            self._announce(f"{action.tool}: refused — nothing was changed.")
            return _refusal(exc, confirmed=True)
        except MediaOperationFailedError as exc:
            # The message is already curated at the raise site in the facade: an
            # orchestrator constant, a typed OpenBlade message, or safe_job_error.
            #
            # A failed archive is NOT "nothing happened". The archive job writes
            # file by file and its cleanup only removes the records for files that
            # did not finish, so a failure on file 300 of 400 leaves 299 files on
            # tape and in the catalog. Tier 1 has SetupPartialWriteError for
            # exactly this; tier 2's partial surface is far bigger, so a failed
            # long-running action is flagged as possibly-partial rather than
            # reported as a clean no-op.
            partial = tool.long_running
            if partial:
                self._possibly_partial.append(action.tool)
            log_action(
                action,
                outcome="failed",
                detail={"error": str(exc), "possiblyPartial": partial},
            )
            self._announce(f"{action.tool}: failed — {exc}")
            return render_result(
                {
                    "executed": False,
                    "status": "failed",
                    "action": action.tool,
                    "preview": action.preview,
                    "error": str(exc),
                    "possiblyPartial": partial,
                    "guidance": (
                        "The operator confirmed and it failed. Report the error text "
                        "as given. "
                        + (
                            "Say explicitly that part of it may already have been "
                            "written and that they should check `openblade jobs` and "
                            "the catalog before retrying. "
                            if partial
                            else ""
                        )
                        + "Do not retry it yourself."
                    ),
                }
            )
        except AssistantError:
            # MediaNotAuthorizedError and facade/registry violations are defects in
            # the tool layer, not data for the model. Surface them loudly.
            raise
        except Exception as exc:  # noqa: BLE001 - curated below, never echoed raw
            log_action(action, outcome="error", detail={"type": type(exc).__name__})
            return render_result(
                {
                    "executed": False,
                    "status": "error",
                    "action": action.tool,
                    "error": (
                        f"{action.tool} was confirmed but could not be completed "
                        f"({type(exc).__name__}). Check `openblade jobs` and the "
                        "library inventory before assuming nothing happened."
                    ),
                }
            )
        self._executed.append(action.tool)
        self._announce(_completion_line(action.tool, result))
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
            if (
                self.setup_enabled
                and self.setup_registry is not None
                and call.name in self.setup_registry
            ):
                content = self._run_setup_tool(call)
            elif (
                self.media_enabled
                and self.media_registry is not None
                and call.name in self.media_registry
            ):
                content = self._run_media_tool(call)
            else:
                content = self._run_tool(call)
            self.messages.append({"role": "tool", "name": call.name, "content": content})

    def ask(self, question: str, *, on_tool: ToolObserver | None = None) -> AssistantTurn:
        """Ask a question and run the tool loop until the model answers in prose.

        On failure the transcript is rewound to where this turn started. Otherwise a
        turn that died mid-round would leave an assistant message carrying unanswered
        ``tool_calls`` plus a dangling ``tool`` message, and the operator's next
        question would be appended onto that malformed history with no sign of it.

        Rewinding stops at an executed tier-1 action, because that one did happen:
        a confirmed write followed by a loop-limit error must not leave the model
        with no memory of it — it would propose the same action again on the next
        question, and the operator would be asked to confirm something already done.
        """
        checkpoint = len(self.messages)
        try:
            return self._ask(question, on_tool)
        except AssistantError:
            del self.messages[self._rewind_floor(checkpoint) :]
            raise

    @property
    def executed_this_turn(self) -> tuple[str, ...]:
        """Tier-1 actions that ran during the last ``ask()``, failed turn included.

        ``AssistantTurn`` only exists on the success path, so without this a turn
        that wrote and then hit the round limit would report nothing to the
        operator — the one case where they most need to be told.
        """
        return tuple(self._executed)

    @property
    def possibly_partial_this_turn(self) -> tuple[str, ...]:
        """Long-running actions that failed and may have written something anyway.

        Separate from ``executed_this_turn`` because the honest answer is "some of
        it, we do not know how much", and reporting that as either "applied" or
        "nothing happened" would be a lie in opposite directions.
        """
        return tuple(self._possibly_partial)

    def _rewind_floor(self, checkpoint: int) -> int:
        """The earliest index this turn may rewind to.

        Everything up to and including the last tool message reporting an executed
        action is kept, so the transcript never claims less happened than did.
        """
        if not self._executed and not self._possibly_partial:
            return checkpoint
        # A possibly-partial failure is kept for the same reason an execution is:
        # something may be on tape, and a transcript that forgets it invites the
        # model to propose the same archive again.
        markers = ('"executed": true', '"possiblyPartial": true')
        for index in range(len(self.messages) - 1, checkpoint - 1, -1):
            message = self.messages[index]
            content = str(message.get("content", ""))
            if message.get("role") == "tool" and any(marker in content for marker in markers):
                return index + 1
        return checkpoint

    def _schemas(self) -> list[dict[str, Any]]:
        """Tool definitions offered to the model this turn.

        Tier-1 and tier-2 schemas are added only when the session can actually
        confirm that kind of action. In one-shot mode the model is told about
        neither, so it proposes commands instead of asking for an execution that
        cannot happen.
        """
        schemas = self.registry.schemas()
        if self.setup_enabled and self.setup_registry is not None:
            schemas.extend(self.setup_registry.schemas())
        if self.media_enabled and self.media_registry is not None:
            schemas.extend(self.media_registry.schemas())
        return schemas

    def _ask(self, question: str, on_tool: ToolObserver | None) -> AssistantTurn:
        # Drop cached rows first, so this turn sees what other processes committed.
        self.context.refresh()
        self.messages.append({"role": "user", "content": question})
        schemas: Sequence[dict[str, Any]] = self._schemas()
        used: list[str] = []
        self._executed.clear()
        self._possibly_partial.clear()

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
