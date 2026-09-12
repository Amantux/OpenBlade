"""``openblade assist`` — the operator assistant.

One-shot::

    openblade assist "which tapes are in the photo-archive pool?"

Interactive::

    openblade assist          # REPL; /quit to leave

The assistant reads state, explains, and proposes commands for you to run. In the
REPL it can also *perform* two kinds of action, and only after you confirm a preview
naming exactly what it would do:

* tier 1 — catalog setup (create a volume group, add tapes to one): answer ``y``.
* tier 2 — media and robotics (load, unload, move, archive, restore, format): the
  preview names the cartridge, slot, drive and cost. Load/unload/move/archive take
  ``y``; a format, and a restore that would overwrite a file, make you TYPE the
  barcode or the word the preview demands. A bare ``y`` is refused there.

Everything else stays propose-only. One-shot mode has nowhere to ask, so it is
offered neither tier's tools. See ``docs/wiki/guides/assistant.md``.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

import typer
from rich.console import Console
from rich.text import Text

from openblade.assistant import (
    AssistantDisabledError,
    AssistantError,
    AssistantSession,
    ConfirmationGrade,
    PendingAction,
    PendingMediaAction,
    create_session,
)
from openblade.assistant.config import DISABLED_MESSAGE, load_assistant_config

console = Console()

_BANNER = (
    "OpenBlade assistant. It can set up volume groups, and load, unload, move,\n"
    "archive, restore and format media — and it asks you first, every time. A\n"
    "format, or a restore that would overwrite a file, makes you type the barcode\n"
    'or the word shown; "y" will not do it. Everything else it proposes; you run it.\n'
    "Type your question, or /quit to leave, /reset to clear the conversation."
)
_PROMPT = "openblade> "
# Answers that mean yes. Anything else — including a bare Enter, "ok", or EOF — is a
# no: a confirmation must be given, never merely not refused.
_YES = frozenset({"y", "yes"})


def _format_arguments(arguments: dict[str, Any]) -> str:
    if not arguments:
        return ""
    try:
        rendered = json.dumps(arguments, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        rendered = str(arguments)
    return rendered if len(rendered) <= 80 else rendered[:77] + "..."


def _show_tool(name: str, arguments: dict[str, Any]) -> None:
    """One dim line per tool call, so the operator can see what was consulted.

    Built as a ``Text`` rather than a markup string: the arguments come from the
    model and a stray ``[/x]`` in them would raise ``MarkupError``.
    """
    rendered = _format_arguments(arguments)
    suffix = f" {rendered}" if rendered else ""
    console.print(Text(f"· {name}{suffix}", style="dim"))


def _confirm_media_action(action: PendingMediaAction) -> str | None:
    """Ask the operator to confirm one tier-2 media action, returning what they typed.

    This returns TEXT, not a decision. Whether the text is good enough is decided by
    :meth:`MediaToolRegistry.authorize`, so the strength of a format confirmation
    does not depend on this function being written correctly — typing "y" at a
    format prompt is refused there even if this prompt accepted it.

    The preview is printed as a ``Text`` for the same reason tier-1's is: it carries
    operator- and model-supplied names, and a stray ``[/x]`` would raise
    ``MarkupError`` in the middle of a confirmation prompt.
    """
    destructive = action.grade is ConfirmationGrade.TYPED
    console.print(
        Text(f"\nProposed action: {action.preview}", style="red" if destructive else "yellow")
    )
    prompt = (
        f"Type {action.required_response} to confirm (anything else cancels): "
        if destructive
        else "Run it? [y/N] "
    )
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        console.print()
        return None


def _progress(line: str) -> None:
    """One dim line around a long media operation, so a blocking REPL is not a hang."""
    console.print(Text(line, style="dim"))


def _confirm_action(action: PendingAction) -> bool:
    """Ask the operator to confirm one tier-1 action. Default is no.

    The preview is printed as a ``Text`` for the same reason tool arguments are:
    it contains operator- and model-supplied names, and a stray ``[/x]`` would
    raise ``MarkupError`` in the middle of a confirmation prompt.
    """
    console.print(Text(f"\nProposed action: {action.preview}", style="yellow"))
    try:
        answer = input("Run it? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        console.print()
        return False
    return answer in _YES


def _build_session(*, interactive: bool) -> AssistantSession:
    """Build a session over the CLI's context.

    ``_get_context()`` is imported lazily because ``openblade.cli.main`` imports
    this module, so a module-level import back into it would be circular. The
    disabled check happens before that call, so an unconfigured assistant never
    opens the database.

    ``interactive`` is the switch for both tiers: only the REPL can ask a human, so
    only the REPL gets confirmation callbacks — and without them, ``create_session``
    builds neither write facade at all.
    """
    config = load_assistant_config()
    if not config.enabled:
        raise AssistantDisabledError(DISABLED_MESSAGE)

    from openblade.cli.main import _get_context  # local: avoids an import cycle

    return create_session(
        _get_context(),
        config=config,
        confirm=_confirm_action if interactive else None,
        confirm_media=_confirm_media_action if interactive else None,
        progress=_progress if interactive else None,
    )


def _show_executed(actions: tuple[str, ...], partial: tuple[str, ...] = ()) -> None:
    """One line per confirmed write, so the transcript shows what changed.

    Printed on the failure path too: a turn that wrote and then hit the round
    limit still wrote, and the operator has to know. A failed archive or restore
    gets its own line, because "it failed" and "nothing happened" are not the same
    sentence when the job writes file by file.
    """
    for action in actions:
        console.print(Text(f"✓ {action} applied", style="green"))
    for action in partial:
        console.print(
            Text(
                f"⚠ {action} failed part-way — some of it may already be on tape. "
                "Check `openblade jobs` and the catalog before retrying.",
                style="yellow",
            )
        )


def _ask(session: AssistantSession, question: str) -> None:
    try:
        turn = session.ask(question, on_tool=_show_tool)
    except AssistantError:
        _show_executed(session.executed_this_turn, session.possibly_partial_this_turn)
        raise
    _show_executed(turn.executed_actions, session.possibly_partial_this_turn)
    # markup=False is load-bearing: model replies contain markdown links, array
    # syntax and quoted doc excerpts. Rich would either raise MarkupError on an
    # unbalanced tag (killing the REPL) or silently swallow "[dim]" as styling.
    console.print(turn.reply, markup=False, highlight=False)


def _repl(session: AssistantSession) -> None:
    # readline is absent only on unusual builds; line editing degrades, nothing breaks.
    with contextlib.suppress(ImportError):
        import readline  # noqa: F401

    console.print(_BANNER)
    while True:
        try:
            line = input(_PROMPT).strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return
        if not line:
            continue
        if line in {"/quit", "/exit", "/q"}:
            return
        if line == "/reset":
            session.reset()
            console.print(Text("conversation cleared", style="dim"))
            continue
        if line in {"/help", "/?"}:
            console.print(_BANNER)
            continue
        try:
            _ask(session, line)
        except AssistantError as exc:
            # Curated message only — provider/socket text never reaches here.
            console.print(Text(str(exc), style="red"))
        except Exception as exc:  # noqa: BLE001 - the REPL must survive a defect
            # An unexpected failure ends the question, not the session: the
            # operator may be mid-setup. The type name only — a database error's
            # text can carry a DSN.
            console.print(
                Text(
                    f"That question failed unexpectedly ({type(exc).__name__}). "
                    "The conversation is intact; try again or /reset.",
                    style="red",
                )
            )


def assist(
    question: str | None = typer.Argument(
        None, help="Ask one question and exit. Omit for an interactive session."
    ),
) -> None:
    """Ask the OpenBlade assistant about this installation, or talk through setup."""
    try:
        session = _build_session(interactive=question is None)
    except AssistantDisabledError as exc:
        console.print(Text(str(exc)))
        raise typer.Exit(code=1) from None

    try:
        if question is not None:
            _ask(session, question)
        else:
            _repl(session)
    except AssistantError as exc:
        console.print(Text(str(exc), style="red"))
        raise typer.Exit(code=1) from None
    finally:
        session.client.close()
