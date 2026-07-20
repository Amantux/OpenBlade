"""Archive job: source files → tape via LTFS → catalog."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from openblade.api import aml_state
from openblade.catalog.repository import CatalogRepository
from openblade.domain.backends import LibraryBackend, LTFSBackend
from openblade.domain.errors import ChecksumMismatchError, NoScratchMediaError
from openblade.domain.models import JobType, MountMode
from openblade.jobs.queue import JobQueue
from openblade.jobs.verify import sha256sum
from openblade.nas.tape_orchestrator import TapeOperationFailedError, execute_tape_request
from openblade.nas.types import TapeOpRequest, TapeOpType

logger = logging.getLogger(__name__)


@dataclass
class ArchiveRequest:
    source_path: Path
    volume_group_name: str
    dry_run: bool = False


@dataclass
class ArchiveResult:
    job_id: str
    files_archived: int
    bytes_archived: int
    tapes_used: list[str]
    errors: list[str]


def _iter_source_files(source_path: Path) -> list[Path]:
    if source_path.is_file():
        return [source_path]
    return sorted(path for path in source_path.rglob("*") if path.is_file())


def _inventory_barcodes(library: LibraryBackend) -> list[str]:
    inventory = library.inventory()
    barcodes = [str(slot.barcode) for slot in inventory.slots if slot.barcode is not None]
    barcodes.extend(str(drive.barcode) for drive in inventory.drives if drive.barcode is not None)
    return sorted(barcodes)


def _is_cleaning_barcode(barcode: str) -> bool:
    return barcode.upper().startswith("CLN")


def _choose_tape(
    catalog: CatalogRepository,
    library: LibraryBackend,
    ltfs: LTFSBackend,
    volume_group_id: str,
    size_bytes: int,
) -> str:
    assigned = [
        cartridge
        for cartridge in catalog.list_cartridges()
        if cartridge.volume_group_id == volume_group_id and cartridge.state != "exported"
    ]
    for cartridge in assigned:
        if _is_cleaning_barcode(cartridge.barcode):
            continue
        if ltfs.remaining_capacity(cartridge.barcode) >= size_bytes:
            return cartridge.barcode
    for barcode in _inventory_barcodes(library):
        if _is_cleaning_barcode(barcode):
            continue
        cartridge = catalog.add_cartridge(barcode)
        if (
            cartridge.volume_group_id not in {None, volume_group_id}
            or cartridge.state == "exported"
        ):
            continue
        if ltfs.remaining_capacity(barcode) < size_bytes:
            continue
        cartridge.volume_group_id = volume_group_id
        catalog.session.commit()
        return barcode
    raise NoScratchMediaError("No scratch media with sufficient capacity is available")


def _aml_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _aml_drive_name(drive_id: int) -> str:
    return f"DRV-{drive_id + 1:03d}"


def _aml_slot_address(slot_id: int) -> str:
    return f"1,1,{slot_id}"


def _mark_aml_drive_busy(barcode: str, drive_id: int) -> None:
    drive_name = _aml_drive_name(drive_id)
    drive = aml_state.get_aml_drive(drive_name)
    if drive is not None:
        media = aml_state.get_aml_media(barcode)
        aml_state.update_aml_drive(
            drive_name,
            {
                "state": "busy",
                "loadedMedia": {
                    "barcode": barcode,
                    "type": (media or {}).get("type", "LTO-9"),
                    "state": "loaded",
                },
            },
        )
    media = aml_state.get_aml_media(barcode)
    if media is not None:
        aml_state.update_aml_media(
            barcode,
            {
                "slotAddress": drive_name,
                "state": "loaded",
                "lastLoaded": _aml_timestamp(),
                "loadCount": int(media.get("loadCount", 0)) + 1,
            },
        )


def _mark_aml_drive_idle(barcode: str, drive_id: int, slot_id: int | None) -> None:
    drive_name = _aml_drive_name(drive_id)
    if aml_state.get_aml_drive(drive_name) is not None:
        aml_state.update_aml_drive(drive_name, {"state": "idle", "loadedMedia": None})
    media = aml_state.get_aml_media(barcode)
    if media is not None and slot_id is not None:
        aml_state.update_aml_media(barcode, {"slotAddress": _aml_slot_address(slot_id), "state": "home"})


def _load_if_needed(
    catalog: CatalogRepository,
    library: LibraryBackend,
    ltfs: LTFSBackend,
    barcode: str,
    job_id: str,
) -> tuple[int, int | None]:
    drive_id = library.find_drive_by_barcode(barcode)
    if drive_id is not None:
        return drive_id, None
    slot_id = library.find_slot_by_barcode(barcode)
    if slot_id is None:
        raise NoScratchMediaError(f"Barcode {barcode} not found in inventory")
    for drive in library.inventory().drives:
        if drive.barcode is not None:
            continue
        try:
            execute_tape_request(
                catalog,
                library,
                ltfs,
                TapeOpRequest(
                    op_type=TapeOpType.LOAD,
                    barcode=barcode,
                    drive_id=drive.drive_id,
                    slot_id=slot_id,
                    requested_by="archive-job",
                    job_id=job_id,
                ),
                raise_on_failed=True,
            )
            return drive.drive_id, slot_id
        except TapeOperationFailedError:
            continue
    raise NoScratchMediaError("No available drives for archive load")


def run_archive_job(
    request: ArchiveRequest,
    library: LibraryBackend,
    ltfs: LTFSBackend,
    catalog: CatalogRepository,
    job_id: str,
) -> ArchiveResult:
    """Archive source files onto LTFS media and record them in the catalog."""
    catalog.update_job_state(job_id, "running")
    files = _iter_source_files(request.source_path)
    volume_group = catalog.get_volume_group(request.volume_group_name)
    if volume_group is None:
        volume_group = catalog.create_volume_group(request.volume_group_name)
    if request.dry_run:
        result = ArchiveResult(
            job_id, len(files), sum(path.stat().st_size for path in files), [], []
        )
        catalog.update_job_state(job_id, "completed")
        return result
    if not files:
        result = ArchiveResult(job_id, 0, 0, [], [])
        catalog.update_job_state(job_id, "completed")
        return result

    current_barcode: str | None = None
    current_handle = None
    current_slot_id: int | None = None
    current_drive_id: int | None = None
    pending_instance_ids: list[str] = []
    bytes_archived = 0
    files_archived = 0
    tapes_used: list[str] = []

    def finalize_current_mount() -> None:
        nonlocal \
            current_barcode, \
            current_handle, \
            current_slot_id, \
            current_drive_id, \
            pending_instance_ids
        if current_handle is None or current_barcode is None or current_drive_id is None:
            return
        ltfs.unmount(current_handle)
        for instance_id in pending_instance_ids:
            catalog.mark_instance_archived(instance_id, checksum_verified=True)
        pending_instance_ids = []
        if current_slot_id is not None:
            execute_tape_request(
                catalog,
                library,
                ltfs,
                TapeOpRequest(
                    op_type=TapeOpType.UNLOAD,
                    barcode=current_barcode,
                    drive_id=current_drive_id,
                    slot_id=current_slot_id,
                    requested_by="archive-job",
                    job_id=job_id,
                ),
            )
        _mark_aml_drive_idle(current_barcode, current_drive_id, current_slot_id)
        cartridge = catalog.add_cartridge(current_barcode, volume_group.id)
        tape = ltfs.ensure_tape(current_barcode)
        cartridge.used_bytes = tape.used_bytes
        cartridge.capacity_bytes = tape.capacity_bytes
        cartridge.formatted = tape.formatted
        cartridge.state = "in_slot" if current_slot_id is not None else "in_drive"
        catalog.session.commit()
        current_barcode = None
        current_handle = None
        current_slot_id = None
        current_drive_id = None

    try:
        for file_path in files:
            relative = (
                file_path.name
                if request.source_path.is_file()
                else str(file_path.relative_to(request.source_path))
            )
            catalog_path = str(PurePosixPath("/") / request.volume_group_name / relative)
            tape_path = PurePosixPath(catalog_path)
            checksum = sha256sum(file_path)
            size_bytes = file_path.stat().st_size
            selected_barcode = _choose_tape(catalog, library, ltfs, volume_group.id, size_bytes)
            if selected_barcode != current_barcode:
                finalize_current_mount()
                current_drive_id, current_slot_id = _load_if_needed(
                    catalog,
                    library,
                    ltfs,
                    selected_barcode,
                    job_id,
                )
                _mark_aml_drive_busy(selected_barcode, current_drive_id)
                current_handle = ltfs.mount(selected_barcode, MountMode.READ_WRITE)
                current_barcode = selected_barcode
                tapes_used.append(selected_barcode)
            assert current_handle is not None
            ltfs.write_file(current_handle, file_path, tape_path)
            record = catalog.create_file_record(
                catalog_path,
                size_bytes,
                checksum,
                volume_group.id,
                shard_count=1,
                shard_index=None,
                block_size=None,
                shard_profile="standard",
                parent_id=None,
            )
            instance = catalog.create_file_instance(record.id, current_barcode, str(tape_path))
            stat = ltfs.stat(current_handle, tape_path)
            if stat.checksum_sha256 != checksum or stat.size_bytes != size_bytes:
                raise ChecksumMismatchError(f"Verification failed for {file_path}")
            pending_instance_ids.append(instance.id)
            bytes_archived += size_bytes
            files_archived += 1
        finalize_current_mount()
    except Exception as exc:
        if current_handle is not None:
            try:
                ltfs.unmount(current_handle)
            except Exception:
                logger.exception("failed to unmount archive handle for job %s", job_id)
        if current_barcode is not None and current_drive_id is not None:
            if current_slot_id is not None:
                try:
                    execute_tape_request(
                        catalog,
                        library,
                        ltfs,
                        TapeOpRequest(
                            op_type=TapeOpType.UNLOAD,
                            barcode=current_barcode,
                            drive_id=current_drive_id,
                            slot_id=current_slot_id,
                            requested_by="archive-job",
                            job_id=job_id,
                        ),
                    )
                except Exception:
                    logger.exception("failed to unload archive drive for job %s", job_id)
            try:
                _mark_aml_drive_idle(current_barcode, current_drive_id, current_slot_id)
            except Exception:
                logger.exception("failed to reset archive AML drive state for job %s", job_id)
        catalog.update_job_state(job_id, "failed", str(exc))
        raise

    result = ArchiveResult(
        job_id=job_id,
        files_archived=files_archived,
        bytes_archived=bytes_archived,
        tapes_used=tapes_used,
        errors=[],
    )
    catalog.update_job_state(job_id, "completed")
    logger.info("archive job completed", job_id=job_id, files_archived=files_archived)
    return result


class ArchiveService:
    def __init__(
        self,
        library: LibraryBackend,
        ltfs: LTFSBackend,
        catalog: CatalogRepository,
        queue: JobQueue,
    ) -> None:
        self.library = library
        self.ltfs = ltfs
        self.catalog = catalog
        self.queue = queue

    def enqueue(self, volume_group_name: str, source_path: Path):
        job = self.catalog.create_job(
            JobType.ARCHIVE.value,
            {"source_path": str(source_path), "volume_group": volume_group_name},
        )
        try:
            run_archive_job(
                ArchiveRequest(source_path=source_path, volume_group_name=volume_group_name),
                self.library,
                self.ltfs,
                self.catalog,
                job.id,
            )
        except Exception:
            for file_path in _iter_source_files(source_path):
                relative = (
                    file_path.name
                    if source_path.is_file()
                    else str(file_path.relative_to(source_path))
                )
                catalog_path = str(PurePosixPath("/") / volume_group_name / relative)
                self.catalog.delete_file_record_if_unarchived(catalog_path)
            raise
        refreshed = self.catalog.get_job(job.id)
        assert refreshed is not None
        return refreshed
