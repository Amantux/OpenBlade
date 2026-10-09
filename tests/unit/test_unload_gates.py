"""Never unload while LTFS is mounted — both halves of the gate.

In-process: the real-hardware library adapter refuses while its mount record is
not UNMOUNTED. Cross-process: the tape orchestrator refuses while another job
holds a live catalog lease on the drive (mount state is per process, the lease
is the only shared evidence).
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from openblade.catalog.db import get_session, init_db
from openblade.catalog.repository import CatalogRepository
from openblade.domain.errors import TapeMountedError
from openblade.domain.models import MountState, OperationResult
from openblade.hardware.library import RealLibraryBackend
from openblade.nas.tape_orchestrator import TapeOperationOrchestrator
from openblade.nas.types import TapeOpRequest, TapeOpStatus, TapeOpType
from openblade.simulator.library import MockLibraryBackend
from openblade.simulator.ltfs_volume import MockLTFSBackend


class _RecordingChanger:
    def __init__(self) -> None:
        self.unloads: list[tuple[int, int]] = []

    def unload(self, drive_id: int, target_slot: int) -> OperationResult:
        self.unloads.append((drive_id, target_slot))
        return OperationResult(success=True, message="unloaded")


def _hardware_library() -> tuple[RealLibraryBackend, _RecordingChanger]:
    # Bypass the guarded constructor: this test is about the unload gate only.
    library = RealLibraryBackend.__new__(RealLibraryBackend)
    changer = _RecordingChanger()
    object.__setattr__(library, "changer", changer)
    object.__setattr__(library, "_mount_states", {})
    return library, changer


def test_hardware_library_refuses_unload_while_mounted() -> None:
    library, changer = _hardware_library()
    library._mount_states[0] = MountState.MOUNTED_RW
    with pytest.raises(TapeMountedError):
        library.unload(0, 1)
    assert changer.unloads == []


def test_hardware_library_refuses_unload_while_dirty() -> None:
    library, changer = _hardware_library()
    library._mount_states[0] = MountState.DIRTY
    with pytest.raises(TapeMountedError):
        library.unload(0, 1)
    assert changer.unloads == []


def test_hardware_library_unloads_when_unmounted() -> None:
    library, changer = _hardware_library()
    assert library.unload(0, 1).success
    assert changer.unloads == [(0, 1)]


def _orchestrator() -> tuple[CatalogRepository, MockLibraryBackend, TapeOperationOrchestrator]:
    init_db("sqlite:///:memory:")
    repo = CatalogRepository(get_session())
    library = MockLibraryBackend(num_slots=4, num_drives=1)
    library.seed_slots(["OBX001L9"])
    ltfs = MockLTFSBackend(library)
    library.load(1, 0)
    return repo, library, TapeOperationOrchestrator(repo, library, ltfs)


def test_orchestrator_refuses_unload_while_another_job_holds_a_live_lease() -> None:
    repo, library, orchestrator = _orchestrator()
    owner = repo.create_job("archive", {})
    leases = repo.acquire_drive_leases(
        job_id=owner.id, barcodes=["OBX001L9"], num_drives=1, ttl=timedelta(minutes=15)
    )
    assert leases is not None
    other = repo.create_job("restore", {})
    record = orchestrator.execute(
        TapeOpRequest(
            op_type=TapeOpType.UNLOAD,
            barcode="OBX001L9",
            drive_id=0,
            slot_id=1,
            requested_by="test",
            job_id=other.id,
        )
    )
    assert record.status is TapeOpStatus.FAILED
    assert record.error == "Tape unload operation failed"  # curated; cause is logged
    assert library.find_drive_by_barcode("OBX001L9") == 0  # still in the drive


def test_orchestrator_allows_unload_for_the_lease_owner_and_after_release() -> None:
    repo, library, orchestrator = _orchestrator()
    owner = repo.create_job("archive", {})
    leases = repo.acquire_drive_leases(
        job_id=owner.id, barcodes=["OBX001L9"], num_drives=1, ttl=timedelta(minutes=15)
    )
    assert leases is not None
    request = TapeOpRequest(
        op_type=TapeOpType.UNLOAD,
        barcode="OBX001L9",
        drive_id=0,
        slot_id=1,
        requested_by="test",
        job_id=owner.id,
    )
    orchestrator.execute(request)
    assert library.find_drive_by_barcode("OBX001L9") is None
    library.load(1, 0)
    repo.release_leases([lease.id for lease in leases])
    other = repo.create_job("restore", {})
    orchestrator.execute(request.model_copy(update={"job_id": other.id}))
    assert library.find_drive_by_barcode("OBX001L9") is None
