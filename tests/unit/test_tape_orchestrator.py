from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, reset_context
from openblade.catalog.db import get_session, init_db
from openblade.catalog.repository import CatalogRepository
from openblade.config import OpenBladeConfig
from openblade.domain.policies import FormatConfirmation, SafetyToken
from openblade.nas.tape_orchestrator import OperationNotConfirmedError, TapeOperationOrchestrator
from openblade.nas.types import TapeOpRequest, TapeOpStatus, TapeOpType
from openblade.simulator.library import MockLibraryBackend
from openblade.simulator.ltfs_volume import MockLTFSBackend


class FailingLibrary(MockLibraryBackend):
    def load(self, source_slot: int, drive_id: int):
        raise RuntimeError("SECRET raw load failure")


class ObservedLibrary(MockLibraryBackend):
    def __init__(self, repo: CatalogRepository) -> None:
        super().__init__(num_slots=4, num_drives=1)
        self.repo = repo
        self.observed_created = False

    def load(self, source_slot: int, drive_id: int):
        ops = self.repo.list_tape_ops(limit=1)
        self.observed_created = bool(ops) and ops[0]["status"] == TapeOpStatus.RUNNING.value
        return super().load(source_slot, drive_id)


def format_extras(barcode: str) -> dict:
    """Extras for a legitimately confirmed format.

    `confirmed_format: True` alone is NOT sufficient any more, and must not be --
    `extras` is bound straight from the POST /tape-ops/execute body, so a bare
    boolean would be the whole gate on a destructive operation.
    """
    return {
        "confirmed_format": True,
        "format_confirmation": FormatConfirmation(
            expected_barcode=barcode,
            safety_token=SafetyToken.generate("format", barcode),
        ),
    }


def make_repo() -> CatalogRepository:
    init_db("sqlite:///:memory:")
    return CatalogRepository(get_session())


def make_orchestrator(
    repo: CatalogRepository | None = None,
    library: MockLibraryBackend | None = None,
    ltfs: MockLTFSBackend | None = None,
) -> tuple[CatalogRepository, MockLibraryBackend, MockLTFSBackend, TapeOperationOrchestrator]:
    repo = repo or make_repo()
    library = library or MockLibraryBackend(num_slots=4, num_drives=1)
    ltfs = ltfs or MockLTFSBackend(library)
    return repo, library, ltfs, TapeOperationOrchestrator(repo, library, ltfs)


def seed_read_data(
    library: MockLibraryBackend, ltfs: MockLTFSBackend, barcode: str, path: str, content: bytes
) -> None:
    library.seed_slots([barcode])
    ltfs.write_bytes(barcode, path, content)


def prepare_write_target(
    orchestrator: TapeOperationOrchestrator, library: MockLibraryBackend, barcode: str
) -> None:
    library.seed_slots([barcode])
    orchestrator.execute(
        TapeOpRequest(
            op_type=TapeOpType.FORMAT,
            barcode=barcode,
            requested_by="tester",
            extras=format_extras(barcode),
        )
    )


def make_client(tmp_path, db_name: str) -> TestClient:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / db_name}"))
    reset_context(context)
    return TestClient(app)


def test_load_op_persists_log() -> None:
    repo, library, _, orchestrator = make_orchestrator()
    library.seed_slots(["LD0001L8"])

    record = orchestrator.execute(
        TapeOpRequest(
            op_type=TapeOpType.LOAD,
            barcode="LD0001L8",
            drive_id=0,
            slot_id=1,
            requested_by="tester",
        )
    )

    stored = repo.get_tape_op(record.op_id)
    assert stored is not None
    assert stored["status"] == TapeOpStatus.COMPLETED.value
    assert stored["barcode"] == "LD0001L8"


def test_write_op_persists_log() -> None:
    repo, library, _, orchestrator = make_orchestrator()
    prepare_write_target(orchestrator, library, "WR0001L8")

    record = orchestrator.execute(
        TapeOpRequest(
            op_type=TapeOpType.WRITE,
            barcode="WR0001L8",
            tape_path="/data.bin",
            content=b"payload",
            size_bytes=7,
            requested_by="tester",
        )
    )

    stored = repo.get_tape_op(record.op_id)
    assert stored is not None
    assert stored["status"] == TapeOpStatus.COMPLETED.value
    assert stored["tape_path"] == "/data.bin"


