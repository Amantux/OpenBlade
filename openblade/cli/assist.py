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

from openblade.assistant import (
    AssistantDisabledError,
    AssistantError,
    AssistantSession,
    create_session,
)
from openblade.assistant.config import load_assistant_config

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
    """One dim line per tool call, so the operator can see what was consulted."""
    rendered = _format_arguments(arguments)
    suffix = f" {rendered}" if rendered else ""
    console.print(f"[dim]· {name}{suffix}[/dim]")


def _build_session() -> AssistantSession:
    """Build a session over the CLI's context.

    Imported lazily: ``openblade.cli.main`` imports this module, so a module-level
    import back into it would be circular.
    """
    from openblade.cli.main import _get_context  # local: avoids an import cycle

    config = load_assistant_config()
    if not config.enabled:
        # Raised before touching the DB, so a disabled assistant costs nothing.
        raise AssistantDisabledError(_disabled_message())
    return create_session(_get_context(), config=config)


def _disabled_message() -> str:
    from openblade.assistant.config import DISABLED_MESSAGE

    return DISABLED_MESSAGE


def _ask(session: AssistantSession, question: str) -> None:
    turn = session.ask(question, on_tool=_show_tool)
    console.print(turn.reply)


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
            console.print("[dim]conversation cleared[/dim]")
            continue
        if line in {"/help", "/?"}:
            console.print(_BANNER)
            continue
        try:
            _ask(session, line)
        except AssistantError as exc:
            # Curated message only — provider/socket text never reaches here.
            console.print(f"[red]{exc}[/red]")


def assist(
    question: str | None = typer.Argument(
        None, help="Ask one question and exit. Omit for an interactive session."
    ),
) -> None:
    """Ask the read-only OpenBlade assistant about this installation."""
    try:
        session = _build_session()
    except AssistantDisabledError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from None

    try:
        if question is not None:
            _ask(session, question)
        else:
            _repl(session)
    except AssistantError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from None
    finally:
        session.client.close()
