"""``POST /assist`` — the operator assistant over HTTP, read-only.

The assistant has existed only as a CLI (``openblade assist``). This exposes the
same session loop to the API so a UI or an automation can ask it questions.

Read-only is structural, not a policy note
------------------------------------------
The CLI turns tier-1 setup actions on by handing
:func:`openblade.assistant.create_session` a confirmation callback — and that
callback is the *only* thing that builds the setup registry and the write facade
(``assistant/__init__.py``: "No callback, no facade, no setup registry: the write
path is absent rather than merely unused"). An HTTP request has nobody to ask, so
this route passes no callback: the mutating tools are never constructed and their
schemas are never shown to the model. :func:`_require_read_only` then asserts that
outcome before the session is used, so a future change to the factory fails loudly
here instead of silently exposing writes over HTTP.

Statelessness
-------------
The request carries the whole conversation. Only ``user`` and ``assistant`` turns
are accepted, so a caller cannot inject a system prompt or forge a ``tool`` result
claiming something was executed; the system prompt is always the one the session
builds for itself.

Not starving the emulator
-------------------------
An assistant turn is a long blocking call — up to ``max_rounds × timeout`` (6 ×
120s by default). This app is also the Quantum AML emulator, so a chat request
must not be able to take that surface down with it. Two consequences, both
deliberate: the handler is ``async def`` and hands off to a worker thread under
its OWN small capacity limiter rather than being a sync handler borrowing from
AnyIO's shared 40-token default pool; and each turn gets its own database session,
because ``AppContext.catalog`` wraps a single process-wide SQLAlchemy ``Session``
that every other (``async def``) handler touches from the event loop thread.

Auth
----
This module adds no authentication of its own — the native API has none yet. It is
a plain native route, so whatever bearer layer lands in front of the native surface
covers it automatically. The per-client rate limit below shapes cost (each request
costs an upstream model call); the concurrency limiter above is what actually
protects the process. Neither is an access control.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Literal

import anyio
from anyio import to_thread
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from openblade.assistant import (
    AssistantDisabledError,
    AssistantLoopLimitError,
    AssistantSession,
    AssistantUpstreamError,
    create_session,
)
from openblade.bootstrap import AppContext, get_context
from openblade.catalog.db import get_session
from openblade.catalog.repository import CatalogRepository

router = APIRouter()

#: Caps on the request body. A conversation is replayed into the model verbatim,
#: so an unbounded one is a cost and latency amplifier, not just a big payload.
MAX_MESSAGES = 40
MAX_MESSAGE_CHARS = 8000


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


#: Token bucket: ``BURST`` requests immediately, then one per
#: ``WINDOW_SECONDS / BURST``. Per client, in-process — this is one API worker's
#: view, which is the honest scope for an in-memory limiter.
RATE_BURST = _positive_int("OPENBLADE_ASSIST_RATE_BURST", 5)
RATE_WINDOW_SECONDS = _positive_int("OPENBLADE_ASSIST_RATE_WINDOW_SECONDS", 60)
#: Idle buckets are dropped rather than accumulating one entry per client forever.
_BUCKET_IDLE_SECONDS = RATE_WINDOW_SECONDS * 10
_MAX_TRACKED_CLIENTS = 4096


@dataclass
class _Bucket:
    tokens: float
    updated: float


@dataclass
class TokenBucketLimiter:
    """Fixed-rate token bucket per client key.

    Deliberately not shared with the AML login limiter: that one is a sliding
    window sized for credential stuffing, this one shapes an expensive call.
    """

    burst: int
    window_seconds: int
    _buckets: dict[str, _Bucket] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def _refill_per_second(self) -> float:
        return self.burst / self.window_seconds

    def allow(self, key: str, *, now: float | None = None) -> bool:
        moment = time.monotonic() if now is None else now
        with self._lock:
            self._evict(moment)
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=float(self.burst), updated=moment)
                self._buckets[key] = bucket
            else:
                elapsed = max(0.0, moment - bucket.updated)
                bucket.tokens = min(
                    float(self.burst), bucket.tokens + elapsed * self._refill_per_second
                )
                bucket.updated = moment
            if bucket.tokens < 1.0:
                return False
            bucket.tokens -= 1.0
            return True

    def _evict(self, moment: float) -> None:
        stale = [
            key
            for key, bucket in self._buckets.items()
            if moment - bucket.updated > _BUCKET_IDLE_SECONDS
        ]
        for key in stale:
            del self._buckets[key]
        if len(self._buckets) > _MAX_TRACKED_CLIENTS:
            # Bounded memory beats perfect accounting: the oldest entries are the
            # least likely to be mid-burst.
            oldest = sorted(self._buckets.items(), key=lambda item: item[1].updated)
            for key, _ in oldest[: len(self._buckets) - _MAX_TRACKED_CLIENTS]:
                del self._buckets[key]

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


_limiter = TokenBucketLimiter(burst=RATE_BURST, window_seconds=RATE_WINDOW_SECONDS)

#: How many assistant turns may occupy a worker thread at once, and how long a
#: request waits for a slot before being told the assistant is busy. Small on
#: purpose: a turn is a long blocking call, and this app also serves the AML
#: emulator parity surface, which must not be starved by a chat request.
ASSIST_MAX_CONCURRENCY = _positive_int("OPENBLADE_ASSIST_MAX_CONCURRENCY", 2)
ASSIST_QUEUE_TIMEOUT_SECONDS = _positive_int("OPENBLADE_ASSIST_QUEUE_TIMEOUT_SECONDS", 30)
_assist_slots = anyio.CapacityLimiter(ASSIST_MAX_CONCURRENCY)


def client_key(request: Request) -> str:
    """Identify the caller for rate limiting.

    The peer address only. ``X-Forwarded-For`` is deliberately ignored: it is
    caller-controlled, so honouring it would let one client mint unlimited
    identities and defeat the limit entirely.
    """
    return request.client.host if request.client else "unknown"


class AssistMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class AssistRequest(BaseModel):
    messages: list[AssistMessage] = Field(min_length=1, max_length=MAX_MESSAGES)


class AssistResponse(BaseModel):
    reply: str
    #: Names only. Arguments and results can carry catalog contents and are the
    #: model's paraphrase of the request — the reply is the answer, this is a trace.
    toolCalls: list[str]


def _require_read_only(session: AssistantSession) -> AssistantSession:
    """Fail loudly unless the write path is structurally absent.

    Checks every part independently rather than trusting ``setup_enabled``: this
    guard exists precisely for the case where that property, or the factory that
    feeds it, changed under us.
    """
    if (
        session.confirm is not None
        or session.setup is not None
        or session.setup_registry is not None
        or session.setup_enabled
    ):
        raise RuntimeError(
            "the /assist session was built with a write path; the HTTP surface is "
            "read-only because there is no operator to confirm an action"
        )
    return session


def build_readonly_session(context: AppContext) -> AssistantSession:
    """Build the session this route runs. No confirm callback, ever."""
    session = create_session(context, confirm=None)
    try:
        return _require_read_only(session)
    except BaseException:
        # The guard fires only on a defect, but a defect that also leaked an
        # httpx.Client per request would turn one bug into two.
        session.client.close()
        raise


@contextmanager
def _request_scoped_context(context: AppContext) -> Iterator[AppContext]:
    """Yield ``context`` with its own database session for the duration of a turn.

    ``AppContext.catalog`` wraps ONE process-global SQLAlchemy ``Session``, which
    is not thread-safe. Every other route handler is ``async def`` and so touches
    it only from the event loop thread; this route runs the assistant's reads on a
    worker thread, and the assistant also calls ``expire_all()`` between rounds —
    which would expire rows out from under a request running on the loop. So the
    turn gets its own session and closes it afterwards. ``inventory_service`` holds
    only the library backend (no session), so it is safe to share.
    """
    db_session = get_session()
    try:
        yield replace(context, catalog=CatalogRepository(db_session))
    finally:
        db_session.close()


def _run_turn(payload: AssistRequest, context: AppContext) -> AssistResponse:
    """One assistant turn. Runs on a worker thread — the session loop is blocking."""
    with _request_scoped_context(context) as scoped:
        try:
            session = build_readonly_session(scoped)
        except AssistantDisabledError as exc:
            # Not configured is a first-class state with a curated explanation, not
            # a failure the caller has to decode.
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        # Prior turns are replayed as history; the system prompt the session built
        # for itself stays in place at index 0 and is never caller-supplied.
        session.messages.extend(
            {"role": message.role, "content": message.content} for message in payload.messages[:-1]
        )

        try:
            turn = session.ask(payload.messages[-1].content)
        except (AssistantUpstreamError, AssistantLoopLimitError) as exc:
            # Both messages are curated at the raise site; raw provider text never
            # reaches here (see assistant/provider.py).
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        finally:
            session.client.close()

    return AssistResponse(reply=turn.reply, toolCalls=list(turn.tool_calls))


@router.post("/assist", response_model=AssistResponse)
async def assist(
    payload: AssistRequest,
    request: Request,
    context: AppContext = Depends(get_context),
) -> AssistResponse:
    """Answer one question in the context of the supplied conversation.

    ``async def`` with an explicit hand-off to a worker thread, rather than a sync
    handler. A sync handler would borrow from AnyIO's *default* thread limiter —
    40 tokens, shared with every sync dependency in the app, including the AML
    emulator surface. One assistant turn can hold its thread for
    ``max_rounds × timeout`` (6 × 120s = 12 minutes), so a handful of slow requests
    would starve the parity surface this repo exists to serve. Holding our own
    small limiter instead bounds the blast radius to ``ASSIST_MAX_CONCURRENCY``.
    """
    if not _limiter.allow(client_key(request)):
        raise HTTPException(
            status_code=429,
            detail=(
                f"Too many assistant requests. The limit is {RATE_BURST} per "
                f"{RATE_WINDOW_SECONDS}s per client; wait and retry."
            ),
        )

    if payload.messages[-1].role != "user":
        raise HTTPException(
            status_code=422,
            detail="The last message must have role 'user' — that is the question to answer.",
        )

    try:
        with anyio.fail_after(ASSIST_QUEUE_TIMEOUT_SECONDS):
            await _assist_slots.acquire()
    except TimeoutError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                f"The assistant is busy ({ASSIST_MAX_CONCURRENCY} turns already running) "
                "and the queue did not clear. Retry shortly."
            ),
        ) from exc

    try:
        return await to_thread.run_sync(_run_turn, payload, context)
    finally:
        _assist_slots.release()
