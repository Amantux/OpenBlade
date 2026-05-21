"""Path mapping service for NAS logical-path lookups."""

from __future__ import annotations

from openblade.catalog.repository import CatalogRepository
from openblade.nas.types import (
    NasFileState,
    PathLookupResult,
    PathMappingBulkUpsertRequest,
    PathMappingRecord,
    PathMappingSearchRequest,
)


class PathMappingService:
    """
    Resolves logical paths to tape locations and manages the global path-to-tape index.
    All writes go through this service — no direct repo access from routes.
    """

    def __init__(self, repo: CatalogRepository) -> None:
        self.repo = repo

    def record_file(self, record: PathMappingRecord) -> PathMappingRecord:
        """Upsert a single path mapping (called during archive)."""
        return self.repo.upsert_path_mapping(record)

    def bulk_record_files(self, req: PathMappingBulkUpsertRequest) -> int:
        """Bulk upsert path mappings (called during manifest import)."""
        entries = req.entries
        if not req.overwrite_existing:
            entries = [
                entry
                for entry in req.entries
                if self.repo.get_path_mapping(entry.logical_path, entry.pool_id) is None
            ]
        return self.repo.bulk_upsert_path_mappings(entries)

    def lookup(self, logical_path: str, pool_id: str = "") -> PathLookupResult:
        """
        Resolve a logical path to its tape location.
        Returns PathLookupResult with found=False if not in index.
        Includes warnings for: missing_tape state, multiple barcodes without restore_strategy.
        """
        record = self.repo.get_path_mapping(logical_path, pool_id)
        if record is None:
            return PathLookupResult(logical_path=logical_path, found=False, pool_id=pool_id)

        warnings: list[str] = []
        if record.file_state is NasFileState.MISSING_TAPE:
            warnings.append("missing_tape")
        if len(record.all_barcodes) > 1 and not record.restore_strategy.strip():
            warnings.append("multiple_barcodes_without_restore_strategy")

        return PathLookupResult(
            logical_path=record.logical_path,
            found=True,
            pool_id=record.pool_id,
            dataset_id=record.dataset_id,
            primary_barcode=record.primary_barcode,
            all_barcodes=record.all_barcodes,
            file_state=record.file_state,
            restore_strategy=record.restore_strategy,
            size=record.size,
            checksum=record.checksum,
            warnings=warnings,
        )

    def search(self, req: PathMappingSearchRequest) -> list[PathMappingRecord]:
        """Search path mappings by prefix, pool, dataset, barcode, or state."""
        return self.repo.search_path_mappings(req)

    def update_file_state(self, logical_path: str, pool_id: str, new_state: NasFileState) -> bool:
        """
        Update file_state for a path mapping (e.g., offline_on_tape → hydrating → online_cached).
        Returns True if updated, False if not found.
        """
        record = self.repo.get_path_mapping(logical_path, pool_id)
        if record is None:
            return False
        self.repo.upsert_path_mapping(record.model_copy(update={"file_state": new_state}))
        return True

    def remove(self, logical_path: str, pool_id: str = "") -> bool:
        """Remove a path mapping. Returns True if removed."""
        return self.repo.delete_path_mapping(logical_path, pool_id)

    def list_tapes_for_pool(self, pool_id: str) -> list[str]:
        """Return distinct barcodes referenced by all mappings for a pool."""
        tapes: set[str] = set()
        for record in self.search(PathMappingSearchRequest(pool_id=pool_id, limit=10_000)):
            if record.primary_barcode:
                tapes.add(record.primary_barcode)
            tapes.update(barcode for barcode in record.all_barcodes if barcode)
        return sorted(tapes)

    def get_stats(self, pool_id: str = "", dataset_id: str = "") -> dict:
        """
        Return stats dict: total_files, total_bytes, by_state (dict[state, count]),
        tape_count (distinct barcodes), pool_id, dataset_id.
        """
        records = self.search(
            PathMappingSearchRequest(pool_id=pool_id, dataset_id=dataset_id, limit=10_000)
        )
        by_state: dict[str, int] = {}
        tapes: set[str] = set()
        total_bytes = 0
        for record in records:
            state = record.file_state.value
            by_state[state] = by_state.get(state, 0) + 1
            total_bytes += record.size
            if record.primary_barcode:
                tapes.add(record.primary_barcode)
            tapes.update(barcode for barcode in record.all_barcodes if barcode)
        return {
            "total_files": self.repo.count_path_mappings(pool_id=pool_id, dataset_id=dataset_id),
            "total_bytes": total_bytes,
            "by_state": by_state,
            "tape_count": len(tapes),
            "pool_id": pool_id,
            "dataset_id": dataset_id,
        }