def test_read_op_returns_bytes_count() -> None:
    _, library, ltfs, orchestrator = make_orchestrator()
    seed_read_data(library, ltfs, "RD0001L8", "/read.txt", b"hello-world")

    record = orchestrator.execute(
        TapeOpRequest(
            op_type=TapeOpType.READ,
            barcode="RD0001L8",
            tape_path="/read.txt",
            requested_by="tester",
        )
    )

    assert record.status is TapeOpStatus.COMPLETED
    assert record.result["bytes_read"] == 11


def test_verify_op_success() -> None:
    _, library, ltfs, orchestrator = make_orchestrator()
    content = b"verify-me"
    seed_read_data(library, ltfs, "VF0001L8", "/verify.txt", content)

    record = orchestrator.execute(
        TapeOpRequest(
            op_type=TapeOpType.VERIFY,
            barcode="VF0001L8",
            tape_path="/verify.txt",
            checksum_sha256=hashlib.sha256(content).hexdigest(),
            requested_by="tester",
        )
    )

    assert record.status is TapeOpStatus.COMPLETED
    assert record.result["verified"] is True


def test_verify_op_checksum_mismatch_returns_failed() -> None:
    _, library, ltfs, orchestrator = make_orchestrator()
    seed_read_data(library, ltfs, "VF0002L8", "/verify.txt", b"actual")

    record = orchestrator.execute(
        TapeOpRequest(
            op_type=TapeOpType.VERIFY,
            barcode="VF0002L8",
            tape_path="/verify.txt",
            checksum_sha256=hashlib.sha256(b"expected").hexdigest(),
            requested_by="tester",
        )
    )

    assert record.status is TapeOpStatus.FAILED
    assert record.error == "Checksum verification failed"


def test_format_op_requires_confirmation() -> None:
    _, library, _, orchestrator = make_orchestrator()
    library.seed_slots(["FM0001L8"])

    try:
        orchestrator.execute(
            TapeOpRequest(op_type=TapeOpType.FORMAT, barcode="FM0001L8", requested_by="tester")
        )
    except OperationNotConfirmedError:
        pass
    else:
        raise AssertionError("expected OperationNotConfirmedError")


def test_format_op_confirmed_succeeds() -> None:
    _, library, ltfs, orchestrator = make_orchestrator()
    library.seed_slots(["FM0002L8"])

    record = orchestrator.execute(
        TapeOpRequest(
            op_type=TapeOpType.FORMAT,
            barcode="FM0002L8",
            requested_by="tester",
            extras=format_extras("FM0002L8"),
        )
    )

    assert record.status is TapeOpStatus.COMPLETED
    assert ltfs.ensure_tape("FM0002L8").formatted is True


def test_move_op_same_slot_raises() -> None:
    _, library, _, orchestrator = make_orchestrator()
    library.seed_slots(["MV0001L8"])

    try:
        orchestrator.execute(
            TapeOpRequest(
                op_type=TapeOpType.MOVE,
                barcode="MV0001L8",
                slot_id=1,
                requested_by="tester",
                extras={"dest_slot_id": 1},
            )
        )
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_failed_op_has_safe_error_message() -> None:
    repo = make_repo()
    library = FailingLibrary(num_slots=4, num_drives=1)
    library.seed_slots(["FL0001L8"])
    ltfs = MockLTFSBackend(library)
    orchestrator = TapeOperationOrchestrator(repo, library, ltfs)

    record = orchestrator.execute(
        TapeOpRequest(
            op_type=TapeOpType.LOAD,
            barcode="FL0001L8",
            drive_id=0,
            slot_id=1,
            requested_by="tester",
        )
    )

    assert record.status is TapeOpStatus.FAILED
    assert record.error == "Tape load operation failed"


def test_failed_op_no_raw_exception_in_error() -> None:
    repo = make_repo()
    library = FailingLibrary(num_slots=4, num_drives=1)
    library.seed_slots(["FL0002L8"])
    ltfs = MockLTFSBackend(library)
    orchestrator = TapeOperationOrchestrator(repo, library, ltfs)

    record = orchestrator.execute(
        TapeOpRequest(
            op_type=TapeOpType.LOAD,
            barcode="FL0002L8",
            drive_id=0,
            slot_id=1,
            requested_by="tester",
        )
    )

    error_text = record.error or ""
    # Raw exception details must not leak through
    assert "SECRET" not in error_text
    assert "raw load failure" not in error_text
    # Exception class names and traceback markers must not appear
    assert "RuntimeError" not in error_text
    assert "Traceback" not in error_text
    assert "File " not in error_text
    assert "line " not in error_text


