"""``openblade assist`` — the read-only operator assistant.

One-shot::

    openblade assist "which tapes are in the photo-archive pool?"

Interactive::

    openblade assist          # REPL; /quit to leave

The assistant never runs anything. It reads state, explains, and proposes commands
for you to run — see ``docs/wiki/guides/assistant.md``.
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
    create_session,
)
from openblade.assistant.config import DISABLED_MESSAGE, load_assistant_config

console = Console()

_BANNER = (
    "OpenBlade assistant — read-only. It proposes commands; it never runs them.\n"
    "Type your question, or /quit to leave, /reset to clear the conversation."
)
_PROMPT = "openblade> "


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


def _build_session() -> AssistantSession:
    """Build a session over the CLI's context.

    ``_get_context()`` is imported lazily because ``openblade.cli.main`` imports
    this module, so a module-level import back into it would be circular. The
    disabled check happens before that call, so an unconfigured assistant never
    opens the database.
    """
    config = load_assistant_config()
    if not config.enabled:
        raise AssistantDisabledError(DISABLED_MESSAGE)

    from openblade.cli.main import _get_context  # local: avoids an import cycle

    return create_session(_get_context(), config=config)


def _ask(session: AssistantSession, question: str) -> None:
    turn = session.ask(question, on_tool=_show_tool)
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


def assist(
    question: str | None = typer.Argument(
        None, help="Ask one question and exit. Omit for an interactive session."
    ),
) -> None:
    """Ask the read-only OpenBlade assistant about this installation."""
    try:
        session = _build_session()
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
