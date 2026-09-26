"""jobs.error is served UNAUTHENTICATED (GET /jobs). Raw exception text —
CommandError carries argv + tool stderr — must never reach that field.
Typed OpenBlade errors carry curated messages and pass through."""

import contextlib

import pytest

from openblade.domain.errors import OpenBladeError, safe_job_error
from openblade.hardware.runner import CommandError


def test_command_error_stderr_never_reaches_the_field() -> None:
    exc = CommandError(
        ["mkltfs", "-d", "/dev/nst0"], 1, "SECRET-DEVICE-PATH /dev/nst0 permission denied"
    )
    msg = safe_job_error(exc)
    assert "SECRET-DEVICE-PATH" not in msg
    assert "/dev/nst0" not in msg
    assert "mkltfs" not in msg
    assert "CommandError" in msg  # class name only


def test_oserror_is_reduced_to_class_name() -> None:
    msg = safe_job_error(OSError(28, "No space left on device: /data/x"))
    assert "/data" not in msg
    assert "OSError" in msg


def test_typed_domain_error_message_passes_through() -> None:
    class DemoError(OpenBladeError):
        pass

    assert (
        safe_job_error(DemoError("Tape OB0001L8 is not formatted"))
        == "Tape OB0001L8 is not formatted"
    )


def test_run_job_records_sanitized_error() -> None:
    from openblade.jobs.queue import JobQueue

    queue = JobQueue()
    job = queue.create_job("archive", {})

    def boom():
        raise CommandError(["mtx", "status"], 1, "raw stderr LEAK")

    with contextlib.suppress(CommandError):
        queue.run_job(job, boom)
    failed = queue.get_job(job.id)
    assert failed.error is not None
    assert "LEAK" not in failed.error
    assert "CommandError" in failed.error


def test_aml_move_error_detail_is_sanitized() -> None:
    """The AML moveMedium fallback handler serves detail= at a boundary that
    is unauthenticated by default — raw exception text (argv, stderr, paths)
    must reduce to a class name there too."""
    import inspect

    from openblade.api import routes_aml_operations as mod

    src = inspect.getsource(mod)
    assert "detail=str(exc)" not in src
    assert src.count("detail=safe_job_error(exc)") >= 2


def test_remote_library_probe_error_is_curated() -> None:
    """The remote-library probe reports "offline" for every failure mode, but the
    text it returns used to be str(exc) on a bare `except Exception`: httpx carries
    the probe URL, and ConnectError wraps OS-level text. The class name is the whole
    payload the client gets."""
    import asyncio

    import httpx

    from openblade.api.routes_proxy import RemoteLibraryProbeRequest, probe_remote_library

    class _ExplodingClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info) -> None:
            return None

        async def post(self, *args, **kwargs):
            raise RuntimeError("connect to http://remote:8000 failed: SECRET-DETAIL")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(httpx, "AsyncClient", _ExplodingClient)
    try:
        result = asyncio.run(
            probe_remote_library(
                "lib-1",
                RemoteLibraryProbeRequest(host="remote", port=8000, username="u", password="p"),
                None,
            )
        )
    finally:
        monkeypatch.undo()

    assert result["status"] == "offline"
    assert "SECRET-DETAIL" not in result["error"]
    assert "remote:8000" not in result["error"]
    assert "RuntimeError" in result["error"]


def test_decode_password_hash_rejects_malformed_hashes_without_swallowing_defects() -> None:
    """_decode_password_hash() narrowed from `except Exception` to the three ways a
    STORED hash can be malformed (non-base64, non-ASCII, NULL column). All three
    still answer "not a hash" rather than raising into the login path."""
    from openblade.api.aml_state import is_password_hash

    assert is_password_hash("not base64!!!") is False
    assert is_password_hash("\u00e9\u00e9\u00e9") is False
    assert is_password_hash("") is False
    assert is_password_hash(None) is False  # type: ignore[arg-type]
    assert is_password_hash("aGVsbG8=") is False  # valid base64, wrong length