def test_list_ops_by_barcode() -> None:
    _, library, ltfs, orchestrator = make_orchestrator()
    seed_read_data(library, ltfs, "LS0001L8", "/one.txt", b"one")
    seed_read_data(library, ltfs, "LS0002L8", "/two.txt", b"two")
    orchestrator.execute(
        TapeOpRequest(op_type=TapeOpType.READ, barcode="LS0001L8", tape_path="/one.txt")
    )
    orchestrator.execute(
        TapeOpRequest(op_type=TapeOpType.READ, barcode="LS0002L8", tape_path="/two.txt")
    )

    records = orchestrator.list_ops(barcode="LS0001L8")

    assert len(records) == 1
    assert records[0].barcode == "LS0001L8"


def test_list_ops_by_status() -> None:
    _, library, ltfs, orchestrator = make_orchestrator()
    seed_read_data(library, ltfs, "ST0001L8", "/ok.txt", b"ok")
    seed_read_data(library, ltfs, "ST0002L8", "/bad.txt", b"bad")
    orchestrator.execute(
        TapeOpRequest(op_type=TapeOpType.READ, barcode="ST0001L8", tape_path="/ok.txt")
    )
    orchestrator.execute(
        TapeOpRequest(
            op_type=TapeOpType.VERIFY,
            barcode="ST0002L8",
            tape_path="/bad.txt",
            checksum_sha256=hashlib.sha256(b"different").hexdigest(),
        )
    )

    records = orchestrator.list_ops(status=TapeOpStatus.FAILED.value)

    assert len(records) == 1
    assert records[0].status is TapeOpStatus.FAILED


def test_get_op_returns_none_for_unknown() -> None:
    _, _, _, orchestrator = make_orchestrator()

    assert orchestrator.get_op("missing-op") is None


def test_op_log_created_before_execution() -> None:
    repo = make_repo()
    library = ObservedLibrary(repo)
    library.seed_slots(["OB0001L8"])
    ltfs = MockLTFSBackend(library)
    orchestrator = TapeOperationOrchestrator(repo, library, ltfs)

    orchestrator.execute(
        TapeOpRequest(
            op_type=TapeOpType.LOAD,
            barcode="OB0001L8",
            drive_id=0,
            slot_id=1,
            requested_by="tester",
        )
    )

    assert library.observed_created is True


def test_op_log_completed_after_execution() -> None:
    repo, library, _, orchestrator = make_orchestrator()
    library.seed_slots(["DN0001L8"])

    record = orchestrator.execute(
        TapeOpRequest(
            op_type=TapeOpType.LOAD,
            barcode="DN0001L8",
            drive_id=0,
            slot_id=1,
            requested_by="tester",
        )
    )

    stored = repo.get_tape_op(record.op_id)
    assert stored is not None
    assert stored["completed_at"] is not None
    assert stored["status"] == TapeOpStatus.COMPLETED.value


def test_write_op_result_has_checksum() -> None:
    _, library, _, orchestrator = make_orchestrator()
    prepare_write_target(orchestrator, library, "SM0001L8")
    content = b"checksum-data"

    record = orchestrator.execute(
        TapeOpRequest(
            op_type=TapeOpType.WRITE,
            barcode="SM0001L8",
            tape_path="/checksum.bin",
            content=content,
            size_bytes=len(content),
            requested_by="tester",
        )
    )

    assert record.result["checksum"] == hashlib.sha256(content).hexdigest()


def test_api_execute_requires_auth(tmp_path) -> None:
    client = make_client(tmp_path, "tape-ops-api-1.db")

    response = client.post(
        "/tape-ops/execute", json={"op_type": "read", "barcode": "AP0001L8", "tape_path": "/x"}
    )

    assert response.status_code == 401


def test_api_get_op_requires_auth(tmp_path) -> None:
    client = make_client(tmp_path, "tape-ops-api-2.db")

    response = client.get("/tape-ops/some-op")

    assert response.status_code == 401


def test_api_list_ops_requires_auth(tmp_path) -> None:
    client = make_client(tmp_path, "tape-ops-api-3.db")

    response = client.get("/tape-ops")

    assert response.status_code == 401


