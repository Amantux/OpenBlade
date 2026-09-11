"""OpenBlade operator assistant — a read-only advisor over the local installation.

The assistant answers questions about this library's state and about how OpenBlade
works, and it proposes commands for the operator to run. It never executes them.
That boundary is structural, not a prompt instruction: see
:mod:`openblade.assistant.readonly` for the three enforcement points.

Typical use::

    from openblade.assistant import create_session
    session = create_session(app_context)
    print(session.ask("which tapes are in the photo-archive pool?").reply)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from openblade.assistant.config import (
    DISABLED_MESSAGE,
    AssistantConfig,
    load_assistant_config,
)
from openblade.assistant.errors import (
    AssistantDisabledError,
    AssistantError,
    AssistantLoopLimitError,
    AssistantUpstreamError,
    ReadOnlyViolationError,
    ToolNotFoundError,
    ToolRegistryViolationError,
)
from openblade.assistant.provider import ChatReply, OllamaClient, ToolCall
from openblade.assistant.session import AssistantSession, AssistantTurn
from openblade.assistant.setup_facade import setup_facade
from openblade.assistant.setup_tools import (
    SETUP_TOOL_NAMES,
    ConfirmCallback,
    PendingAction,
    build_setup_registry,
)
from openblade.assistant.tools import (
    READ_ONLY_TOOL_NAMES,
    ReadOnlyTool,
    ToolContext,
    ToolRegistry,
    build_context,
    build_registry,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle guard only
    from openblade.bootstrap import AppContext

__all__ = [
    "DISABLED_MESSAGE",
    "READ_ONLY_TOOL_NAMES",
    "SETUP_TOOL_NAMES",
    "AssistantConfig",
    "AssistantDisabledError",
    "AssistantError",
    "AssistantLoopLimitError",
    "AssistantSession",
    "AssistantTurn",
    "AssistantUpstreamError",
    "ChatReply",
    "OllamaClient",
    "PendingAction",
    "ReadOnlyTool",
    "ReadOnlyViolationError",
    "ToolCall",
    "ToolContext",
    "ToolNotFoundError",
    "ToolRegistry",
    "ToolRegistryViolationError",
    "build_context",
    "build_registry",
    "create_session",
    "load_assistant_config",
]


def create_session(
    app_context: AppContext,
    *,
    config: AssistantConfig | None = None,
    http_client: httpx.Client | None = None,
    confirm: ConfirmCallback | None = None,
) -> AssistantSession:
    """Build a session over a live :class:`~openblade.bootstrap.AppContext`.

    Raises :class:`AssistantDisabledError` with the curated setup message when
    ``OPENBLADE_OLLAMA_URL`` is unset.

    ``confirm`` is what turns tier-1 setup actions on. Pass the REPL's ``[y/N]``
    prompt to get a session that can create a volume group and add tapes to it once
    the operator says yes; omit it — as one-shot mode does — and the session is
    read-only, with the setup tools never shown to the model.
    """
    resolved = config or load_assistant_config()
    if not resolved.enabled:
        raise AssistantDisabledError(DISABLED_MESSAGE)

    openblade_config = app_context.config
    tool_context = build_context(
        config=resolved,
        catalog=app_context.catalog,
        inventory_service=app_context.inventory_service,
        backend=openblade_config.backend.value,
        real_hardware_enabled=openblade_config.real_hardware_enabled,
        db_url=openblade_config.db_url,
        scalar_url=openblade_config.scalar_url,
        scalar_password=openblade_config.scalar_password,
        hardware_dry_run=openblade_config.hardware_dry_run,
    )
    return AssistantSession(
        client=OllamaClient(resolved, client=http_client),
        registry=build_registry(),
        context=tool_context,
        config=resolved,
        # Built only when a confirmation callback exists. No callback, no facade,
        # no setup registry: the write path is absent rather than merely unused.
        setup_registry=build_setup_registry() if confirm is not None else None,
        setup=setup_facade(app_context.catalog) if confirm is not None else None,
        confirm=confirm,
    )
