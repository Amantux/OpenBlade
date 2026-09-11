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


class SetupRegistryViolationError(AssistantError):
    """A tier-1 setup tool was registered that the setup registry refuses.

    Either the name is not on ``SETUP_TOOL_NAMES`` (the fail-closed allowlist) or it
    contains a destructive verb (the denylist, which wins over the allowlist).
    """


class SetupFacadeViolationError(AssistantError):
    """A setup tool tried to reach something the write facade does not expose."""


class MediaRegistryViolationError(AssistantError):
    """A tier-2 media tool was registered that the media registry refuses.

    Either the name is not on ``MEDIA_TOOL_NAMES`` (the fail-closed allowlist) or it
    already belongs to another registry. The three registries are disjoint by
    construction: a media tool name can never register as a tier-1 setup tool (the
    tier-1 denylist sees to that), and a setup or read tool name can never register
    as a media tool.
    """


class MediaFacadeViolationError(AssistantError):
    """A media tool tried to reach something the tier-2 media facade does not expose."""


class MediaRefusedError(AssistantError):
    """A media action was refused because its target is not unambiguous.

    The tier-1 house rule, unchanged, and applied *before* the operator is prompted:
    an unknown barcode, a barcode that appears in two places, an occupied target
    drive or slot, a mounted drive — each stops the action here and hands back the
    candidates the operator might have meant. It is raised again inside the write
    path, because a confirmation is not a licence to act on stale facts.
    """

    def __init__(self, message: str, *, code: str, candidates: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.code = code
        self.candidates = candidates


class MediaOperationFailedError(AssistantError):
    """A confirmed media action ran and failed.

    The message is the *curated* failure text — the orchestrator's per-op-type
    constant, a typed ``OpenBladeError``'s operator-written message, or
    ``safe_job_error``'s class-name-only fallback. Raw tool output (``mtx``/
    ``mkltfs`` stderr, argv, device paths, a DSN) never reaches it, because it is
    built at the raise site in :mod:`openblade.assistant.media_facade` rather than
    by wrapping whatever came out of the service.
    """


class MediaNotAuthorizedError(AssistantError):
    """A media action reached the write path without a matching authorization.

    Raised by :meth:`MediaToolRegistry.perform` when the authorization it is handed
    does not re-verify against the action it claims to authorize — a wrong action
    key, the wrong confirmation grade, or a response the grade does not accept (a
    bare ``y`` against a typed-barcode confirmation). It is a defect in the caller,
    not data for the model, so it propagates rather than being reported back into
    the conversation.
    """


class SetupRefusedError(AssistantError):
    """A setup action was refused because its target is not unambiguous.

    Confirmation is not a licence to guess: an unknown barcode, a tape already in
    another volume group, or a name that is already taken stops the action here and
    hands the candidates back to the model. ``code`` is a stable machine-readable
    reason; ``candidates`` are the objects the operator might have meant.
    """

    def __init__(self, message: str, *, code: str, candidates: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.code = code
        self.candidates = candidates


class SetupPartialWriteError(AssistantError):
    """A multi-step setup action failed after part of it had already committed.

    The catalog repository commits per cartridge, so "add three tapes" is three
    transactions and a failure on the second leaves the first in place. Reporting
    "nothing happened" there would be false, and the audit line would be wrong
    about what the assistant did — so the applied items travel with the error.
    """

    def __init__(self, message: str, *, applied: tuple[str, ...], cause: str) -> None:
        super().__init__(message)
        self.applied = applied
        self.cause = cause