# --- Real-data campaign regressions -----------------------------------------
# docs/runbooks/real-data-campaign.md: `openblade format confirm` failed against
# real hardware for every cartridge in a slot, because _format never loaded the
# cartridge into a drive. mkltfs runs against a drive node; MockLTFSBackend.format
# only needs a barcode, which is why the simulator never showed it.


class _DriveObservingLTFS(MockLTFSBackend):
    """Records where the cartridge was at the moment format() was called."""

    def __init__(self, library: MockLibraryBackend, *, fail: bool = False) -> None:
        super().__init__(library)
        self._library_ref = library
        self.drive_at_format: int | None = None
        self.format_called = False
        self._fail = fail

    def format(self, barcode, confirmation=None):
        self.format_called = True
        self.drive_at_format = self._library_ref.find_drive_by_barcode(barcode)
        if self._fail:
            raise RuntimeError("SECRET mkltfs blew up")
        return super().format(barcode, confirmation)


def _format_request(barcode: str) -> TapeOpRequest:
    return TapeOpRequest(
        op_type=TapeOpType.FORMAT,
        barcode=barcode,
        requested_by="tester",
        extras=format_extras(barcode),
    )


def test_format_loads_the_cartridge_into_a_drive_first() -> None:
    library = MockLibraryBackend(num_slots=4, num_drives=1)
    ltfs = _DriveObservingLTFS(library)
    _, _, _, orchestrator = make_orchestrator(library=library, ltfs=ltfs)
    library.seed_slots(["FM0010L8"])

    record = orchestrator.execute(_format_request("FM0010L8"))

    assert record.status is TapeOpStatus.COMPLETED
    assert ltfs.format_called is True
    assert ltfs.drive_at_format == 0, "format ran with the cartridge still in its slot"


def test_format_returns_the_cartridge_to_its_original_slot() -> None:
    library = MockLibraryBackend(num_slots=4, num_drives=1)
    ltfs = _DriveObservingLTFS(library)
    _, _, _, orchestrator = make_orchestrator(library=library, ltfs=ltfs)
    library.seed_slots(["FM0011L8"])
    origin = library.find_slot_by_barcode("FM0011L8")

    orchestrator.execute(_format_request("FM0011L8"))

    # `drive_at_format` is asserted too, so this test also fails on a full revert
    # of the load -- without it, "not in a drive afterwards" passes trivially
    # because the cartridge was never put in one.
    assert ltfs.drive_at_format == 0
    assert library.find_drive_by_barcode("FM0011L8") is None
    assert library.find_slot_by_barcode("FM0011L8") == origin


def test_format_returns_the_cartridge_to_a_slot_even_when_it_fails() -> None:
    """A failed format must not strand the cartridge in the drive."""
    library = MockLibraryBackend(num_slots=4, num_drives=1)
    ltfs = _DriveObservingLTFS(library, fail=True)
    _, _, _, orchestrator = make_orchestrator(library=library, ltfs=ltfs)
    library.seed_slots(["FM0012L8"])

    origin = library.find_slot_by_barcode("FM0012L8")

    record = orchestrator.execute(_format_request("FM0012L8"))

    assert record.status is TapeOpStatus.FAILED
    assert ltfs.drive_at_format == 0, "the cartridge was never loaded, so this proves nothing"
    assert library.find_drive_by_barcode("FM0012L8") is None
    assert library.find_slot_by_barcode("FM0012L8") == origin


def test_format_leaves_an_already_loaded_cartridge_in_its_drive() -> None:
    """Only put back what we took out -- same discipline as _write."""
    library = MockLibraryBackend(num_slots=4, num_drives=1)
    ltfs = _DriveObservingLTFS(library)
    _, _, _, orchestrator = make_orchestrator(library=library, ltfs=ltfs)
    library.seed_slots(["FM0013L8"])
    library.load(library.find_slot_by_barcode("FM0013L8"), 0)

    orchestrator.execute(_format_request("FM0013L8"))

    assert library.find_drive_by_barcode("FM0013L8") == 0


