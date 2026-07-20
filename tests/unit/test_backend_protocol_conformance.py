"""Backend-protocol conformance (roadmap #4).

Orchestration (archive/restore/verify/shard jobs) is typed against the
LibraryBackend/LTFSBackend Protocols, not the Mock* concretes. Pin that both the
simulator and the real backends actually provide the protocol surface, so a
backend can be swapped without the orchestration knowing.
"""

from __future__ import annotations

from openblade.domain.backends import LibraryBackend, LTFSBackend
from openblade.simulator.library import MockLibraryBackend
from openblade.simulator.ltfs_volume import MockLTFSBackend

# Methods newly promoted into the shared protocol this change — verified present on
# the simulator AND the real backends (mtx/SCSI + Scalar HTTP + real LTFS).
_ADDED_LIBRARY = ("find_slot_by_barcode", "find_drive_by_barcode", "get_all_barcodes", "get_cartridge_state")
_ADDED_LTFS = ("ensure_tape", "remaining_capacity", "read_bytes")


def test_mock_backends_satisfy_protocols() -> None:
    library = MockLibraryBackend(num_slots=2, num_drives=1)
    ltfs = MockLTFSBackend(library, capacity_bytes=1024)
    assert isinstance(library, LibraryBackend)
    assert isinstance(ltfs, LTFSBackend)


def test_real_library_backends_provide_added_methods() -> None:
    # Structural (no instantiation -> no hardware side effects).
    from openblade.hardware.library import RealLibraryBackend
    from openblade.hardware.scalar_http.library_backend import ScalarHttpLibraryBackend

    for backend in (RealLibraryBackend, ScalarHttpLibraryBackend):
        for method in _ADDED_LIBRARY:
            assert hasattr(backend, method), f"{backend.__name__} missing {method}"


def test_real_ltfs_backend_provides_added_methods() -> None:
    from openblade.hardware.ltfs import RealLTFSBackend

    for method in _ADDED_LTFS:
        assert hasattr(RealLTFSBackend, method), f"RealLTFSBackend missing {method}"
