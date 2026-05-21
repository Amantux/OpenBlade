"""Virtual filesystem service for browsing logical pool namespaces.

Provides read-only directory listings and file metadata from the catalog,
plus hydration request queuing for offline tape-backed files.
"""

from __future__ import annotations

from datetime import datetime
from secrets import token_hex
from threading import RLock

import structlog

from openblade.catalog.repository import CatalogRepository
from openblade.nas.types import (
    HydrationJob,
    HydrationRequest,
    NasDataset,
    NasFileRecord,
    NasFileState,
    PathMappingRecord,
    VirtualDirectoryListing,
    VirtualFileEntry,
    VirtualFileStatus,
)

logger = structlog.get_logger(__name__)

_TERMINAL_HYDRATION_STATES = {"completed", "failed", "cancelled"}
_DIRECTORY_STATUS = VirtualFileStatus.ONLINE_CACHED


class VirtualFilesystem:
    """Read-only virtual filesystem backed by the catalog."""

    def __init__(self, repo: CatalogRepository) -> None:
        """Initialize the virtual filesystem with a catalog repository."""
        self.repo = repo
        self._jobs: dict[str, HydrationJob] = {}
        self._lock = RLock()

    def list_directory(self, path: str) -> VirtualDirectoryListing:
        """List a virtual directory path and return its immediate children."""
        normalized = self._normalize_path(path)
        parts = self._split_path(normalized)

        if normalized in {"/", "/pools"}:
            entries = [self._build_pool_entry(pool_name) for pool_name in self._list_pool_names()]
            return VirtualDirectoryListing(path=normalized, entries=entries, total_entries=len(entries))

        if not parts or parts[0] != "pools":
            raise ValueError("invalid virtual path")

        if len(parts) == 1:
            entries = [self._build_pool_entry(pool_name) for pool_name in self._list_pool_names()]
            return VirtualDirectoryListing(path=normalized, entries=entries, total_entries=len(entries))

        pool_name = parts[1]
        if len(parts) == 2:
            entries = self._list_dataset_entries(pool_name)
            return VirtualDirectoryListing(path=normalized, entries=entries, total_entries=len(entries))

        dataset_id = parts[2]
        relative_prefix = "/".join(parts[3:])
        entries = self._list_dataset_children(pool_name, dataset_id, relative_prefix)
        return VirtualDirectoryListing(path=normalized, entries=entries, total_entries=len(entries))

    def stat_file(self, path: str) -> VirtualFileEntry:
        """Return metadata for a virtual file or directory."""
        normalized = self._normalize_path(path)
        parts = self._split_path(normalized)

        if normalized == "/":
            return self._build_directory_entry(path="/", name="/", pool="")
        if normalized == "/pools":
            return self._build_directory_entry(path="/pools", name="pools", pool="")
        if not parts or parts[0] != "pools":
            raise FileNotFoundError(normalized)
        if len(parts) == 2:
            pool_name = parts[1]
            if pool_name not in self._list_pool_names():
                raise FileNotFoundError(normalized)
            return self._build_directory_entry(path=normalized, name=pool_name, pool=pool_name)
        if len(parts) == 3:
            dataset = self._find_dataset(parts[1], parts[2])
            if dataset is None:
                raise FileNotFoundError(normalized)
            return self._build_directory_entry(
                path=normalized,
                name=dataset.id,
                pool=parts[1],
                dataset_id=dataset.id,
                mtime=dataset.updated_at or dataset.created_at or self._utcnow_iso(),
            )

        pool_name = parts[1]
        dataset_id = parts[2]
        relative_path = "/".join(parts[3:])
        record = self._find_file_record(dataset_id, relative_path)
        if record is not None and self._find_dataset(pool_name, dataset_id) is not None:
            return self._build_file_entry(pool_name, dataset_id, record)
        if self._has_directory(dataset_id, relative_path) and self._find_dataset(pool_name, dataset_id) is not None:
            return self._build_directory_entry(
                path=normalized,
                name=parts[-1],
                pool=pool_name,
                dataset_id=dataset_id,
            )
        raise FileNotFoundError(normalized)

    def request_hydration(self, request: HydrationRequest) -> HydrationJob:
        """Queue a mock hydration job for offline files and return the queued job."""
        normalized_paths = [self._normalize_path(path) for path in request.paths]
        required_tapes: set[str] = set()
        missing_tapes: set[str] = set()

        for path in normalized_paths:
            entry = self.stat_file(path)
            mapping = self._get_path_mapping_for_entry(entry)
            barcodes = self._barcodes_for_entry(entry, mapping)
            required_tapes.update(barcodes)
            if entry.status is VirtualFileStatus.MISSING_TAPE or not barcodes:
                if barcodes:
                    missing_tapes.update(barcodes)
                elif entry.tape_barcode:
                    missing_tapes.add(entry.tape_barcode)
            if entry.status is VirtualFileStatus.OFFLINE_ON_TAPE:
                self._mark_entry_hydrating(entry)

        now = self._utcnow_iso()
        job = HydrationJob(
            job_id=token_hex(8),
            status="queued",
            paths=normalized_paths,
            destination=request.destination,
            required_tapes=sorted(required_tapes),
            missing_tapes=sorted(missing_tapes),
            total_files=len(normalized_paths),
            completed_files=0,
            failed_files=0,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._jobs[job.job_id] = job
        logger.info(
            "virtual_fs.hydration_queued",
            job_id=job.job_id,
            paths=job.paths,
            required_tapes=job.required_tapes,
            missing_tapes=job.missing_tapes,
        )
        return job.model_copy(deep=True)

    def get_hydration_job(self, job_id: str) -> HydrationJob:
        """Return a hydration job by id."""
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job.model_copy(deep=True)

    def list_hydration_jobs(self) -> list[HydrationJob]:
        """Return all hydration jobs in creation order."""
        with self._lock:
            jobs = list(self._jobs.values())
        jobs.sort(key=lambda job: (job.created_at, job.job_id))
        return [job.model_copy(deep=True) for job in jobs]

    def cancel_hydration_job(self, job_id: str) -> HydrationJob:
        """Cancel a queued or running hydration job."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            if job.status in _TERMINAL_HYDRATION_STATES:
                raise ValueError("hydration job already completed")
            updated = job.model_copy(update={"status": "cancelled", "updated_at": self._utcnow_iso()})
            self._jobs[job_id] = updated
        logger.info("virtual_fs.hydration_cancelled", job_id=job_id)
        return updated.model_copy(deep=True)

    def _list_pool_names(self) -> list[str]:
        pool_names = {
            pool_name
            for dataset in self._list_datasets()
            if (pool_name := self._dataset_pool_name(dataset))
        }
        return sorted(pool_names)

    def _list_dataset_entries(self, pool_name: str) -> list[VirtualFileEntry]:
        entries = [
            self._build_directory_entry(
                path=f"/pools/{pool_name}/{dataset.id}",
                name=dataset.id,
                pool=pool_name,
                dataset_id=dataset.id,
                mtime=dataset.updated_at or dataset.created_at or self._utcnow_iso(),
            )
            for dataset in self._list_datasets()
            if self._dataset_pool_name(dataset) == pool_name
        ]
        entries.sort(key=lambda entry: entry.name)
        return entries

    def _list_dataset_children(
        self,
        pool_name: str,
        dataset_id: str,
        relative_prefix: str,
    ) -> list[VirtualFileEntry]:
        if self._find_dataset(pool_name, dataset_id) is None:
            return []
        prefix = relative_prefix.strip("/")
        directory_names: set[str] = set()
        entries: list[VirtualFileEntry] = []
        for record in self._list_file_records(dataset_id):
            relative_path = record.relative_path.strip("/")
            if prefix:
                if relative_path == prefix:
                    continue
                if not relative_path.startswith(f"{prefix}/"):
                    continue
                remainder = relative_path[len(prefix) + 1 :]
            else:
                remainder = relative_path
            if not remainder:
                continue
            if "/" in remainder:
                directory_name = remainder.split("/", 1)[0]
                if directory_name not in directory_names:
                    directory_names.add(directory_name)
                    child_prefix = f"{prefix}/{directory_name}" if prefix else directory_name
                    entries.append(
                        self._build_directory_entry(
                            path=f"/pools/{pool_name}/{dataset_id}/{child_prefix}",
                            name=directory_name,
                            pool=pool_name,
                            dataset_id=dataset_id,
                        )
                    )
                continue
            entries.append(self._build_file_entry(pool_name, dataset_id, record))
        entries.sort(key=lambda entry: (not entry.is_directory, entry.name))
        return entries

    def _list_datasets(self) -> list[NasDataset]:
        return [NasDataset.model_validate(row) for row in self.repo.list_nas_datasets()]

    def _list_file_records(self, dataset_id: str) -> list[NasFileRecord]:
        return [NasFileRecord.model_validate(row) for row in self.repo.list_nas_file_records(dataset_id)]

    def _find_dataset(self, pool_name: str, dataset_id: str) -> NasDataset | None:
        for dataset in self._list_datasets():
            if dataset.id == dataset_id and self._dataset_pool_name(dataset) == pool_name:
                return dataset
        return None

    def _find_file_record(self, dataset_id: str, relative_path: str) -> NasFileRecord | None:
        normalized = relative_path.strip("/")
        for record in self._list_file_records(dataset_id):
            if record.relative_path.strip("/") == normalized:
                return record
        return None

    def _has_directory(self, dataset_id: str, relative_path: str) -> bool:
        prefix = relative_path.strip("/")
        if not prefix:
            return False
        return any(
            record.relative_path.strip("/").startswith(f"{prefix}/")
            for record in self._list_file_records(dataset_id)
        )

    def _build_pool_entry(self, pool_name: str) -> VirtualFileEntry:
        return self._build_directory_entry(path=f"/pools/{pool_name}", name=pool_name, pool=pool_name)

    def _build_directory_entry(
        self,
        *,
        path: str,
        name: str,
        pool: str,
        dataset_id: str = "",
        mtime: str | None = None,
    ) -> VirtualFileEntry:
        return VirtualFileEntry(
            path=path,
            name=name,
            size_bytes=0,
            mtime=mtime or self._utcnow_iso(),
            status=_DIRECTORY_STATUS,
            is_directory=True,
            pool=pool,
            dataset_id=dataset_id,
        )

    def _build_file_entry(self, pool_name: str, dataset_id: str, record: NasFileRecord) -> VirtualFileEntry:
        relative_path = record.relative_path.strip("/")
        full_path = f"/pools/{pool_name}/{dataset_id}/{relative_path}"
        mapping = self._get_path_mapping(full_path, pool_name)
        return VirtualFileEntry(
            path=full_path,
            name=relative_path.rsplit("/", 1)[-1],
            size_bytes=record.size_bytes,
            mtime=record.mtime or record.updated_at or record.created_at or self._utcnow_iso(),
            checksum_sha256=(mapping.checksum if mapping is not None and mapping.checksum else record.checksum_sha256 or ""),
            tape_barcode=(mapping.primary_barcode if mapping is not None and mapping.primary_barcode else record.tape_barcode or ""),
            status=self._entry_status(record, mapping),
            is_directory=False,
            pool=pool_name,
            dataset_id=dataset_id,
        )

    def _dataset_pool_name(self, dataset: NasDataset) -> str:
        return (dataset.volume_group_id or dataset.pool_id or "").strip()

    def _get_path_mapping_for_entry(self, entry: VirtualFileEntry) -> PathMappingRecord | None:
        return self._get_path_mapping(entry.path, entry.pool)

    def _get_path_mapping(self, logical_path: str, pool_name: str) -> PathMappingRecord | None:
        mapping = self.repo.get_path_mapping(logical_path, pool_name)
        if mapping is not None:
            return mapping
        return self.repo.get_path_mapping(logical_path, "")

    def _entry_status(
        self,
        record: NasFileRecord,
        mapping: PathMappingRecord | None,
    ) -> VirtualFileStatus:
        if mapping is not None:
            return VirtualFileStatus(mapping.file_state.value)
        return VirtualFileStatus(record.status.value)

    def _barcodes_for_entry(
        self,
        entry: VirtualFileEntry,
        mapping: PathMappingRecord | None,
    ) -> list[str]:
        barcodes = [] if mapping is None else [barcode for barcode in mapping.all_barcodes if barcode]
        if mapping is not None and mapping.primary_barcode:
            barcodes.append(mapping.primary_barcode)
        if entry.tape_barcode:
            barcodes.append(entry.tape_barcode)
        return sorted(set(barcodes))

    def _mark_entry_hydrating(self, entry: VirtualFileEntry) -> None:
        mapping = self._get_path_mapping_for_entry(entry)
        if mapping is not None:
            self.repo.upsert_path_mapping(
                mapping.model_copy(update={"file_state": NasFileState.HYDRATING})
            )
        record = self._find_file_record(entry.dataset_id, self._relative_from_virtual_path(entry.path))
        if record is not None:
            self.repo.update_nas_file_status(record.id, NasFileState.HYDRATING.value)

    def _relative_from_virtual_path(self, path: str) -> str:
        parts = self._split_path(path)
        return "/".join(parts[3:])

    def _normalize_path(self, path: str) -> str:
        raw = str(path or "/").strip()
        if not raw:
            return "/"
        if not raw.startswith("/"):
            raw = f"/{raw}"
        segments = [segment for segment in raw.split("/") if segment]
        if any(segment == ".." for segment in segments):
            raise ValueError("invalid virtual path")
        normalized = "/" + "/".join(segments)
        return normalized if normalized != "" else "/"

    def _split_path(self, path: str) -> list[str]:
        return [segment for segment in path.split("/") if segment]

    def _utcnow_iso(self) -> str:
        return datetime.utcnow().isoformat() + "Z"
