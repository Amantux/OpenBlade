"""Validated deployment pipeline.

A deploy is gated: the configuration must be valid *before* anything is deployed,
and the runtime topology must verify *after*, or the deploy is not promoted. This
is the same refuse-on-blocking-finding discipline as the CI gate, applied at
deploy time so a bad config or an unwired runtime never silently goes live.

The core is a pure sequencer over three callables so it is fully testable without
Docker or a live host; the CLI (scripts/deploy.py) supplies real stages.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import Enum


class Stage(str, Enum):
    PRECHECK = "precheck"
    DEPLOY = "deploy"
    POSTCHECK = "postcheck"


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
