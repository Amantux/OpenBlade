"""Validated deployment pipeline.

A deploy is gated: the configuration must be valid *before* anything is deployed,
and the runtime topology must verify *after*, or the deploy is not promoted. This
is the same refuse-on-blocking-finding discipline as the CI gate, applied at
deploy time so a bad config or an unwired runtime never silently goes live.

The core is a pure sequencer over three callables so it is fully testable without
Docker or a live host; the CLI (scripts/deploy.py) supplies real stages.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import Enum


class Stage(str, Enum):
    PRECHECK = "precheck"
    DEPLOY = "deploy"
    POSTCHECK = "postcheck"
    ROLLBACK = "rollback"


@dataclass
class StageResult:
    stage: Stage
    ok: bool
    detail: str = ""
    findings: list[str] = field(default_factory=list)


@dataclass
class DeployReport:
    results: list[StageResult] = field(default_factory=list)
    promoted: bool = False

    @property
    def ok(self) -> bool:
        return self.promoted

    def to_dict(self) -> dict[str, object]:
        return {
            "promoted": self.promoted,
            "results": [asdict(r) | {"stage": r.stage.value} for r in self.results],
        }


def run_deploy_pipeline(
    *,
    precheck: Callable[[], StageResult],
    deploy: Callable[[], StageResult],
    postcheck: Callable[[], StageResult],
) -> DeployReport:
    """Precheck -> deploy -> postcheck, stopping at the first failure.

    - precheck fails  -> refuse to deploy (deploy/postcheck never run).
    - deploy fails     -> not promoted (postcheck never runs).
    - postcheck passes -> promoted. Anything else -> not promoted.
    """
    report = DeployReport()

    pre = precheck()
    report.results.append(pre)
    if not pre.ok:
        return report

    dep = deploy()
    report.results.append(dep)
    if not dep.ok:
        return report

    post = postcheck()
    report.results.append(post)
    report.promoted = post.ok
    return report


# --- Read-only appliance postcheck -------------------------------------------

# A probe takes (method, path) and returns an HTTP status code (same contract as
# openblade.topology). These are the appliance checks run after a deploy; every
# one is a GET so a postcheck can never move a cartridge or start a job.
READ_ONLY_APPLIANCE_PROBES: tuple[tuple[str, str], ...] = (
    ("GET", "/inventory/"),
    ("GET", "/jobs/recovery"),
)
_READ_ONLY_METHODS = frozenset({"GET", "HEAD"})


class ReadOnlyProbe:
    """Wraps a probe so that only GET/HEAD requests are ever sent.

    A non-read-only request is never forwarded: it is recorded in ``skipped`` and
    answered with 405 (which topology treats as "present, not verified"), so the
    caller can report exactly which checks were not exercised.
    """

    def __init__(self, probe: Callable[[str, str], int]) -> None:
        self._probe = probe
        self.skipped: list[str] = []

    def __call__(self, method: str, path: str) -> int:
        if method.upper() not in _READ_ONLY_METHODS:
            self.skipped.append(f"{method.upper()} {path}")
            return 405
        return self._probe(method.upper(), path)


def run_read_only_appliance_checks(probe: Callable[[str, str], int]) -> StageResult:
    """GET each appliance endpoint; any non-2xx status is a blocking finding."""
    guarded = probe if isinstance(probe, ReadOnlyProbe) else ReadOnlyProbe(probe)
    failures = [
        f"{method} {path} -> {status}"
        for method, path in READ_ONLY_APPLIANCE_PROBES
        if not 200 <= (status := guarded(method, path)) < 300
    ]
    ok = not failures
    return StageResult(
        Stage.POSTCHECK,
        ok,
        "read-only appliance checks OK" if ok else f"appliance checks failed: {failures}",
        findings=failures,
    )


# --- Rollback -----------------------------------------------------------------

ROLLBACK_NOTICE = (
    "!!! ROLLBACK REDEPLOYS APPLICATION CODE ONLY !!!\n"
    "Physical tape state is NEVER rolled back: cartridges moved, written or loaded\n"
    "by the rolled-back version stay where they are. Review reconciliation at\n"
    "GET /jobs/recovery before resuming operations."
)

_DIGEST_RE = re.compile(r"^(?:(?P<image>[a-z0-9][a-z0-9._/:-]*)@)?(?P<digest>sha256:[0-9a-f]{64})$")


class InvalidDigestError(ValueError):
    """The rollback target is not an immutable ``sha256:`` digest reference."""


def parse_image_digest(ref: str, default_image: str) -> str:
    """Return ``image@sha256:...`` for a bare digest or full digest reference.

    Tags are refused: a rollback must name the exact bytes that ran before.
    """
    m = _DIGEST_RE.match(ref.strip())
    if m is None:
        raise InvalidDigestError(
            "rollback target must be sha256:<64 hex> or <image>@sha256:<64 hex>"
        )
    return f"{m.group('image') or default_image}@{m.group('digest')}"


def run_rollback(
    *,
    image_ref: str,
    redeploy: Callable[[str], StageResult],
    notify: Callable[[str], None],
) -> StageResult:
    """Redeploy ``image_ref`` (application code only), announcing the tape caveat.

    The notice is emitted before the redeploy and again after it, whatever the
    outcome, so it cannot be missed in a long log.
    """
    notify(ROLLBACK_NOTICE)
    result = redeploy(image_ref)
    notify(ROLLBACK_NOTICE)
    return StageResult(Stage.ROLLBACK, result.ok, result.detail, findings=list(result.findings))
