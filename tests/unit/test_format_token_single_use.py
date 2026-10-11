"""Format safety tokens are single-use and consumed BEFORE the destructive job.

Fakes only: the format job is replaced, no device is touched.
"""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from openblade.catalog.db import get_session, init_db
from openblade.catalog.repository import CatalogRepository
from openblade.domain.errors import FormatRequiresConfirmationError
from openblade.domain.models import OperationResult
from openblade.domain.policies import SafetyToken
from openblade.jobs import format as format_module
from openblade.jobs.format import FormatService

BARCODE = "SCR001L9"


class _Catalog:
    """Delegates token methods to a real repository; stubs the rest."""

    def __init__(self, repo: CatalogRepository, barrier: threading.Barrier | None) -> None:
        self.repo = repo
        self.barrier = barrier
        self.session = SimpleNamespace(commit=lambda: None)
        self.fail_delete = False

    def get_safety_token(self, value: str) -> SafetyToken | None:
        token = self.repo.get_safety_token(value)
        if self.barrier is not None:
            # Both confirms have read the (valid) token before either proceeds.
            self.barrier.wait(timeout=5)
        return token

    def delete_safety_token(self, value: str) -> bool:
        if self.fail_delete:
            return False
        return self.repo.delete_safety_token(value)

    def add_cartridge(self, barcode: str) -> Any:
        return SimpleNamespace(formatted=False, used_bytes=1)


@pytest.fixture
def repo(tmp_path: Path) -> CatalogRepository:
    init_db(f"sqlite:///{tmp_path / 'catalog.db'}")
    return CatalogRepository(get_session())


@pytest.fixture
def job_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def fake_job(barcode: str, *_: object, **__: object) -> OperationResult:
        calls.append(barcode)
        return OperationResult(success=True, message="formatted (fake)")

    monkeypatch.setattr(format_module, "run_format_job", fake_job)
    return calls


def _service(catalog: _Catalog) -> FormatService:
    return FormatService(catalog, SimpleNamespace(), SimpleNamespace())  # type: ignore[arg-type]  # fakes stand in for typed backends


def _armed_token(repo: CatalogRepository) -> SafetyToken:
    token = SafetyToken.generate("format", BARCODE)
    repo.save_safety_token(token)
    return token


def test_repository_delete_is_single_use(repo: CatalogRepository) -> None:
    token = _armed_token(repo)
    assert repo.delete_safety_token(token.token) is True
    assert repo.delete_safety_token(token.token) is False


def test_sequential_second_confirm_is_refused(
    repo: CatalogRepository, job_calls: list[str]
) -> None:
    token = _armed_token(repo)
    service = _service(_Catalog(repo, None))
    service.confirm(BARCODE, token.token)
    with pytest.raises(FormatRequiresConfirmationError):
        service.confirm(BARCODE, token.token)
    assert job_calls == [BARCODE]


def test_concurrent_confirms_with_one_token_run_the_job_once(
    repo: CatalogRepository, job_calls: list[str]
) -> None:
    token = _armed_token(repo)
    service = _service(_Catalog(repo, threading.Barrier(2)))
    outcomes: list[str] = []

    def confirm() -> None:
        try:
            service.confirm(BARCODE, token.token)
            outcomes.append("ok")
        except FormatRequiresConfirmationError:
            outcomes.append("refused")

    threads = [threading.Thread(target=confirm) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert sorted(outcomes) == ["ok", "refused"]
    assert job_calls == [BARCODE]


def test_failed_delete_refuses_and_never_runs_the_job(
    repo: CatalogRepository, job_calls: list[str]
) -> None:
    token = _armed_token(repo)
    catalog = _Catalog(repo, None)
    catalog.fail_delete = True
    with pytest.raises(FormatRequiresConfirmationError):
        _service(catalog).confirm(BARCODE, token.token)
    assert job_calls == []


def test_failed_job_does_not_rearm_the_token(
    repo: CatalogRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = _armed_token(repo)

    def boom(*_: object, **__: object) -> OperationResult:
        raise RuntimeError("fake drive failure")

    monkeypatch.setattr(format_module, "run_format_job", boom)
    with pytest.raises(RuntimeError):
        _service(_Catalog(repo, None)).confirm(BARCODE, token.token)
    assert repo.delete_safety_token(token.token) is False
