"""Tests for runtime topology verification (operability gate).

Reproduces the "required endpoint/consumer has no registration" and
"worker/service not wired" failure classes.
"""

from __future__ import annotations

from types import SimpleNamespace

from openblade.topology import (
    REQUIRED_CONTEXT_MEMBERS,
    is_healthy_topology,
    verify_topology,
)


def _wired_context() -> SimpleNamespace:
    return SimpleNamespace(**{member: object() for member in REQUIRED_CONTEXT_MEMBERS})


def _ok_probe(_method: str, _path: str) -> int:
    return 200


def test_healthy_topology_passes() -> None:
    findings = verify_topology(probe=_ok_probe, context=_wired_context(), emulator_urls=["http://e:8010"])
    assert is_healthy_topology(findings)


def test_auth_gated_endpoints_count_as_present() -> None:
    # 401/403 mean "registered + auth working", not a topology failure.
    findings = verify_topology(probe=lambda m, p: 401, context=_wired_context(), emulator_urls=["x"])
    assert is_healthy_topology(findings)


def test_missing_endpoint_blocks() -> None:
    def probe(_m: str, path: str) -> int:
        return 404 if path == "/jobs/" else 200
    findings = verify_topology(probe=probe, context=_wired_context(), emulator_urls=["x"])
    assert not is_healthy_topology(findings)
    assert any(f.code == "missing_endpoint" and "/jobs/" in f.message for f in findings)


def test_server_error_endpoint_blocks() -> None:
    findings = verify_topology(probe=lambda m, p: 503, context=_wired_context(), emulator_urls=["x"])
    assert not is_healthy_topology(findings)
    assert any(f.code == "endpoint_error" for f in findings)


def test_unwired_worker_blocks() -> None:
    ctx = _wired_context()
    ctx.worker = None  # a "queue with no consumer" analog
    findings = verify_topology(probe=_ok_probe, context=ctx, emulator_urls=["x"])
    assert not is_healthy_topology(findings)
    assert any(f.code == "unwired_component" and "worker" in f.message for f in findings)


def test_no_emulator_fleet_is_warning_only() -> None:
    findings = verify_topology(probe=_ok_probe, context=_wired_context(), emulator_urls=[])
    assert is_healthy_topology(findings)  # warning does not block
    assert any(f.code == "no_emulator_fleet" for f in findings)
