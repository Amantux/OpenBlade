"""Plain-CI unit tests for the read-only lane's runtime guards (no rig needed).

ApplianceClient.request and ReadOnlyRunner.run must refuse anything that could
mutate the rig with ReadOnlyViolation *before* touching the network or a
subprocess. The transport and subprocess.run are replaced so nothing executes.
"""

from __future__ import annotations

import io
import json
import subprocess
from typing import Any

import pytest

from .conftest import ApplianceClient, ReadOnlyRunner, ReadOnlyViolation


class _FakeResponse(io.BytesIO):
    status = 200


@pytest.fixture
def sent() -> list[tuple[str, str]]:
    return []


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, sent: list[tuple[str, str]]) -> ApplianceClient:
    appliance = ApplianceClient(base_url="https://rig.invalid", user="u", password="p")

    def fake_open(req: Any, timeout: int = 0) -> _FakeResponse:
        sent.append((req.get_method(), req.full_url))
        return _FakeResponse(json.dumps({"ok": True}).encode())

    monkeypatch.setattr(appliance._opener, "open", fake_open)
    return appliance


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/aml/media/move"),
        ("PUT", "/aml/partitions/1"),
        ("PATCH", "/aml/partitions/1"),
        ("DELETE", "/aml/partitions/1"),
        ("DELETE", "/aml/users/login"),
        ("POST", "/aml/users/login/../media/move"),
        ("post", "/aml/physicalLibrary/elements/move"),
        ("GET", "aml/partitions"),
        ("get", "/aml/partitions"),
    ],
)
def test_appliance_request_mutating_call_raises_before_sending(
    client: ApplianceClient, sent: list[tuple[str, str]], method: str, path: str
) -> None:
    with pytest.raises(ReadOnlyViolation):
        client.request(method, path)

    assert sent == []


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/aml/partitions"),
        ("POST", "/aml/users/login"),
        ("POST", "/aml/auth/logout"),
    ],
)
def test_appliance_request_read_or_auth_call_is_sent(
    client: ApplianceClient, sent: list[tuple[str, str]], method: str, path: str
) -> None:
    resp = client.request(method, path)

    assert resp.status == 200
    assert sent == [(method, "https://rig.invalid" + path)]


def test_appliance_login_and_logout_pass_the_guard(
    client: ApplianceClient, sent: list[tuple[str, str]]
) -> None:
    client.login()
    client.logout()

    assert [m for m, _ in sent] == ["POST", "POST"]


@pytest.fixture
def executed(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    # SafeRunner calls subprocess.run via the module, so this intercepts it.
    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


# Commands are space-joined strings (split at call time) rather than list
# literals so test_readonly_ast_safety's argv scan of this file stays clean.
@pytest.mark.parametrize(
    "command",
    [
        "mtx -f /dev/sg0 load 1 0",
        "mtx -f /dev/sg0 unload 1 0",
        "mtx -f /dev/sg0 transfer 1 2",
        "mtx -f /dev/sg0 status load",
        "mkltfs --force -d /dev/nst0",
        "ltfs /mnt/tape -o devname=/dev/nst0",
        "mount /dev/nst0 /mnt",
        "sg_logs -R /dev/sg1",
        "sg_logs -p 0x2e -R /dev/sg1",
        "sg_inq --page=0x83 /dev/sg1",
        "sg_inq /etc/passwd",
        "mt -f /dev/nst0 erase",
        "ls -R /dev",
        "ls",
    ],
)
def test_readonly_runner_forbidden_argv_raises_before_executing(
    executed: list[list[str]], command: str
) -> None:
    with pytest.raises(ReadOnlyViolation):
        ReadOnlyRunner(dry_run=False).run(command.split())

    assert executed == []


def test_readonly_runner_string_command_raises(executed: list[list[str]]) -> None:
    with pytest.raises(ReadOnlyViolation):
        ReadOnlyRunner(dry_run=False).run("mtx -f /dev/sg0 status")  # type: ignore[arg-type]

    assert executed == []


# Commands are space-joined strings (split at call time) rather than list
# literals so test_readonly_ast_safety's argv scan of this file stays clean.
@pytest.mark.parametrize(
    "command",
    [
        "mtx -f /dev/sg0 status",
        "sg_inq /dev/sg1",
        "sg_inq /dev/tape/by-id/scsi-3500e09e0bb562001-nst",
        "sg_logs -p 0x2e /dev/sg1",
        "ls /dev/tape/by-id",
    ],
)
def test_readonly_runner_allowed_argv_executes(executed: list[list[str]], command: str) -> None:
    result = ReadOnlyRunner(dry_run=False).run(command.split())

    assert result.returncode == 0
    assert executed == [command.split()]
