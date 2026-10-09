"""Release-pipeline deploy stages: read-only appliance postcheck and rollback.

Safety invariants under test:
- the read-only postcheck never sends a non-GET request to the appliance;
- rollback redeploys application code only, has no option that could touch tape
  state, refuses a mutable tag, and always prints the tape-state notice.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from openblade.deploy import (
    READ_ONLY_APPLIANCE_PROBES,
    ROLLBACK_NOTICE,
    InvalidDigestError,
    ReadOnlyProbe,
    Stage,
    StageResult,
    parse_image_digest,
    run_read_only_appliance_checks,
    run_rollback,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import deploy as deploy_cli  # noqa: E402 - scripts/ is not a package; path set just above

DIGEST = "sha256:" + "a" * 64


def _recording_probe(status: int = 200) -> tuple[list[tuple[str, str]], ReadOnlyProbe]:
    sent: list[tuple[str, str]] = []

    def probe(method: str, path: str) -> int:
        sent.append((method, path))
        return status

    return sent, ReadOnlyProbe(probe)


def test_appliance_checks_are_all_gets() -> None:
    assert {m for m, _ in READ_ONLY_APPLIANCE_PROBES} == {"GET"}
    assert {p for _, p in READ_ONLY_APPLIANCE_PROBES} == {"/inventory/", "/jobs/recovery"}


def test_appliance_checks_pass_on_2xx() -> None:
    sent, probe = _recording_probe(200)
    result = run_read_only_appliance_checks(probe)
    assert result.ok and result.stage is Stage.POSTCHECK
    assert sent == list(READ_ONLY_APPLIANCE_PROBES)


@pytest.mark.parametrize("status", [404, 500, 503, 307])
def test_appliance_checks_fail_on_non_2xx(status: int) -> None:
    _, probe = _recording_probe(status)
    result = run_read_only_appliance_checks(probe)
    assert not result.ok
    assert len(result.findings) == 2


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "post"])
def test_read_only_probe_never_forwards_mutating_requests(method: str) -> None:
    sent, probe = _recording_probe()
    assert probe(method, "/aml/users/login") == 405
    assert sent == []
    assert probe.skipped == [f"{method.upper()} /aml/users/login"]


def test_live_postcheck_read_only_mode_sends_only_gets(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []

    class _Resp:
        status = 200

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *_: object) -> None:
            return None

    def fake_urlopen(req: object, timeout: float) -> _Resp:
        sent.append(req.get_method())  # type: ignore[attr-defined]
        return _Resp()

    monkeypatch.setattr(deploy_cli.urllib.request, "urlopen", fake_urlopen)
    result = deploy_cli._postcheck_live("http://appliance", read_only_appliance_checks=True)
    assert sent and set(sent) == {"GET"}
    assert "not sent (non-GET)" in result.detail


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        (DIGEST, f"ghcr.io/amantux/openblade@{DIGEST}"),
        (f"ghcr.io/other/img@{DIGEST}", f"ghcr.io/other/img@{DIGEST}"),
    ],
)
def test_parse_image_digest_accepts_digests(ref: str, expected: str) -> None:
    assert parse_image_digest(ref, "ghcr.io/amantux/openblade") == expected


@pytest.mark.parametrize("ref", ["latest", "ghcr.io/amantux/openblade:0.5.0", "sha256:abc", ""])
def test_parse_image_digest_refuses_tags(ref: str) -> None:
    with pytest.raises(InvalidDigestError):
        parse_image_digest(ref, "ghcr.io/amantux/openblade")


def test_rollback_prints_notice_and_redeploys_image_only() -> None:
    notices: list[str] = []
    redeployed: list[str] = []

    def redeploy(ref: str) -> StageResult:
        redeployed.append(ref)
        return StageResult(Stage.DEPLOY, False, "exited 1")

    result = run_rollback(image_ref=f"img@{DIGEST}", redeploy=redeploy, notify=notices.append)
    assert redeployed == [f"img@{DIGEST}"]
    assert result.stage is Stage.ROLLBACK and not result.ok
    assert notices == [ROLLBACK_NOTICE, ROLLBACK_NOTICE]
    assert "tape state is NEVER rolled back" in ROLLBACK_NOTICE
    assert "/jobs/recovery" in ROLLBACK_NOTICE


def test_rollback_cli_has_no_tape_state_option(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        deploy_cli.rollback_main(["--help"])
    help_text = capsys.readouterr().out.lower()
    for forbidden in ("tape", "cartridge", "inventory", "unload", "move", "slot", "drive"):
        assert f"--{forbidden}" not in help_text
    assert set(help_text.split()) & {"--to", "--image", "--deploy-cmd", "--json"} == {
        "--to",
        "--image",
        "--deploy-cmd",
        "--json",
    }


def test_rollback_cli_refuses_tag(capsys: pytest.CaptureFixture[str]) -> None:
    rc = deploy_cli.main(["rollback", "--to", "latest", "--deploy-cmd", "true"])
    assert rc == 2
    assert "rollback refused" in capsys.readouterr().err


def test_rollback_cli_passes_digest_and_prints_notice(capsys: pytest.CaptureFixture[str]) -> None:
    cmd = f"{sys.executable} -c \"import os,sys; sys.exit(0 if os.environ['OPENBLADE_IMAGE'].endswith('{DIGEST}') else 3)\""
    rc = deploy_cli.main(["rollback", "--to", DIGEST, "--deploy-cmd", cmd])
    assert rc == 0
    assert capsys.readouterr().err.count("Physical tape state is NEVER rolled back") == 2