def test_failure_log_carries_the_real_cause_while_the_record_stays_curated() -> None:
    """The curated message is the contract; the log must still name the cause.

    Without this, an operator whose format failed on real hardware sees only the
    constant string "Tape format operation failed" and the cause exists nowhere.
    """
    from structlog.testing import capture_logs

    library = MockLibraryBackend(num_slots=4, num_drives=1)
    ltfs = _DriveObservingLTFS(library, fail=True)
    _, _, _, orchestrator = make_orchestrator(library=library, ltfs=ltfs)
    library.seed_slots(["FM0014L8"])

    with capture_logs() as logs:
        record = orchestrator.execute(_format_request("FM0014L8"))

    # The record that crosses the trust boundary stays curated.
    assert record.error == "Tape format operation failed"
    assert "SECRET" not in (record.error or "")

    failures = [entry for entry in logs if entry.get("event") == "tape operation failed"]
    assert failures, "the failure was not logged at all"
    assert failures[0]["error"] == "Tape format operation failed"
    assert failures[0]["cause"] == "SECRET mkltfs blew up"
    assert failures[0]["cause_type"] == "RuntimeError"


def test_move_refuses_a_destination_outside_the_storage_slots() -> None:
    """A move destination goes straight to `mtx transfer`; mtx element numbers
    continue past the storage slots into the import/export magazine.

    Real-data campaign: POST /tape-ops/execute with dest_slot_id 9 physically
    ejected a cartridge holding 358 archived files to the mailslot, after which
    `inventory()` could not see it and nothing on it could be restored.
    """
    _, library, _, orchestrator = make_orchestrator()
    library.seed_slots(["MV0100L8"])
    slot = library.find_slot_by_barcode("MV0100L8")
    beyond = max(s.slot_id for s in library.inventory().slots) + 1

    # Rejected during validation, like the same-slot case above, so the caller
    # gets a 4xx rather than a queued-then-failed op.
    with pytest.raises(ValueError, match="import/export"):
        orchestrator.execute(
            TapeOpRequest(
                op_type=TapeOpType.MOVE,
                barcode="MV0100L8",
                slot_id=slot,
                requested_by="tester",
                extras={"dest_slot_id": beyond},
            )
        )

    assert library.find_slot_by_barcode("MV0100L8") == slot, "the cartridge moved anyway"


def test_move_to_a_real_storage_slot_still_works() -> None:
    """The guard must not break the ordinary slot-to-slot move."""
    _, library, _, orchestrator = make_orchestrator()
    library.seed_slots(["MV0101L8"])
    slot = library.find_slot_by_barcode("MV0101L8")
    empty = next(s.slot_id for s in library.inventory().slots if s.barcode is None)

    record = orchestrator.execute(
        TapeOpRequest(
            op_type=TapeOpType.MOVE,
            barcode="MV0101L8",
            slot_id=slot,
            requested_by="tester",
            extras={"dest_slot_id": empty},
        )
    )

    assert record.status is TapeOpStatus.COMPLETED
    assert library.find_slot_by_barcode("MV0101L8") == empty


def test_format_refuses_a_bare_confirmed_format_flag() -> None:
    """`extras` is bound straight from the POST /tape-ops/execute body.

    Before this, `{"confirmed_format": true}` -- a boolean an operator can type --
    was the entire gate on a destructive operation, and `_format` then minted the
    SafetyToken that was supposed to authorise it. AGENTS.md requires "positive
    barcode confirmation and a cryptographically valid safety token".
    """
    _, library, ltfs, orchestrator = make_orchestrator()
    library.seed_slots(["FM0020L8"])

    with pytest.raises(OperationNotConfirmedError):
        orchestrator.execute(
            TapeOpRequest(
                op_type=TapeOpType.FORMAT,
                barcode="FM0020L8",
                requested_by="attacker",
                extras={"confirmed_format": True},
            )
        )

    assert ltfs.ensure_tape("FM0020L8").formatted is False


def test_format_refuses_a_confirmation_for_a_different_barcode() -> None:
    """A token authorises one cartridge, not any cartridge."""
    _, library, ltfs, orchestrator = make_orchestrator()
    library.seed_slots(["FM0021L8", "FM0022L8"])

    record = orchestrator.execute(
        TapeOpRequest(
            op_type=TapeOpType.FORMAT,
            barcode="FM0022L8",
            requested_by="attacker",
            extras=format_extras("FM0021L8"),
        )
    )

    assert record.status is TapeOpStatus.FAILED
    assert record.error == "Tape format operation failed"  # curated, not the raw mismatch
    assert ltfs.ensure_tape("FM0022L8").formatted is False
