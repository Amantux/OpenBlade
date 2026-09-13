"""OpenBlade operator assistant — an advisor over the local installation.

The assistant answers questions about this library's state and about how OpenBlade
works, and it proposes commands for the operator to run. It executes exactly two
kinds of thing, both only in the interactive REPL and both only after the operator
confirms: tier-1 catalog setup (:mod:`openblade.assistant.setup_tools`, a simple
yes) and tier-2 media and robotics (:mod:`openblade.assistant.media_tools`, a
confirmation graded by consequence — a typed barcode to format, a typed word to
overwrite a file). Everything else it proposes and the operator runs.

That boundary is structural, not a prompt instruction: see
:mod:`openblade.assistant.readonly` for the allowlist machinery all three surfaces
are built on, and ``tests/safety/test_assistant_read_only.py`` for the
mutation-checked regressions.

This module is also the composition root. It is the one place allowed to hand the
live job services to the media bundle, and it does so only when a media
confirmation callback exists — which is what makes one-shot mode read-only by
construction rather than by policy.

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
    MediaFacadeViolationError,
    MediaNotAuthorizedError,
    MediaOperationFailedError,
    MediaRefusedError,
    MediaRegistryViolationError,
    ReadOnlyViolationError,
    ToolNotFoundError,
    ToolRegistryViolationError,
)
from openblade.assistant.media_facade import MediaFacade, media_bundle, media_facade
from openblade.assistant.media_tools import (
    MEDIA_TOOL_NAMES,
    ConfirmationGrade,
    MediaConfirmCallback,
    PendingMediaAction,
    ProgressCallback,
    build_media_registry,
)
from openblade.assistant.provider import ChatReply, OllamaClient, ToolCall
from openblade.assistant.readonly import read_only_catalog, read_only_inventory
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
    "MEDIA_TOOL_NAMES",
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
    "ConfirmationGrade",
    "MediaConfirmCallback",
    "MediaFacade",
    "MediaFacadeViolationError",
    "MediaNotAuthorizedError",
    "MediaOperationFailedError",
    "MediaRefusedError",
    "MediaRegistryViolationError",
    "OllamaClient",
    "PendingAction",
    "PendingMediaAction",
    "ProgressCallback",
    "ReadOnlyTool",
    "ReadOnlyViolationError",
    "ToolCall",
    "ToolContext",
    "ToolNotFoundError",
    "ToolRegistry",
    "ToolRegistryViolationError",
    "build_context",
    "build_media_registry",
    "build_registry",
    "create_session",
    "load_assistant_config",
    "media_bundle",
    "media_facade",
]


def create_session(
    app_context: AppContext,
    *,
    config: AssistantConfig | None = None,
    http_client: httpx.Client | None = None,
    confirm: ConfirmCallback | None = None,
    confirm_media: MediaConfirmCallback | None = None,
    progress: ProgressCallback | None = None,
) -> AssistantSession:
    """Build a session over a live :class:`~openblade.bootstrap.AppContext`.

    Raises :class:`AssistantDisabledError` with the curated setup message when
    ``OPENBLADE_OLLAMA_URL`` is unset.

    ``confirm`` turns tier-1 setup actions on and ``confirm_media`` turns tier-2
    media actions on. They are separate parameters, not one flag, because they are
    separate capabilities with different confirmation strengths — and because a
    caller that can only ask a yes/no question must not silently acquire the power
    to format a cartridge. Pass neither — as one-shot mode does — and the session is
    read-only, with neither tier's tools shown to the model at all.

    ``progress`` receives one line before and after a long media operation.
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
    # Built only when a confirmation callback exists. No callback, no facade, no
    # registry: each write path is absent rather than merely unused. The media
    # bundle also wraps its own read proxies, so the tier-2 validation half cannot
    # write even though the tier-2 execution half can.
    media = (
        media_facade(
            media_bundle(
                catalog=read_only_catalog(app_context.catalog),
                inventory=read_only_inventory(app_context.inventory_service),
                catalog_repo=app_context.catalog,
                library=app_context.library,
                ltfs=app_context.ltfs,
                format_service=app_context.format_service,
                archive_service=app_context.archive_service,
                restore_service=app_context.restore_service,
                drive_serials=openblade_config.drive_serial_map,
            )
        )
        if confirm_media is not None
        else None
    )
    return AssistantSession(
        client=OllamaClient(resolved, client=http_client),
        registry=build_registry(),
        context=tool_context,
        config=resolved,
        setup_registry=build_setup_registry() if confirm is not None else None,
        setup=setup_facade(app_context.catalog) if confirm is not None else None,
        confirm=confirm,
        media_registry=build_media_registry() if confirm_media is not None else None,
        media=media,
        confirm_media=confirm_media,
        progress=progress,
    )
