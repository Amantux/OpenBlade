"""Tape operation orchestration and audit logging."""

from __future__ import annotations

import hashlib
import threading
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

import structlog

from openblade.catalog.repository import CatalogRepository
from openblade.domain.errors import (
    ChecksumMismatchError,
    ExportRefusedError,
    ImportExportSlotError,
    MailslotUnsupportedError,
)
from openblade.domain.models import MountMode
from openblade.domain.policies import FormatConfirmation
from openblade.nas.types import TapeOpRecord, TapeOpRequest, TapeOpStatus, TapeOpType

logger = structlog.get_logger(__name__)


class OperationNotConfirmedError(Exception):
    """Raised when a destructive tape operation lacks explicit confirmation."""


class TapeOperationFailedError(RuntimeError):
    """Raised when a tape operation is recorded as failed."""


class TapeOperationOrchestrator:
    """Single choke point for tape hardware operations."""

    def __init__(self, repo: CatalogRepository, library: Any, ltfs: Any) -> None:
        """Initialize the orchestrator with persistence and hardware backends."""
        self.repo = repo
        self.library = library
        self.ltfs = ltfs
        self._drive_locks: dict[int, threading.Lock] = {}
        self._barcode_locks: dict[str, threading.Lock] = {}
        self._lock = threading.RLock()

    def execute(self, request: TapeOpRequest) -> TapeOpRecord:
        """Validate, log, execute, and complete a tape operation request."""
        self._validate_request(request)
        created_at = _utcnow_iso()
        op_id = str(uuid4())
        self.repo.create_tape_op(
            {
                "op_id": op_id,
                "op_type": request.op_type.value,
                "barcode": request.barcode,
                "drive_id": request.drive_id,
                "slot_id": request.slot_id,
                "tape_path": request.tape_path,
                "size_bytes": request.size_bytes,
                "checksum_sha256": request.checksum_sha256,
                "requested_by": request.requested_by,
                "job_id": request.job_id,
                "priority": request.priority,
                "status": TapeOpStatus.QUEUED.value,
                "result": {},
                "error": None,
                "created_at": created_at,
            }
        )
        logger.info(
            "tape operation queued",
            op_id=op_id,
            op_type=request.op_type.value,
            barcode=request.barcode,
        )
        self.repo.update_tape_op(
            op_id,
            {
                "status": TapeOpStatus.RUNNING.value,
                "started_at": _utcnow_iso(),
            },
        )
        try:
            result = self._execute_locked(request)
            persisted = self.repo.update_tape_op(
                op_id,
                {
                    "status": TapeOpStatus.COMPLETED.value,
                    "result": result,
                    "error": None,
                    "completed_at": _utcnow_iso(),
                },
            )
            assert persisted is not None
            logger.info(
                "tape operation completed",
                op_id=op_id,
                op_type=request.op_type.value,
                barcode=request.barcode,
            )
            return TapeOpRecord.model_validate(persisted)
        except Exception as exc:
            safe_error = self._safe_error_message(request.op_type, exc)
            persisted = self.repo.update_tape_op(
                op_id,
                {
                    "status": TapeOpStatus.FAILED.value,
                    "error": safe_error,
                    "completed_at": _utcnow_iso(),
                },
            )
            assert persisted is not None
            # The persisted/returned `error` stays curated -- it crosses a trust
            # boundary and must never carry raw exception text. The server log is
            # not that boundary, and without the cause here an operator whose
            # format failed has no diagnostic anywhere on the host: the message
            # they see is a constant string per op type. Log the real cause.
            logger.warning(
                "tape operation failed",
                op_id=op_id,
                op_type=request.op_type.value,
                barcode=request.barcode,
                error=safe_error,
                cause_type=type(exc).__name__,
                cause=str(exc),
                exc_info=True,
            )
            if isinstance(exc, OperationNotConfirmedError | ExportRefusedError):
                # A refusal is not a failed operation the caller may shrug at: it
                # is a decision with a reason, and the reason is the message. The
                # audit row above is still written as failed.
                raise exc
            return TapeOpRecord.model_validate(persisted)

    def get_op(self, op_id: str) -> TapeOpRecord | None:
        """Return a tape operation record by id."""
        payload = self.repo.get_tape_op(op_id)
        return None if payload is None else TapeOpRecord.model_validate(payload)

    def list_ops(
        self,
        barcode: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[TapeOpRecord]:
        """Return tape operation records filtered by barcode and status."""
        return [
            TapeOpRecord.model_validate(payload)
            for payload in self.repo.list_tape_ops(barcode=barcode, status=status, limit=limit)
        ]

    def _execute_locked(self, request: TapeOpRequest) -> dict[str, Any]:
        if request.op_type in {
            TapeOpType.READ,
            TapeOpType.VERIFY,
            # Import/export resolve an element from the barcode and then move
            # it. Without the lock, a concurrent restore can load that tape into
            # a drive in between, and the export then moves whichever cartridge
            # next occupies the slot -- the threaded version of trusting a stale
            # slot id.
            TapeOpType.IMPORT,
            TapeOpType.EXPORT,
        }:
            with self._barcode_lock(request.barcode):
                return self._dispatch(request)
        if request.op_type in {TapeOpType.WRITE, TapeOpType.FORMAT}:
            drive_id = self._resolve_drive_for_write(request)
            with self._drive_lock(drive_id):
                return self._dispatch(request)
        return self._dispatch(request)

    def _dispatch(self, request: TapeOpRequest) -> dict[str, Any]:
        if request.op_type is TapeOpType.LOAD:
            return self._load(request)
        if request.op_type is TapeOpType.UNLOAD:
            return self._unload(request)
        if request.op_type is TapeOpType.FORMAT:
            return self._format(request)
        if request.op_type is TapeOpType.WRITE:
            return self._write(request)
        if request.op_type is TapeOpType.READ:
            return self._read(request)
        if request.op_type is TapeOpType.MOVE:
            return self._move(request)
        if request.op_type is TapeOpType.VERIFY:
            return self._verify(request)
        if request.op_type is TapeOpType.EJECT:
            return self._eject(request)
        if request.op_type is TapeOpType.IMPORT:
            return self._import(request)
        if request.op_type is TapeOpType.EXPORT:
            return self._export(request)
        raise ValueError(f"Unsupported tape op {request.op_type.value}")

    def _validate_request(self, request: TapeOpRequest) -> None:
        barcode = request.barcode.strip()
        if not barcode:
            raise ValueError("barcode must be non-empty")
        if (
            request.op_type in {TapeOpType.READ, TapeOpType.WRITE, TapeOpType.VERIFY}
            and not request.tape_path
        ):
            raise ValueError("tape_path is required for read, write, and verify operations")
        if request.op_type is TapeOpType.WRITE and request.content is None:
            raise ValueError("content is required for write operations")
        if request.op_type is TapeOpType.MOVE:
            source_slot = self._source_slot(request)
            dest_slot = self._dest_slot(request)
            if source_slot == dest_slot:
                raise ValueError("source slot and destination slot must differ")
        if (
            request.op_type is TapeOpType.FORMAT
            and request.extras.get("confirmed_format") is not True
        ):
            raise OperationNotConfirmedError("Format operations require explicit confirmation")

    def _load(self, request: TapeOpRequest) -> dict[str, Any]:
        slot_id = (
            request.slot_id
            if request.slot_id is not None
            else self.library.find_slot_by_barcode(request.barcode)
        )
        if slot_id is None:
            raise ValueError(f"Barcode {request.barcode} is not present in a slot")
        drive_id = request.drive_id if request.drive_id is not None else 0
        result = self.library.load(slot_id, drive_id)
        return self._operation_result(
            result, {"barcode": request.barcode, "drive_id": drive_id, "slot_id": slot_id}
        )

    def _unload(self, request: TapeOpRequest) -> dict[str, Any]:
        drive_id = request.drive_id
        if drive_id is None:
            drive_id = self.library.find_drive_by_barcode(request.barcode)
        if drive_id is None:
            raise ValueError(f"Barcode {request.barcode} is not loaded in a drive")
        slot_id = request.slot_id if request.slot_id is not None else self._find_empty_slot()
        result = self.library.unload(drive_id, slot_id)
        return self._operation_result(
            result, {"barcode": request.barcode, "drive_id": drive_id, "slot_id": slot_id}
        )

    def _format(self, request: TapeOpRequest) -> dict[str, Any]:
        confirmation = request.extras.get("format_confirmation")
        if not isinstance(confirmation, FormatConfirmation):
            # It used to MINT its own SafetyToken here. `extras` is bound straight
            # from the `POST /tape-ops/execute` body, so `{"confirmed_format":true}`
            # -- a bare boolean an operator can type -- was the entire gate on a
            # destructive operation, and the orchestrator then issued itself the
            # token that was supposed to authorise it. AGENTS.md: "Never perform
            # format or erase operations without positive barcode confirmation and
            # a cryptographically valid safety token."
            #
            # This was previously inert against real hardware only by accident --
            # the format failed before reaching mkltfs because the cartridge was
            # never loaded. Fixing that (same commit series) made the hole live,
            # which is why it is closed here rather than left as pre-existing.
            #
            # The legitimate path (FormatService.confirm -> run_format_job) always
            # supplies a FormatConfirmation carrying the token it just validated
            # against the persisted safety_tokens row.
            raise OperationNotConfirmedError(
                "Format requires a FormatConfirmation carrying a valid safety token; "
                "obtain one from the format dry-run and confirm through FormatService"
            )
        confirmation.validate(request.barcode)
        # mkltfs runs against a drive, so the cartridge has to be in one. The
        # simulator's format() only needs a barcode, which is why this was never
        # noticed: against real hardware every `openblade format confirm` on a
        # cartridge sitting in its slot -- the normal state -- failed with
        # "Barcode ... is not loaded in a drive" behind the curated message.
        # Same load/restore discipline as _write: only put back what we took out.
        drive_id, loaded_slot = self._ensure_loaded(request.barcode, request.drive_id)
        try:
            result = self.ltfs.format(request.barcode, confirmation)
        finally:
            if loaded_slot is not None:
                self.library.unload(drive_id, loaded_slot)
        return self._operation_result(
            result,
            {"barcode": request.barcode, "formatted": True, "drive_id": drive_id},
        )

    def _write(self, request: TapeOpRequest) -> dict[str, Any]:
        drive_id, loaded_slot = self._ensure_loaded(request.barcode, request.drive_id)
        handle = self.ltfs.mount(request.barcode, MountMode.READ_WRITE)
        try:
            content = request.content or b""
            self.ltfs.write_bytes(
                handle,
                PurePosixPath(request.tape_path or "/"),
                content,
                size_bytes=request.size_bytes,
                checksum_sha256=request.checksum_sha256,
            )
            stat = self.ltfs.stat(handle, PurePosixPath(request.tape_path or "/"))
            return {
                "drive_id": drive_id,
                "slot_id": loaded_slot,
                "tape_path": request.tape_path,
                "bytes_written": stat.size_bytes,
                "checksum": stat.checksum_sha256,
            }
        finally:
            self.ltfs.unmount(handle)
            if loaded_slot is not None:
                self.library.unload(drive_id, loaded_slot)

    def _read(self, request: TapeOpRequest) -> dict[str, Any]:
        data = self.ltfs.read_bytes(request.barcode, request.tape_path)
        if data is None:
            raise FileNotFoundError(f"Tape path {request.tape_path} not found")
        checksum = hashlib.sha256(data).hexdigest()
        return {"bytes_read": len(data), "checksum": checksum}

    def _verify(self, request: TapeOpRequest) -> dict[str, Any]:
        data = self.ltfs.read_bytes(request.barcode, request.tape_path)
        if data is None:
            raise FileNotFoundError(f"Tape path {request.tape_path} not found")
        checksum = hashlib.sha256(data).hexdigest()
        if request.checksum_sha256 and checksum != request.checksum_sha256:
            raise ChecksumMismatchError("checksum mismatch")
        return {
            "bytes_read": len(data),
            "checksum": checksum,
            "verified": True,
        }

    def _move(self, request: TapeOpRequest) -> dict[str, Any]:
        source_slot = self._source_slot(request)
        dest_slot = self._dest_slot(request)
        result = self.library.move(source_slot, dest_slot)
        return self._operation_result(
            result,
            {
                "barcode": request.barcode,
                "source_slot": source_slot,
                "destination_slot": dest_slot,
            },
        )

    def _eject(self, request: TapeOpRequest) -> dict[str, Any]:
        eject = getattr(self.library, "eject", None)
        if callable(eject):
            result = eject(request.barcode)
            return self._operation_result(result, {"barcode": request.barcode, "ejected": True})
        return self._unload(request)

    def _mailslot_library(self) -> Any:
        """The library backend, or a typed refusal if it has no I/E station."""
        from openblade.domain.backends import MailslotBackend

        if not isinstance(self.library, MailslotBackend):
            raise MailslotUnsupportedError(
                f"{type(self.library).__name__} has no import/export station; "
                "mailslot operations are not available on this backend"
            )
        return self.library

    def _import_export_elements(self, library: Any) -> dict[int, Any]:
        return {slot.slot_id: slot for slot in library.import_export_slots()}

    def _require_ie_element(self, library: Any, ie_slot: int) -> Any:
        """An I/E element that actually exists on THIS library.

        ``extras["ie_slot"]`` reaches ``mtx transfer`` as a raw element number,
        and mtx validates nothing. ``_dest_slot`` already refuses an unvalidated
        destination for MOVE, with a comment explaining that exact incident
        (defect 3.9); these op types have to do the same or they reopen it.
        """
        elements = self._import_export_elements(library)
        element = elements.get(ie_slot)
        if element is None:
            raise ImportExportSlotError(
                f"element {ie_slot} is not an import/export element in this library "
                f"(valid: {sorted(elements) or 'none — this library has no mailslot'})"
            )
        return element

    def _require_storage_slot(self, slot_id: int) -> Any:
        slots = {slot.slot_id: slot for slot in self.library.inventory().slots}
        slot = slots.get(slot_id)
        if slot is None:
            raise ImportExportSlotError(
                f"slot {slot_id} is not a data storage slot in this library "
                f"(valid: {min(slots, default=0)}-{max(slots, default=0)})"
            )
        return slot

    def _import(self, request: TapeOpRequest) -> dict[str, Any]:
        """Move media from an import/export element into a storage slot.

        Every element number is validated against the library's OWN elements,
        and the named barcode must be the cartridge actually sitting in the
        source element -- `barcode` is what the guard and the catalog write use,
        so a request where the two disagree must not be executed.
        """
        library = self._mailslot_library()
        ie_slot = _required_int(request.extras, "ie_slot", "import")
        source = self._require_ie_element(library, ie_slot)
        if source.barcode is None:
            raise ImportExportSlotError(f"import/export element {ie_slot} is empty")
        if str(source.barcode) != request.barcode:
            raise ImportExportSlotError(
                f"import/export element {ie_slot} holds {source.barcode}, "
                f"not {request.barcode}; refusing to move a cartridge the request "
                "does not name"
            )
        target_slot = request.slot_id
        if target_slot is None:
            raise ImportExportSlotError("slot_id (destination storage slot) is required for import")
        target = self._require_storage_slot(target_slot)
        if target.barcode is not None:
            raise ImportExportSlotError(
                f"storage slot {target_slot} already holds {target.barcode}"
            )

        result = library.import_cartridge(ie_slot, target_slot)
        # The catalog write belongs HERE, beside the move, not in the service:
        # otherwise `POST /tape-ops/execute` moves the media and leaves the
        # catalog's view of it stale.
        self._record_cartridge_state(request.barcode, "in_slot")
        return self._operation_result(
            result,
            {"barcode": request.barcode, "ie_slot": ie_slot, "target_slot": target_slot},
        )

    def _export(self, request: TapeOpRequest) -> dict[str, Any]:
        """Move media from a storage slot into an import/export element.

        The data guard lives HERE rather than only in the service, because this
        is the choke point every surface goes through. ``extras["force"]`` is
        the single documented override.

        The source slot is resolved FROM THE BARCODE, never taken from
        ``request.slot_id``. Trusting that field meant the guard assessed one
        cartridge while the robot moved whichever one happened to be in the
        named slot -- naming an empty tape and pointing `slot_id` at a loaded
        one ejected a cartridge holding archived files past a guard that had
        just reported "carries no data". That is defect 3.9 with extra steps.
        ``slot_id`` is now only ever a cross-check.
        """
        library = self._mailslot_library()
        source_slot = self.library.find_slot_by_barcode(request.barcode)
        if source_slot is None:
            raise ImportExportSlotError(
                f"Barcode {request.barcode} is not in a storage slot; "
                "unload it from its drive before exporting"
            )
        if request.slot_id is not None and request.slot_id != source_slot:
            raise ImportExportSlotError(
                f"{request.barcode} is in slot {source_slot}, not the requested "
                f"slot {request.slot_id}; refusing to move a cartridge the request "
                "does not name"
            )
        # Guard AFTER resolving, so it assesses the cartridge actually moving.
        self._guard_export(request)
        ie_slot = _required_int(request.extras, "ie_slot", "export")
        target = self._require_ie_element(library, ie_slot)
        if target.barcode is not None:
            raise ImportExportSlotError(
                f"import/export element {ie_slot} already holds {target.barcode}"
            )

        result = library.export_cartridge_to_ie(source_slot, ie_slot)
        # Every restore path reads this flag; writing it beside the move is what
        # makes it true for /tape-ops/execute as well as for the CLI.
        self._record_cartridge_state(request.barcode, "exported")
        return self._operation_result(
            result,
            {"barcode": request.barcode, "source_slot": source_slot, "ie_slot": ie_slot},
        )

    def _record_cartridge_state(self, barcode: str, state: str) -> None:
        """Persist a cartridge's catalog state, if this repo has a catalog.

        add_cartridge first: catalog file_instances key on the BARCODE, not on a
        cartridges row, so a tape can carry archived data with no row at all and
        set_cartridge_state would silently no-op on it.
        """
        add = getattr(self.repo, "add_cartridge", None)
        setter = getattr(self.repo, "set_cartridge_state", None)
        if add is None or setter is None:
            return
        add(barcode)
        setter(barcode, state)

    def _guard_export(self, request: TapeOpRequest) -> None:
        if request.extras.get("force") is True:
            return
        assess = getattr(self.repo, "list_instances_for_barcode", None)
        if assess is None:
            # A transient (catalog-less) repository cannot tell us what is on the
            # cartridge. Fail closed: an unknown payload is not an empty one.
            raise ExportRefusedError(
                f"Cannot determine what is on cartridge {request.barcode} without a "
                "catalog; refusing to export. Re-run against the catalog, or force."
            )
        from openblade.catalog.export_policy import assess_export

        assessment = assess_export(self.repo, request.barcode)
        if assessment.carries_data:
            raise ExportRefusedError(assessment.refusal_message())

    def _ensure_loaded(self, barcode: str, drive_id: int | None) -> tuple[int, int | None]:
        loaded_drive_id = self.library.find_drive_by_barcode(barcode)
        if loaded_drive_id is not None:
            return loaded_drive_id, None
        slot_id = self.library.find_slot_by_barcode(barcode)
        if slot_id is None:
            raise ValueError(f"Barcode {barcode} is not present in simulator inventory")
        target_drive_id = drive_id if drive_id is not None else 0
        self.library.load(slot_id, target_drive_id)
        return target_drive_id, slot_id

    def _resolve_drive_for_write(self, request: TapeOpRequest) -> int:
        if request.drive_id is not None:
            return request.drive_id
        loaded_drive_id = self.library.find_drive_by_barcode(request.barcode)
        if loaded_drive_id is not None:
            return loaded_drive_id
        return 0

    def _source_slot(self, request: TapeOpRequest) -> int:
        source_slot = request.extras.get(
            "source_slot_id", request.extras.get("source_slot", request.slot_id)
        )
        if source_slot is None:
            slot_id = self.library.find_slot_by_barcode(request.barcode)
            if slot_id is None:
                raise ValueError(f"Barcode {request.barcode} is not present in a slot")
            return slot_id
        return int(source_slot)

    def _dest_slot(self, request: TapeOpRequest) -> int:
        destination = request.extras.get(
            "dest_slot_id", request.extras.get("dest_slot", request.slot_id)
        )
        if destination is None:
            raise ValueError("destination slot is required for move operations")
        destination_id = int(destination)
        known_slots = {slot.slot_id for slot in self.library.inventory().slots}
        if destination_id not in known_slots:
            # This integer went straight to `mtx transfer` unvalidated, and mtx
            # element numbers continue past the storage slots into the
            # import/export magazine. On the rig, POST /tape-ops/execute with
            # dest_slot_id 9 physically ejected a cartridge holding 358 archived
            # files into the mailslot -- after which `inventory()` (storage slots
            # only, by design) could not see it at all, so nothing on it could be
            # restored. One unconfirmed integer orphaned a third of an archive.
            #
            # Moving media out of the library is an export, and the product's
            # position on export is already explicit: routes_aml_move_medium
            # rejects moveClass import/export on i3/i6. Refuse here too rather
            # than let a typo do it silently.
            raise ValueError(
                f"destination slot {destination_id} is not a data storage slot in this "
                f"library (valid: {min(known_slots, default=0)}-{max(known_slots, default=0)}); "
                "moving media to an import/export element is not supported"
            )
        return destination_id

    def _find_empty_slot(self) -> int:
        inventory = self.library.inventory()
        for slot in inventory.slots:
            if slot.barcode is None:
                return slot.slot_id
        raise ValueError("No empty slot is available for unload/eject")

    def _drive_lock(self, drive_id: int) -> threading.Lock:
        with self._lock:
            return self._drive_locks.setdefault(drive_id, threading.Lock())

    def _barcode_lock(self, barcode: str) -> threading.Lock:
        with self._lock:
            return self._barcode_locks.setdefault(barcode, threading.Lock())

    @staticmethod
    def _operation_result(result: Any, fallback: dict[str, Any]) -> dict[str, Any]:
        if hasattr(result, "success") and hasattr(result, "details"):
            payload = dict(result.details or {})
            payload.setdefault("success", bool(result.success))
            payload.setdefault("message", str(getattr(result, "message", "")))
            for key, value in fallback.items():
                payload.setdefault(key, value)
            return payload
        return fallback

    @staticmethod
    def _safe_error_message(op_type: TapeOpType, exc: Exception) -> str:
        if isinstance(exc, OperationNotConfirmedError):
            return "Format operations require explicit confirmation"
        if isinstance(exc, ChecksumMismatchError):
            return "Checksum verification failed"
        if isinstance(exc, ExportRefusedError | MailslotUnsupportedError | ImportExportSlotError):
            # Operator-written text that exists precisely to be read: it names the
            # cartridge, the volume group, and what would go out of the door.
            # Replacing it with "Tape export operation failed" would defeat it.
            return str(exc)
        safe_messages = {
            TapeOpType.LOAD: "Tape load operation failed",
            TapeOpType.UNLOAD: "Tape unload operation failed",
            TapeOpType.FORMAT: "Tape format operation failed",
            TapeOpType.WRITE: "Tape write operation failed",
            TapeOpType.READ: "Tape read operation failed",
            TapeOpType.MOVE: "Tape move operation failed",
            TapeOpType.VERIFY: "Tape verify operation failed",
            TapeOpType.EJECT: "Tape eject operation failed",
            TapeOpType.IMPORT: "Tape import operation failed",
            TapeOpType.EXPORT: "Tape export operation failed",
        }
        return safe_messages[op_type]


class _TransientTapeOpRepository:
    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}

    def create_tape_op(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._records[str(payload["op_id"])] = dict(payload)
        return dict(payload)

    def update_tape_op(self, op_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        record = self._records.get(op_id)
        if record is None:
            return None
        record.update(updates)
        return dict(record)


def execute_tape_request(
    repo: CatalogRepository | None,
    library: Any,
    ltfs: Any,
    request: TapeOpRequest,
    *,
    raise_on_failed: bool = False,
) -> TapeOpRecord:
    """Execute a tape operation through the orchestrator."""
    active_repo: CatalogRepository | _TransientTapeOpRepository = (
        repo or _TransientTapeOpRepository()
    )
    record = TapeOperationOrchestrator(active_repo, library, ltfs).execute(request)
    if raise_on_failed and record.status is TapeOpStatus.FAILED:
        raise TapeOperationFailedError(
            record.error or f"Tape {request.op_type.value} operation failed"
        )
    return record


def _required_int(extras: dict[str, Any], key: str, op_name: str) -> int:
    value = extras.get(key)
    if value is None:
        raise ValueError(f"extras[{key!r}] is required for {op_name} operations")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"extras[{key!r}] must be an element number") from exc


def _utcnow_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"
