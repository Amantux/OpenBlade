"""Typed errors for the operator assistant.

Every error raised out of this package carries an operator-facing message that is
safe to print. Upstream provider text (socket errors, HTTP bodies, tracebacks) is
never propagated: it can embed URLs, hostnames and credentials, and it is noise to
the operator. See ``openblade/assistant/provider.py`` for the normalization site.
"""

from __future__ import annotations

from openblade.domain.errors import OpenBladeError


class AssistantError(OpenBladeError):
    """Base error for the operator assistant."""


class AssistantDisabledError(AssistantError):
    """The assistant is not configured (no Ollama endpoint)."""


class AssistantUpstreamError(AssistantError):
    """The Ollama endpoint was unreachable or returned something unusable.

    The message is curated at the raise site; the raw upstream text is deliberately
    dropped rather than wrapped.
    """


class AssistantLoopLimitError(AssistantError):
    """The model kept calling tools past the bounded round limit."""


class ToolNotFoundError(AssistantError):
    """The model asked for a tool that is not in the read-only registry."""


class ToolRegistryViolationError(AssistantError):
    """A tool was registered that is not on the read-only allowlist.

    This is the fail-closed guard: adding a tool without adding its name to
    ``READ_ONLY_TOOL_NAMES`` raises at registry-build time rather than silently
    widening what the assistant can do.
    """


class ReadOnlyViolationError(AssistantError):
    """A tool tried to reach a mutating method through a read-only proxy."""
