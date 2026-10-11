"""Ingest must never format a tape on a real backend (reviewer BLOCKER, fleet4)."""

from pathlib import Path
from typing import Any

import pytest

from openblade.bootstrap import get_context
from openblade.nas.ingest import register_archive_plan, start_ingest_job
from openblade.nas.types import DatasetStatus
from tests.unit.test_nas_ingest import _make_plan, _run_job, _setup_service


def _start(tmp_path: Path) -> tuple[Any, str]:
    service, cache_root = _setup_service(tmp_path)
    job = start_ingest_job(
        plan=register_archive_plan(_make_plan(cache_root)),
        dataset_name="dataset-a",
        pool_id="pool-1",
        nas_service=service,
        cache_drive_id="cache-1",
    )
    return service, job.job_id


def _record_format_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    ltfs = get_context().ltfs
    real_format = ltfs.format
    calls: list[str] = []

    def recording_format(barcode: str, confirmation: Any) -> Any:
        calls.append(barcode)
        return real_format(barcode, confirmation)

    monkeypatch.setattr(ltfs, "format", recording_format)
    return calls


def test_real_backend_unformatted_tape_is_refused_and_never_formatted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, job_id = _start(tmp_path)
    calls = _record_format_calls(monkeypatch)
    # Context is already a simulator; only the backend detection flips to REAL.
    monkeypatch.setenv("OPENBLADE_BACKEND", "real")
    monkeypatch.delenv("OPENBLADE_REAL_HARDWARE_ENABLED", raising=False)

    result = _run_job(service, job_id)

    assert calls == []
    assert result.status is DatasetStatus.FAILED
    assert any(
        "not LTFS-formatted" in error and "/ltfs/format" in error for error in result.errors
    ), result.errors
    assert all(
        not record.status.name.startswith("OFFLINE")
        for record in service.list_file_records(result.dataset_id)
    )


def test_simulator_unformatted_tape_is_auto_formatted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, job_id = _start(tmp_path)
    calls = _record_format_calls(monkeypatch)
    monkeypatch.setenv("OPENBLADE_BACKEND", "mock")

    result = _run_job(service, job_id)

    assert result.status is DatasetStatus.ARCHIVED
    assert len(calls) == 1
    assert get_context().ltfs.ensure_tape(calls[0]).formatted
