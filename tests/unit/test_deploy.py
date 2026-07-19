"""Tests for the validated deployment pipeline sequencer.

Reproduces the "bad config went live" and "deployed but unwired" failure classes
and asserts the pipeline refuses to promote — and, critically, that a failing
precheck means the deploy command never runs.
"""

from __future__ import annotations

from openblade.deploy import Stage, StageResult, run_deploy_pipeline


def _result(stage: Stage, ok: bool) -> StageResult:
    return StageResult(stage, ok)


def test_failing_precheck_refuses_to_deploy() -> None:
    calls: list[str] = []

    def precheck() -> StageResult:
        calls.append("pre")
        return _result(Stage.PRECHECK, False)

    def deploy() -> StageResult:
        calls.append("deploy")
        return _result(Stage.DEPLOY, True)

    def postcheck() -> StageResult:
        calls.append("post")
        return _result(Stage.POSTCHECK, True)

    report = run_deploy_pipeline(precheck=precheck, deploy=deploy, postcheck=postcheck)

    assert not report.promoted
    assert calls == ["pre"]  # deploy must NOT run when config is invalid
    assert [r.stage for r in report.results] == [Stage.PRECHECK]


def test_failing_deploy_is_not_promoted_and_skips_postcheck() -> None:
    calls: list[str] = []
    report = run_deploy_pipeline(
        precheck=lambda: (calls.append("pre"), _result(Stage.PRECHECK, True))[1],
        deploy=lambda: (calls.append("deploy"), _result(Stage.DEPLOY, False))[1],
        postcheck=lambda: (calls.append("post"), _result(Stage.POSTCHECK, True))[1],
    )
    assert not report.promoted
    assert calls == ["pre", "deploy"]  # postcheck skipped after a failed deploy


def test_failing_postcheck_is_not_promoted() -> None:
    report = run_deploy_pipeline(
        precheck=lambda: _result(Stage.PRECHECK, True),
        deploy=lambda: _result(Stage.DEPLOY, True),
        postcheck=lambda: _result(Stage.POSTCHECK, False),
    )
    assert not report.promoted
    assert [r.stage for r in report.results] == [Stage.PRECHECK, Stage.DEPLOY, Stage.POSTCHECK]


def test_all_stages_pass_promotes() -> None:
    report = run_deploy_pipeline(
        precheck=lambda: _result(Stage.PRECHECK, True),
        deploy=lambda: _result(Stage.DEPLOY, True),
        postcheck=lambda: _result(Stage.POSTCHECK, True),
    )
    assert report.promoted and report.ok
    assert report.to_dict()["promoted"] is True
