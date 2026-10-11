"""`openblade fuse mount --hydrate` builds the production data plane."""

from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from openblade.catalog.db import get_session, init_db
from openblade.catalog.repository import CatalogRepository
from openblade.cli import fuse_and_health
from openblade.config import load_config
from openblade.fuse.filesystem import CatalogFilesystem
from openblade.fuse.hydration import Hydrator


def _context(tmp_path: Path, **config_overrides: Any) -> SimpleNamespace:
    init_db(f"sqlite:///{tmp_path / 'cat.db'}")
    config = replace(
        load_config(),
        cache_dir=str(tmp_path / "cache"),
        restore_dir=str(tmp_path / "restore"),
        **config_overrides,
    )
    return SimpleNamespace(
        config=config,
        catalog=CatalogRepository(get_session()),
        library=object(),
        ltfs=object(),
        queue=object(),
    )


def test_build_data_plane_reads_timeouts_from_settings(tmp_path: Path) -> None:
    ctx = _context(tmp_path, fuse_hydrate_timeout_s=7.5, fuse_batch_window_ms=40)

    plane = fuse_and_health.build_data_plane(
        ctx, CatalogFilesystem(ctx.catalog, ctx.config.cache_dir)
    )

    assert isinstance(plane, Hydrator)
    assert plane.batch_window_s == pytest.approx(0.04)
    assert plane.engine.resume_timeout_s == 7.5  # type: ignore[attr-defined]


def test_worker_restore_never_uses_the_callers_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _context(tmp_path)
    group = ctx.catalog.create_volume_group("g")
    ctx.catalog.create_file_record("/a.bin", 3, "0" * 64, group.id)
    plane = fuse_and_health.build_data_plane(
        ctx, CatalogFilesystem(ctx.catalog, ctx.config.cache_dir)
    )
    seen: list[Any] = []

    class StubService:
        def __init__(self, library: Any, ltfs: Any, catalog: Any, queue: Any) -> None:
            seen.append(catalog)

        def enqueue(self, path: str, dest: Path) -> Any:
            return SimpleNamespace(id="j1", state="completed")

        def enqueue_batch(self, requests: list[Any]) -> Any:
            return SimpleNamespace(id="j1", state="completed"), SimpleNamespace(items=[])

    monkeypatch.setattr(fuse_and_health, "RestoreService", StubService)
    worker = threading.Thread(target=plane.engine.restore_batch, args=("T1", ["/a.bin"]))
    worker.start()
    worker.join(5)

    assert len(seen) == 1
    assert seen[0].session is not ctx.catalog.session


def test_mount_with_hydrate_passes_data_plane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _context(tmp_path)
    calls: dict[str, Any] = {}
    monkeypatch.setattr("openblade.cli.main._get_context", lambda: ctx)
    monkeypatch.setattr(fuse_and_health, "mount_catalog", lambda fs, mp, **kw: calls.update(kw))

    result = CliRunner().invoke(fuse_and_health.fuse_app, [str(tmp_path), "--hydrate"])

    assert result.exit_code == 0, result.output
    assert isinstance(calls["data_plane"], Hydrator)


def test_mount_without_hydrate_has_no_data_plane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _context(tmp_path)
    calls: dict[str, Any] = {}
    monkeypatch.setattr("openblade.cli.main._get_context", lambda: ctx)
    monkeypatch.setattr(fuse_and_health, "mount_catalog", lambda fs, mp, **kw: calls.update(kw))

    result = CliRunner().invoke(fuse_and_health.fuse_app, [str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert calls["data_plane"] is None
