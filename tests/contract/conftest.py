"""Backend pairings for the behavioural contract suite.

Every contract test receives a ``BackendPair`` and talks to it ONLY through the
``LibraryBackend`` / ``LTFSBackend`` Protocols. A new backend must pass this suite
before it may be selected via ``OPENBLADE_BACKEND`` (docs/test-plan.md).
"""

from __future__ import annotations

import inspect
import os
from collections.abc import Callable, Generator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest

from openblade.catalog.db import get_session, init_db
from openblade.catalog.repository import CatalogRepository
from openblade.domain.backends import LibraryBackend, LTFSBackend
from openblade.domain.policies import FormatConfirmation, SafetyToken
from openblade.jobs.inventory import InventoryService
from openblade.jobs.scheduler import DriveScheduler
from openblade.simulator.library import MockLibraryBackend
from openblade.simulator.ltfs_volume import MockLTFSBackend

CAPACITY = 50 * 1024 * 1024
SIM_BARCODES = ["CTR001L8", "CTR002L8", "CTR003L8"]


@dataclass
class BackendPair:
    name: str
    library: LibraryBackend
    ltfs: LTFSBackend
    num_drives: int
    barcodes: list[str]

    def format_all(self) -> None:
        for barcode in self.barcodes:
            self.ltfs.format(
                barcode,
                FormatConfirmation(
                    expected_barcode=barcode,
                    safety_token=SafetyToken.generate("format", barcode),
                ),
            )

    def unload_everything(self) -> None:
        """Return every loaded cartridge to its home slot (emulator state is shared)."""
        snapshot = InventoryService(self.library).snapshot()
        empty_slots = [slot.slot_id for slot in snapshot.slots if not slot.occupied]
        for drive in snapshot.drives:
            if drive.barcode is not None and empty_slots:
                self.library.unload(drive.drive_id, empty_slots.pop(0))


def _sim_pair() -> Iterator[BackendPair]:
    library = MockLibraryBackend(num_slots=20, num_drives=3)
    for slot_id, barcode in enumerate(SIM_BARCODES, start=1):
        library.add_cartridge(slot_id, barcode)
    ltfs = MockLTFSBackend(library, capacity_bytes=CAPACITY)
    yield BackendPair("sim+sim", library, ltfs, 3, list(SIM_BARCODES))


def _emulator_pair(tmp_path: Path) -> Iterator[BackendPair]:
    os.environ.setdefault("OPENBLADE_BACKEND", "mock")
    from fastapi.testclient import TestClient

    from openblade.api.main import app
    from openblade.hardware.scalar_http.library_backend import ScalarHttpLibraryBackend
    from openblade.hardware.scalar_http.session import ScalarHttpSession

    with TestClient(app) as client:
        library = ScalarHttpLibraryBackend(
            ScalarHttpSession(client, username="admin", password="password")
        )
        snapshot = InventoryService(library).snapshot()
        barcodes = [slot.barcode.value for slot in snapshot.slots if slot.barcode][:3]
        # The simulator LTFS is typed against MockLibraryBackend but only consumes
        # the LibraryBackend surface; the cast is the pairing under test.
        ltfs = MockLTFSBackend(cast(MockLibraryBackend, library), capacity_bytes=CAPACITY)
        pair = BackendPair("emulator+sim-ltfs", library, ltfs, len(snapshot.drives), barcodes)
        pair.unload_everything()
        try:
            yield pair
        finally:
            pair.unload_everything()


def _real_pair() -> Iterator[BackendPair]:
    from openblade.bootstrap import get_library, get_ltfs

    library = get_library()
    snapshot = InventoryService(library).snapshot()
    barcodes = [slot.barcode.value for slot in snapshot.slots if slot.barcode][:1]
    yield BackendPair("real+real", library, get_ltfs(), len(snapshot.drives), barcodes)


_REAL_ENABLED = os.environ.get("OPENBLADE_REAL_HARDWARE_ENABLED", "false").lower() == "true"

PAIRINGS = [
    pytest.param("sim+sim", id="sim+sim"),
    pytest.param("emulator+sim-ltfs", id="emulator+sim-ltfs"),
    pytest.param(
        "real+real",
        id="real+real",
        marks=[
            pytest.mark.real_hardware,
            pytest.mark.skipif(
                not _REAL_ENABLED, reason="OPENBLADE_REAL_HARDWARE_ENABLED is not true"
            ),
        ],
    ),
]


@pytest.fixture(params=PAIRINGS)
def backend_pair(request: pytest.FixtureRequest, tmp_path: Path) -> Generator[BackendPair]:
    name = request.param
    if name == "sim+sim":
        yield from _sim_pair()
    elif name == "emulator+sim-ltfs":
        yield from _emulator_pair(tmp_path)
    else:
        yield from _real_pair()


@pytest.fixture
def catalog(backend_pair: BackendPair, tmp_path: Path) -> CatalogRepository:
    # Depends on backend_pair so the emulator app's own init_db runs first.
    init_db(f"sqlite:///{tmp_path / 'contract-catalog.db'}")
    return CatalogRepository(get_session())


def call_with_scheduler(
    fn: Callable[..., Any], *args: Any, scheduler: DriveScheduler, **kwargs: Any
) -> Any:
    """Call a job runner whether or not its signature takes ``scheduler``.

    ``run_archive_job`` / ``run_restore_job`` are gaining a ``scheduler=`` kwarg in a
    parallel workstream; this keeps the contract test valid across both shapes.
    """
    if "scheduler" in inspect.signature(fn).parameters:
        kwargs["scheduler"] = scheduler
    return fn(*args, **kwargs)
