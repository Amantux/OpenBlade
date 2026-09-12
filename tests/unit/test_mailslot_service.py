"""Import/export (mailslot) service.

The guard these tests exist for: ``mailslot export`` is how data walks out of
the library. Once a cartridge is in the I/E station and the catalog knows it,
every restore path refuses it -- so an unguarded export is a silent, remote
data-availability loss. ``test_export_refuses_a_cartridge_carrying_data`` is
the mutation-check anchor (drop the ``carries_data`` check in
``TapeOperationOrchestrator._guard_export`` and it must fail).
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from openblade.catalog.db import get_session, init_db
from openblade.catalog.repository import CatalogRepository
from openblade.domain.errors import (
    CartridgeNotFoundError,
    ExportRefusedError,
    ImportExportSlotError,
    MailslotUnsupportedError,
)
from openblade.domain.models import MountMode
from openblade.domain.policies import FormatConfirmation, SafetyToken
from openblade.nas.mailslot import MailslotService
from openblade.simulator.library import MockLibraryBackend
from openblade.simulator.ltfs_volume import MockLTFSBackend


def make_repo() -> CatalogRepository:
    init_db("sqlite:///:memory:")
    return CatalogRepository(get_session())


def make_service(
    *, ie_slots: int = 4, slots: int = 6
) -> tuple[CatalogRepository, MockLibraryBackend, MailslotService]:
    repo = make_repo()
    library = MockLibraryBackend(
        num_slots=slots, num_drives=1, num_import_export_slots=ie_slots
    )
    library.seed_slots(["OB0001L8", "OB0002L8"])
    ltfs = MockLTFSBackend(library)
    return repo, library, MailslotService(repo, library, ltfs)


def archive_one_file(
    repo: CatalogRepository,
    library: MockLibraryBackend,
    barcode: str,
    catalog_path: str,
    volume_group: str = "photos",
) -> None:
    """Put one archived file instance on ``barcode``, catalog-side only."""
    group = repo.create_volume_group(volume_group)
    repo.add_cartridge(barcode, group.id)
    record = repo.create_file_record(catalog_path, 1024, "deadbeef", group.id)
    instance = repo.create_file_instance(record.id, barcode, catalog_path)
    repo.mark_instance_archived(instance.id)


class TestList:
    def test_lists_every_element_and_flags_occupancy(self) -> None:
        _, library, service = make_service(ie_slots=3)
        library.export_cartridge_to_ie(1, 7)

        listing = service.list_slots()

        assert [slot.slot_id for slot in listing.slots] == [7, 8, 9]
        assert listing.slot_count == 3
        assert listing.occupied_barcodes() == ["OB0001L8"]

    def test_backend_without_a_mailslot_refuses_by_type(self) -> None:
        repo = make_repo()
        library = MockLibraryBackend(num_slots=4, num_drives=1)  # no I/E elements

        class NoMailslot:
            """Stands in for a backend that does not implement the protocol."""

            def inventory(self) -> None: ...

        service = MailslotService(repo, NoMailslot(), MockLTFSBackend(library))
        with pytest.raises(MailslotUnsupportedError):
            service.list_slots()


class TestImport:
    def test_picks_and_names_the_first_empty_storage_slot(self) -> None:
        _, library, service = make_service()
        library.export_cartridge_to_ie(2, 7)  # park OB0002L8 in the mailslot

        result = service.import_cartridge(7)

        # Slots 1 is occupied by OB0001L8, 2 was just vacated -> 2 is first free.
        assert result.destination_slot == 2
        assert result.slot_was_chosen is True
        assert result.barcode == "OB0002L8"
        assert library.find_slot_by_barcode("OB0002L8") == 2

    def test_honours_an_explicit_destination_slot(self) -> None:
        _, library, service = make_service()
        library.export_cartridge_to_ie(2, 7)

        result = service.import_cartridge(7, to_slot=5)

        assert result.destination_slot == 5
        assert result.slot_was_chosen is False
        assert library.find_slot_by_barcode("OB0002L8") == 5

    def test_clears_the_exported_flag_so_restores_work_again(self) -> None:
        repo, library, service = make_service()
        archive_one_file(repo, library, "OB0001L8", "/photos/a.txt")
        service.export_cartridge("OB0001L8", force=True)
        assert repo.get_cartridge("OB0001L8").state == "exported"

        service.import_cartridge(7)

        assert repo.get_cartridge("OB0001L8").state == "in_slot"

    def test_empty_element_refuses_with_a_typed_error(self) -> None:
        _, _, service = make_service()
        with pytest.raises(ImportExportSlotError, match="is empty"):
            service.import_cartridge(7)

    def test_unknown_element_names_the_ones_that_exist(self) -> None:
        _, _, service = make_service(ie_slots=2)
        with pytest.raises(ImportExportSlotError, match=r"\[7, 8\]"):
            service.import_cartridge(99)

    def test_a_full_library_refuses_rather_than_choosing_an_ie_slot(self) -> None:
        """The i3's I/E elements number past the storage slots.

        If "first empty slot" ever fell through to the mailslot, an import would
        shuffle media between I/E elements and report success.
        """
        repo = make_repo()
        library = MockLibraryBackend(num_slots=2, num_drives=1, num_import_export_slots=2)
        library.seed_slots(["OB0001L8", "OB0002L8"])
        library.export_cartridge_to_ie(1, 3)
        library.add_cartridge(1, "OB0003L8")  # refill the vacated storage slot
        service = MailslotService(repo, library, MockLTFSBackend(library))

        with pytest.raises(ImportExportSlotError, match="No empty storage slot"):
            service.import_cartridge(3)


class TestExport:
    def test_exports_a_cartridge_with_no_archived_data(self) -> None:
        repo, library, service = make_service()

        result = service.export_cartridge("OB0001L8")

        assert result.destination_slot == 7
        assert result.slot_was_chosen is True
        assert library.find_slot_by_barcode("OB0001L8") is None
        assert [slot.barcode for slot in service.list_slots().slots] == [
            "OB0001L8",
            None,
            None,
            None,
        ]

    def test_export_refuses_a_cartridge_carrying_data(self) -> None:
        """MUTATION ANCHOR. Remove the guard and this test must fail.

        Verified by deleting the ``if assessment.carries_data: raise`` block in
        ``TapeOperationOrchestrator._guard_export``: this test then fails while
        ``test_export_with_force_moves_the_cartridge_anyway`` still passes.
        """
        repo, library, service = make_service()
        archive_one_file(repo, library, "OB0001L8", "/photos/holiday.jpg")

        with pytest.raises(ExportRefusedError) as caught:
            service.export_cartridge("OB0001L8")

        message = str(caught.value)
        # It must NAME what is on it -- a bare "refused" is not actionable.
        assert "OB0001L8" in message
        assert "/photos/holiday.jpg" in message
        assert "photos" in message
        # And nothing moved.
        assert library.find_slot_by_barcode("OB0001L8") == 1
        assert repo.get_cartridge("OB0001L8").state != "exported"

    def test_a_blank_tape_in_an_active_group_exports_without_a_fight(self) -> None:
        """Rotating a scratch tape out of a working group is routine.

        This used to refuse, on the theory that a block_stripe file spans the
        group. That reasoning is wrong: a shard IS a file instance, so a
        cartridge holding part of a striped file has instances of its own and
        the real check catches it. A cartridge with zero instances carries
        nothing -- and refusing it with the sentence "still carries archived
        data" was both false and a good way to teach operators that `--force`
        is the normal way to export. (Adversarial review finding.)
        """
        repo, library, service = make_service()
        group = repo.create_volume_group("shards")
        repo.add_cartridge("OB0001L8", group.id)
        archive_one_file(repo, library, "OB0002L8", "/shards/big.bin", "shards")
        # OB0001L8 itself carries nothing.

        result = service.export_cartridge("OB0001L8")

        assert library.find_slot_by_barcode("OB0001L8") is None
        # ...but the sibling situation is still REPORTED, as context.
        assert result.assessment is not None
        assert result.assessment.volume_group_barcodes_with_data == ["OB0002L8"]
        assert result.assessment.carries_data is False

    def test_export_refuses_while_a_write_to_the_cartridge_is_in_flight(self) -> None:
        """A `pending` instance is an archive job mid-write on that tape."""
        repo, library, service = make_service()
        group = repo.create_volume_group("photos")
        repo.add_cartridge("OB0001L8", group.id)
        record = repo.create_file_record("/photos/inflight.raw", 99, "abc", group.id)
        repo.create_file_instance(record.id, "OB0001L8", "/photos/inflight.raw")

        with pytest.raises(ExportRefusedError, match="in flight"):
            service.export_cartridge("OB0001L8")

        assert library.find_slot_by_barcode("OB0001L8") == 1

    def test_export_with_force_moves_the_cartridge_anyway(self) -> None:
        repo, library, service = make_service()
        archive_one_file(repo, library, "OB0001L8", "/photos/holiday.jpg")

        result = service.export_cartridge("OB0001L8", force=True)

        assert result.destination_slot == 7
        assert library.find_slot_by_barcode("OB0001L8") is None
        # Reported honestly: the caller is told what just went out of the door.
        assert result.assessment is not None
        assert result.assessment.archived_files_on_cartridge == 1

    def test_export_marks_the_catalog_cartridge_offline(self) -> None:
        repo, library, service = make_service()
        archive_one_file(repo, library, "OB0001L8", "/photos/holiday.jpg")

        service.export_cartridge("OB0001L8", force=True)

        # This is the flag jobs/restore.py and jobs/sharded_restore.py read.
        assert repo.get_cartridge("OB0001L8").state == "exported"

    def test_export_marks_a_cartridge_the_catalog_has_never_seen(self) -> None:
        """file_instances key on the barcode, not on a cartridges row.

        A tape can therefore carry archived data with no cartridge row at all,
        and ``set_cartridge_state`` no-ops on an unknown barcode -- which would
        export the media while the catalog still called it restorable.
        """
        repo, library, service = make_service()
        group = repo.create_volume_group("photos")
        record = repo.create_file_record("/photos/x.jpg", 10, "abc", group.id)
        instance = repo.create_file_instance(record.id, "OB0001L8", "/photos/x.jpg")
        repo.mark_instance_archived(instance.id)
        assert repo.get_cartridge("OB0001L8") is None

        service.export_cartridge("OB0001L8", force=True)

        assert repo.get_cartridge("OB0001L8").state == "exported"

    def test_exported_data_actually_becomes_unrestorable(self) -> None:
        """The consequence the guard exists to prevent, demonstrated end to end."""
        from openblade.domain.errors import CartridgeOfflineError
        from openblade.jobs.restore import RestoreRequest, run_restore_job

        repo, library, service = make_service()
        ltfs = MockLTFSBackend(library)
        service = MailslotService(repo, library, ltfs)
        archive_one_file(repo, library, "OB0001L8", "/photos/holiday.jpg")

        service.export_cartridge("OB0001L8", force=True)

        job = repo.create_job("restore", {})
        with pytest.raises(CartridgeOfflineError):
            run_restore_job(
                RestoreRequest(
                    catalog_path="/photos/holiday.jpg", dest_path=Path("/tmp/nope")
                ),
                library,
                ltfs,
                repo,
                job.id,
            )

    def test_export_refuses_when_the_cartridge_is_not_in_a_storage_slot(self) -> None:
        _, library, service = make_service()
        library.load(1, 0)  # OB0001L8 is in a drive, not a slot

        with pytest.raises(CartridgeNotFoundError, match="not in a storage slot"):
            service.export_cartridge("OB0001L8")

    def test_export_refuses_when_the_mailslot_is_full(self) -> None:
        repo = make_repo()
        library = MockLibraryBackend(num_slots=4, num_drives=1, num_import_export_slots=1)
        library.seed_slots(["OB0001L8", "OB0002L8"])
        library.export_cartridge_to_ie(2, 5)
        service = MailslotService(repo, library, MockLTFSBackend(library))

        with pytest.raises(ImportExportSlotError, match="occupied"):
            service.export_cartridge("OB0001L8")

    def test_preview_moves_nothing(self) -> None:
        repo, library, service = make_service()
        archive_one_file(repo, library, "OB0001L8", "/photos/holiday.jpg")

        assessment = service.preview_export("OB0001L8")

        assert assessment.carries_data is True
        assert assessment.archived_files_on_cartridge == 1
        assert library.find_slot_by_barcode("OB0001L8") == 1


class TestAuditTrail:
    def test_every_move_is_recorded_as_a_tape_op(self) -> None:
        repo, library, service = make_service()

        service.export_cartridge("OB0001L8")
        service.import_cartridge(7)

        ops = repo.list_tape_ops(limit=10)
        assert [op["op_type"] for op in ops][:2] == ["import", "export"] or [
            op["op_type"] for op in ops
        ][:2] == ["export", "import"]
        assert all(op["status"] == "completed" for op in ops)
        assert all(op["requested_by"] == "mailslot" for op in ops)

    def test_a_refused_export_still_leaves_an_audit_row(self) -> None:
        repo, library, service = make_service()
        archive_one_file(repo, library, "OB0001L8", "/photos/holiday.jpg")

        with pytest.raises(ExportRefusedError):
            service.export_cartridge("OB0001L8")

        ops = repo.list_tape_ops(limit=10)
        assert len(ops) == 1
        assert ops[0]["op_type"] == "export"
        assert ops[0]["status"] == "failed"
        # The curated error is the operator-written refusal, not a constant.
        assert "/photos/holiday.jpg" in ops[0]["error"]


class TestOrchestratorGuardIsUnbypassable:
    """The orchestrator is the choke point, so attack IT, not the service.

    `POST /tape-ops/execute` binds TapeOpRequest straight from a request body,
    so every field here is attacker-supplied. The service resolving things
    correctly proves nothing about this path.
    """

    @staticmethod
    def _loaded_rig():
        repo = make_repo()
        library = MockLibraryBackend(
            num_slots=4, num_drives=1, num_import_export_slots=2
        )
        library.seed_slots(["OB0001L8", "OB0002L8"])
        ltfs = MockLTFSBackend(library)
        group = repo.create_volume_group("photos")
        repo.add_cartridge("OB0001L8", group.id)
        record = repo.create_file_record("/photos/holiday.jpg", 10, "abc", group.id)
        instance = repo.create_file_instance(record.id, "OB0001L8", "/photos/holiday.jpg")
        repo.mark_instance_archived(instance.id)
        return repo, library, ltfs

    def test_naming_an_empty_tape_cannot_eject_a_loaded_one(self) -> None:
        """MUTATION ANCHOR. Defect 3.9 through the new EXPORT op.

        The guard assesses `request.barcode`; the move used to use
        `request.slot_id`. Nothing tied them together, so naming a blank
        cartridge and pointing `slot_id` at a data-carrying slot ejected the
        wrong tape past a guard that had just reported "carries no data" --
        and the catalog still called it online. Found by adversarial review and
        reproduced end to end before the fix.

        Restore `source_slot = request.slot_id` in
        `TapeOperationOrchestrator._export` and this test fails.
        """
        from openblade.nas.tape_orchestrator import execute_tape_request
        from openblade.nas.types import TapeOpRequest, TapeOpStatus, TapeOpType

        repo, library, ltfs = self._loaded_rig()

        record = execute_tape_request(
            repo,
            library,
            ltfs,
            TapeOpRequest(
                op_type=TapeOpType.EXPORT,
                barcode="OB0002L8",  # carries nothing
                slot_id=1,           # ...but slot 1 holds OB0001L8, which does
                extras={"ie_slot": 5},
            ),
        )

        assert record.status is TapeOpStatus.FAILED
        assert "does not name" in record.error
        assert library.find_slot_by_barcode("OB0001L8") == 1
        assert all(not slot.occupied for slot in library.import_export_slots())
        assert repo.get_cartridge("OB0001L8").state != "exported"

    def test_export_to_a_non_ie_element_is_refused(self) -> None:
        """`extras["ie_slot"]` reaches `mtx transfer` verbatim; mtx validates nothing."""
        from openblade.nas.tape_orchestrator import execute_tape_request
        from openblade.nas.types import TapeOpRequest, TapeOpStatus, TapeOpType

        repo, library, ltfs = self._loaded_rig()

        record = execute_tape_request(
            repo,
            library,
            ltfs,
            TapeOpRequest(
                op_type=TapeOpType.EXPORT,
                barcode="OB0002L8",
                extras={"ie_slot": 3, "force": True},  # 3 is a STORAGE slot
            ),
        )
        assert record.status is TapeOpStatus.FAILED
        assert "not an import/export element" in record.error
        assert library.find_slot_by_barcode("OB0002L8") == 2

    def test_import_from_a_non_ie_element_is_refused(self) -> None:
        from openblade.nas.tape_orchestrator import execute_tape_request
        from openblade.nas.types import TapeOpRequest, TapeOpStatus, TapeOpType

        repo, library, ltfs = self._loaded_rig()

        record = execute_tape_request(
            repo,
            library,
            ltfs,
            TapeOpRequest(
                op_type=TapeOpType.IMPORT,
                barcode="OB0001L8",
                slot_id=3,
                extras={"ie_slot": 1},  # 1 is a STORAGE slot holding OB0001L8
            ),
        )
        assert record.status is TapeOpStatus.FAILED
        assert library.find_slot_by_barcode("OB0001L8") == 1

    def test_import_refuses_when_the_element_holds_a_different_cartridge(self) -> None:
        from openblade.nas.tape_orchestrator import execute_tape_request
        from openblade.nas.types import TapeOpRequest, TapeOpStatus, TapeOpType

        repo, library, ltfs = self._loaded_rig()
        library.export_cartridge_to_ie(2, 5)  # OB0002L8 into I/E 5

        record = execute_tape_request(
            repo,
            library,
            ltfs,
            TapeOpRequest(
                op_type=TapeOpType.IMPORT,
                barcode="OB0001L8",  # not what is in element 5
                slot_id=3,
                extras={"ie_slot": 5},
            ),
        )
        assert record.status is TapeOpStatus.FAILED
        assert "does not name" in record.error
        assert library.find_slot_by_barcode("OB0001L8") == 1
        # OB0002L8 is still parked in the mailslot, untouched.
        assert [
            str(slot.barcode) for slot in library.import_export_slots() if slot.occupied
        ] == ["OB0002L8"]

    def test_import_refuses_an_occupied_destination_slot(self) -> None:
        from openblade.nas.tape_orchestrator import execute_tape_request
        from openblade.nas.types import TapeOpRequest, TapeOpStatus, TapeOpType

        repo, library, ltfs = self._loaded_rig()
        library.export_cartridge_to_ie(2, 5)

        record = execute_tape_request(
            repo,
            library,
            ltfs,
            TapeOpRequest(
                op_type=TapeOpType.IMPORT,
                barcode="OB0002L8",
                slot_id=1,  # holds OB0001L8
                extras={"ie_slot": 5},
            ),
        )
        assert record.status is TapeOpStatus.FAILED
        assert "already holds" in record.error
        assert library.find_slot_by_barcode("OB0001L8") == 1

    def test_the_catalog_flag_is_written_by_the_orchestrator_not_the_service(
        self,
    ) -> None:
        """Otherwise /tape-ops/execute moves media and leaves the catalog stale."""
        from openblade.nas.tape_orchestrator import execute_tape_request
        from openblade.nas.types import TapeOpRequest, TapeOpStatus, TapeOpType

        repo, library, ltfs = self._loaded_rig()

        record = execute_tape_request(
            repo,
            library,
            ltfs,
            TapeOpRequest(
                op_type=TapeOpType.EXPORT,
                barcode="OB0001L8",
                extras={"ie_slot": 5, "force": True},
            ),
        )
        assert record.status is TapeOpStatus.COMPLETED
        assert repo.get_cartridge("OB0001L8").state == "exported"

        execute_tape_request(
            repo,
            library,
            ltfs,
            TapeOpRequest(
                op_type=TapeOpType.IMPORT,
                barcode="OB0001L8",
                slot_id=1,
                extras={"ie_slot": 5},
            ),
        )
        assert repo.get_cartridge("OB0001L8").state == "in_slot"

    def test_export_through_a_catalogless_repo_fails_closed(self) -> None:
        """A transient repo cannot say what is on the tape, so it must refuse."""
        from openblade.nas.tape_orchestrator import execute_tape_request
        from openblade.nas.types import TapeOpRequest, TapeOpType

        library = MockLibraryBackend(
            num_slots=4, num_drives=1, num_import_export_slots=2
        )
        library.seed_slots(["OB0001L8"])
        ltfs = MockLTFSBackend(library)

        with pytest.raises(ExportRefusedError, match="without a"):
            execute_tape_request(
                None,
                library,
                ltfs,
                TapeOpRequest(
                    op_type=TapeOpType.EXPORT,
                    barcode="OB0001L8",
                    slot_id=1,
                    extras={"ie_slot": 5},
                ),
            )
        assert library.find_slot_by_barcode("OB0001L8") == 1

    def test_move_still_refuses_an_import_export_destination(self) -> None:
        """Defect 3.9 stays closed: the new op types are the only way in."""
        from openblade.nas.tape_orchestrator import execute_tape_request
        from openblade.nas.types import TapeOpRequest, TapeOpType

        library = MockLibraryBackend(
            num_slots=4, num_drives=1, num_import_export_slots=2
        )
        library.seed_slots(["OB0001L8"])
        ltfs = MockLTFSBackend(library)

        with pytest.raises(ValueError, match="import/export element is not supported"):
            execute_tape_request(
                None,
                library,
                ltfs,
                TapeOpRequest(
                    op_type=TapeOpType.MOVE,
                    barcode="OB0001L8",
                    slot_id=1,
                    extras={"source_slot_id": 1, "dest_slot_id": 5},
                ),
            )
        assert library.find_slot_by_barcode("OB0001L8") == 1

    def test_a_full_library_refuses_to_unload_rather_than_eject_to_the_mailslot(
        self,
    ) -> None:
        """The defect-3.9 shape, from the other direction.

        `_find_empty_slot` picks "the first empty slot" for an unload. Now that
        I/E elements exist and are numbered past the storage slots, a library
        with no free storage slot must REFUSE, not fall through to the mailslot
        and eject a loaded cartridge to the front panel.
        """
        from openblade.nas.tape_orchestrator import (
            TapeOperationOrchestrator,
            _TransientTapeOpRepository,
        )

        library = MockLibraryBackend(
            num_slots=2, num_drives=1, num_import_export_slots=2
        )
        library.seed_slots(["OB0001L8", "OB0002L8"])
        library.load(1, 0)                    # slot 1 is now free...
        library.add_cartridge(1, "OB0003L8")  # ...and immediately refilled
        orchestrator = TapeOperationOrchestrator(
            _TransientTapeOpRepository(), library, MockLTFSBackend(library)
        )

        with pytest.raises(ValueError, match="No empty slot"):
            orchestrator._find_empty_slot()

        assert all(not slot.occupied for slot in library.import_export_slots())


class TestSimulatorState:
    def test_import_export_elements_are_not_storage_slots(self) -> None:
        library = MockLibraryBackend(
            num_slots=3, num_drives=1, num_import_export_slots=2
        )
        assert [slot.slot_id for slot in library.inventory().slots] == [1, 2, 3]
        assert [slot.slot_id for slot in library.import_export_slots()] == [4, 5]

    def test_a_tape_in_the_mailslot_is_not_found_by_slot_lookup(self) -> None:
        library = MockLibraryBackend(
            num_slots=3, num_drives=1, num_import_export_slots=2
        )
        library.seed_slots(["OB0001L8"])
        library.export_cartridge_to_ie(1, 4)
        # Nothing that unloads or loads may pick it up from the mailslot.
        assert library.find_slot_by_barcode("OB0001L8") is None

    def test_json_round_trip_preserves_the_mailslot(self) -> None:
        library = MockLibraryBackend(
            num_slots=3, num_drives=1, num_import_export_slots=2
        )
        library.seed_slots(["OB0001L8"])
        library.export_cartridge_to_ie(1, 4)

        restored = MockLibraryBackend.from_json(library.to_json())

        assert [
            (slot.slot_id, None if slot.barcode is None else str(slot.barcode))
            for slot in restored.import_export_slots()
        ] == [(4, "OB0001L8"), (5, None)]

    def test_a_snapshot_without_a_mailslot_still_loads(self) -> None:
        payload = MockLibraryBackend(num_slots=2, num_drives=1).to_json()
        payload.pop("import_export_slots")

        restored = MockLibraryBackend.from_json(payload)

        assert restored.import_export_slots() == []

    def test_seeded_elements_keep_their_own_numbers(self) -> None:
        """Some libraries report I/E at 768+; nothing may renumber them."""
        library = MockLibraryBackend(num_slots=2, num_drives=1)
        library.seed_import_export_slots({768: None, 769: "HIA769L8"})

        assert [slot.slot_id for slot in library.import_export_slots()] == [768, 769]


def test_ltfs_still_sees_a_tape_that_came_in_through_the_mailslot(
    tmp_path: Path,
) -> None:
    """An imported cartridge is usable, not merely present."""
    repo, library, service = make_service()
    ltfs = MockLTFSBackend(library)
    service = MailslotService(repo, library, ltfs)
    service.export_cartridge("OB0001L8")
    service.import_cartridge(7, to_slot=3)

    library.load(3, 0)
    ltfs.format(
        "OB0001L8",
        FormatConfirmation(
            expected_barcode="OB0001L8",
            safety_token=SafetyToken.generate("format", "OB0001L8"),
        ),
    )
    handle = ltfs.mount("OB0001L8", MountMode.READ_WRITE)
    source = tmp_path / "x.txt"
    source.write_text("hello")
    ltfs.write_file(handle, source, PurePosixPath("/x.txt"))
    ltfs.unmount(handle)

    assert ltfs.read_bytes("OB0001L8", "/x.txt") == b"hello"
