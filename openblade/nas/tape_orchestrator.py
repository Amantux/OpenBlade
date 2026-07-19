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
from openblade.domain.errors import ChecksumMismatchError
from openblade.domain.models import MountMode
from openblade.domain.policies import FormatConfirmation, SafetyToken
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
        created = self.repo.create_tape_op(
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
        logger.info("tape operation queued", op_id=op_id, op_type=request.op_type.value, barcode=request.barcode)
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
            logger.warning(
                "tape operation failed",
                op_id=op_id,
                op_type=request.op_type.value,
                barcode=request.barcode,
                error=safe_error,
            )
            if isinstance(exc, OperationNotConfirmedError):
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
        if request.op_type in {TapeOpType.READ, TapeOpType.VERIFY}:
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
        raise ValueError(f"Unsupported tape op {request.op_type.value}")

    def _validate_request(self, request: TapeOpRequest) -> None:
        barcode = request.barcode.strip()
        if not barcode:
            raise ValueError("barcode must be non-empty")
        if request.op_type in {TapeOpType.READ, TapeOpType.WRITE, TapeOpType.VERIFY} and not request.tape_path:
            raise ValueError("tape_path is required for read, write, and verify operations")
        if request.op_type is TapeOpType.WRITE and request.content is None:
            raise ValueError("content is required for write operations")
        if request.op_type is TapeOpType.MOVE:
            source_slot = self._source_slot(request)
            dest_slot = self._dest_slot(request)
            if source_slot == dest_slot:
                raise ValueError("source slot and destination slot must differ")
        if request.op_type is TapeOpType.FORMAT and request.extras.get("confirmed_format") is not True:
            raise OperationNotConfirmedError("Format operations require explicit confirmation")

    def _load(self, request: TapeOpRequest) -> dict[str, Any]:
        slot_id = request.slot_id if request.slot_id is not None else self.library.find_slot_by_barcode(request.barcode)
        if slot_id is None:
            raise ValueError(f"Barcode {request.barcode} is not present in a slot")
        drive_id = request.drive_id if request.drive_id is not None else 0
        result = self.library.load(slot_id, drive_id)
        return self._operation_result(result, {"barcode": request.barcode, "drive_id": drive_id, "slot_id": slot_id})

    def _unload(self, request: TapeOpRequest) -> dict[str, Any]:
        drive_id = request.drive_id
        if drive_id is None:
            drive_id = self.library.find_drive_by_barcode(request.barcode)
        if drive_id is None:
            raise ValueError(f"Barcode {request.barcode} is not loaded in a drive")
        slot_id = request.slot_id if request.slot_id is not None else self._find_empty_slot()
        result = self.library.unload(drive_id, slot_id)
        return self._operation_result(result, {"barcode": request.barcode, "drive_id": drive_id, "slot_id": slot_id})

    def _format(self, request: TapeOpRequest) -> dict[str, Any]:
        confirmation = request.extras.get("format_confirmation")
        if not isinstance(confirmation, FormatConfirmation):
            confirmation = FormatConfirmation(
                expected_barcode=request.barcode,
                safety_token=SafetyToken.generate("format", request.barcode),
                operator_note=str(request.extras.get("operator_note", "")),
            )
        result = self.ltfs.format(request.barcode, confirmation)
        return self._operation_result(result, {"barcode": request.barcode, "formatted": True})

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
        source_slot = request.extras.get("source_slot_id", request.extras.get("source_slot", request.slot_id))
        if source_slot is None:
            slot_id = self.library.find_slot_by_barcode(request.barcode)
            if slot_id is None:
                raise ValueError(f"Barcode {request.barcode} is not present in a slot")
            return slot_id
        return int(source_slot)

    def _dest_slot(self, request: TapeOpRequest) -> int:
        destination = request.extras.get("dest_slot_id", request.extras.get("dest_slot", request.slot_id))
        if destination is None:
            raise ValueError("destination slot is required for move operations")
        return int(destination)

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
        safe_messages = {
            TapeOpType.LOAD: "Tape load operation failed",
            TapeOpType.UNLOAD: "Tape unload operation failed",
            TapeOpType.FORMAT: "Tape format operation failed",
            TapeOpType.WRITE: "Tape write operation failed",
            TapeOpType.READ: "Tape read operation failed",
            TapeOpType.MOVE: "Tape move operation failed",
            TapeOpType.VERIFY: "Tape verify operation failed",
            TapeOpType.EJECT: "Tape eject operation failed",
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
    active_repo: CatalogRepository | _TransientTapeOpRepository = repo or _TransientTapeOpRepository()
    record = TapeOperationOrchestrator(active_repo, library, ltfs).execute(request)
    if raise_on_failed and record.status is TapeOpStatus.FAILED:
        raise TapeOperationFailedError(record.error or f"Tape {request.op_type.value} operation failed")
    return record


def _utcnow_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"
