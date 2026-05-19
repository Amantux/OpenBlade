"""Application bootstrap and shared runtime context."""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from openblade.catalog.db import get_session, init_db
from openblade.catalog.repository import CatalogRepository
from openblade.config import BackendMode, OpenBladeConfig, load_config
from openblade.jobs.archive import ArchiveService
from openblade.jobs.format import FormatService
from openblade.jobs.inventory import InventoryService, run_inventory_job
from openblade.jobs.queue import JobQueue
from openblade.jobs.restore import RestoreService
from openblade.jobs.worker import Worker
from openblade.simulator.library import MockLibraryBackend
from openblade.simulator.ltfs_volume import MockLTFSBackend
from openblade.simulator.scenarios import one_drive_twenty_slots_five_cartridges

structlog.configure()

_library: MockLibraryBackend | None = None
_ltfs: MockLTFSBackend | None = None
_catalog: CatalogRepository | None = None


@dataclass
class AppContext:
    config: OpenBladeConfig
    library: MockLibraryBackend
    ltfs: MockLTFSBackend
    catalog: CatalogRepository
    queue: JobQueue
    worker: Worker
    inventory_service: InventoryService
    format_service: FormatService
    archive_service: ArchiveService
    restore_service: RestoreService


def get_library() -> MockLibraryBackend:
    global _library, _ltfs
    if _library is None:
        cfg = load_config()
        if cfg.backend != BackendMode.MOCK:
            raise NotImplementedError("Real hardware backend not yet implemented")
        _library, _ltfs = one_drive_twenty_slots_five_cartridges()
    return _library


def get_ltfs() -> MockLTFSBackend:
    global _ltfs
    if _ltfs is None:
        get_library()
    assert _ltfs is not None
    return _ltfs


def get_catalog() -> CatalogRepository:
    global _catalog
    cfg = load_config()
    init_db(cfg.db_url)
    if _catalog is None:
        _catalog = CatalogRepository(get_session())
    return _catalog


def create_context(config: OpenBladeConfig | None = None) -> AppContext:
    active_config = config or load_config()
    if active_config.backend != BackendMode.MOCK:
        raise NotImplementedError("Real hardware backend not yet implemented")
    init_db(active_config.db_url)
    library, ltfs = one_drive_twenty_slots_five_cartridges()
    catalog = CatalogRepository(get_session())
    run_inventory_job(library, catalog)
    queue = JobQueue()
    worker = Worker(queue)
    return AppContext(
        config=active_config,
        library=library,
        ltfs=ltfs,
        catalog=catalog,
        queue=queue,
        worker=worker,
        inventory_service=InventoryService(library),
        format_service=FormatService(catalog, library, ltfs),
        archive_service=ArchiveService(library, ltfs, catalog, queue),
        restore_service=RestoreService(library, ltfs, catalog, queue),
    )


_CONTEXT: AppContext | None = None


def get_context() -> AppContext:
    global _CONTEXT
    if _CONTEXT is None:
        _CONTEXT = create_context()
    return _CONTEXT


def reset_context(context: AppContext | None = None) -> AppContext:
    global _CONTEXT, _library, _ltfs, _catalog
    _CONTEXT = context or create_context()
    _library = _CONTEXT.library
    _ltfs = _CONTEXT.ltfs
    _catalog = _CONTEXT.catalog
    return _CONTEXT
