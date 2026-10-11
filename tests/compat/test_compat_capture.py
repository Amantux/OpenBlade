"""Unit tests for tools/compat_capture.py: sanitizer rules, provenance refusal, determinism."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

_PATH = Path(__file__).resolve().parents[2] / "tools" / "compat_capture.py"
_spec = importlib.util.spec_from_file_location("compat_capture", _PATH)
assert _spec and _spec.loader
cc = importlib.util.module_from_spec(_spec)
sys.modules["compat_capture"] = cc
_spec.loader.exec_module(cc)

SECRET = "s3cr3t-T0KEN-abcdef0123456789"


def _dirty_case() -> dict[str, Any]:
    return {
        "id": "x",
        "request": {
            "method": "POST",
            "path": "/aml/users/login",
            "headers": {"Authorization": f"Bearer {SECRET}", "Cookie": f"sessionID={SECRET}"},
            "json": {"name": "admin", "password": "hunter2-plain", "newPassword": "pw2"},
        },
        "observed": {
            "headers": {"set-cookie": f"sessionID={SECRET}; Path=/; HttpOnly; SameSite=lax"},
            "body": {
                "sessionId": SECRET,
                "access_token": SECRET,
                "hardwareSerialNumber": "QTM-SN-998877",
                "partnerBladeSerial": "PB-1234",
                "link": "https://i3-lab.example.com:8443/aml/x?y=1",
                "peer": "connect to 10.20.30.40:3260 ok",
                "when": "2026-10-11T09:15:02.123Z",
                "httpDate": "Sun, 11 Oct 2026 09:15:02 GMT",
                "nested": [{"session_token": SECRET, "count": 3, "ok": True}],
            },
        },
    }


def test_sanitize_scrubs_every_secret_class() -> None:
    clean = cc.sanitize(_dirty_case())
    text = json.dumps(clean)
    for leaked in (SECRET, "hunter2-plain", "pw2", "QTM-SN-998877", "PB-1234"):
        assert leaked not in text, f"leaked {leaked!r}"
    for leaked in ("i3-lab.example.com", "10.20.30.40", "2026-10-11T09", "09:15:02 GMT"):
        assert leaked not in text, f"leaked {leaked!r}"
    body = clean["observed"]["body"]
    assert clean["request"]["json"]["password"] == cc.REDACTED
    assert clean["request"]["json"]["name"] == "admin"
    assert clean["request"]["headers"]["Authorization"] == cc.TOKEN
    assert body["link"] == f"https://{cc.HOST}/aml/x?y=1"
    assert body["when"] == cc.TIMESTAMP
    assert body["nested"][0] == {"session_token": cc.TOKEN, "count": 3, "ok": True}
    # Cookie attributes survive; only the value is replaced.
    assert clean["observed"]["headers"]["set-cookie"] == (
        f"sessionID={cc.TOKEN}; Path=/; HttpOnly; SameSite=lax"
    )


def test_sanitize_is_pure_and_idempotent() -> None:
    dirty = _dirty_case()
    before = json.dumps(dirty, sort_keys=True)
    once = cc.sanitize(dirty)
    assert json.dumps(dirty, sort_keys=True) == before, "input was mutated"
    assert cc.sanitize(once) == once


def _resp(body: dict[str, Any], status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        json=body,
        headers={"set-cookie": f"sessionID={SECRET}; Path=/", "date": "Sun, 11 Oct 2026"},
        request=httpx.Request("GET", "http://appliance.local/aml/x"),
    )


RECIPE = {"id": "r1", "profile": "scalar-i3-341g", "source": "inferred", "request": {}}


def test_build_case_is_deterministic_and_enforces_only_unsanitized_keys() -> None:
    body = {"status": "ok", "sessionId": SECRET, "serialNumber": "SN1"}
    a = cc.dumps_case(cc.build_case(RECIPE, _resp(body), source="self-captured"))
    b = cc.dumps_case(cc.build_case(RECIPE, _resp(body), source="self-captured"))
    assert a == b
    case = json.loads(a)
    assert SECRET not in a and "SN1" not in a
    assert case["source"] == "self-captured"
    assert case["expected"]["json_contains"] == {"status": "ok"}
    assert "date" not in case["observed"]["headers"]


@pytest.mark.parametrize(
    "kwargs",
    [{}, {"appliance_firmware": "341G"}, {"attest": "Jane Operator"}],
    ids=["neither", "firmware-only", "attest-only"],
)
def test_captured_without_attestation_is_refused(kwargs: dict[str, str]) -> None:
    with pytest.raises(cc.CaptureRefusedError):
        cc.build_case(RECIPE, _resp({}), source="captured", **kwargs)


def test_captured_with_attestation_records_provenance() -> None:
    case = cc.build_case(
        RECIPE, _resp({}), source="captured", appliance_firmware="341G", attest="Jane Operator"
    )
    assert (case["source"], case["firmware"], case["attested_by"]) == (
        "captured",
        "341G",
        "Jane Operator",
    )


def test_cli_refusal_writes_nothing(tmp_path: Path) -> None:
    recipe = tmp_path / "r.json"
    recipe.write_text(json.dumps({**RECIPE, "request": {"method": "GET", "path": "/x"}}))
    out = tmp_path / "out"

    def factory() -> httpx.Client:
        return httpx.Client(
            base_url="http://t", transport=httpx.MockTransport(lambda r: httpx.Response(200))
        )

    argv = ["capture", "--base-url", "http://t", "--recipe", str(recipe), "--out", str(out)]
    assert cc.main([*argv, "--source", "captured"], factory=factory) == 2
    assert not out.exists()
    assert cc.main(argv, factory=factory) == 0
    assert (out / "r1.json").exists()
