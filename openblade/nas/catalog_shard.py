from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from openblade.nas.ltfs_manifest import TapeMetadataWriter
from openblade.nas.types import NasFileState, PathMappingRecord


class CatalogShardFileEntry(BaseModel):
    """One file entry in a catalog shard — sufficient to rebuild PathMappingRecord."""

    logical_path: str
    tape_path: str
    dataset_id: str
    file_record_id: str
    pool_id: str = ""
    size: int = 0
    mtime: str = ""
    checksum: str = ""
    file_state: str = "offline_on_tape"
    policy: str = ""
    copy_index: int = 1
    verified: bool = False


class CatalogShardDatasetEntry(BaseModel):
    """Dataset-level summary in a catalog shard."""

    dataset_id: str
    pool_id: str = ""
    volume_group: str = ""
    policy: str = ""
    ingest_mode: str = ""
    file_count: int = 0
    total_bytes: int = 0
    tape_set: list[str] = Field(default_factory=list)
    shard_set: list[str] = Field(default_factory=list)


class CatalogShard(BaseModel):
    schema_: str = Field("openblade.catalog_shard.v1", alias="schema")
    barcode: str
    openblade_tape_id: str
    volume_group: str = ""
    generated_at: str = ""
    openblade_version: str = "0.1.0"
    datasets: list[CatalogShardDatasetEntry] = Field(default_factory=list)
    files: list[CatalogShardFileEntry] = Field(default_factory=list)
    file_count: int = 0
    total_bytes: int = 0
    rebuild_hint: str = "Use catalog-shard.json to restore the global path index for this tape."

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="after")
    def sync_totals(self) -> CatalogShard:
        self.file_count = len(self.files)
        self.total_bytes = sum(file.size for file in self.files)
        return self


class CatalogShardWriter:
    """
    Writes per-tape catalog shard to /.openblade/catalog-shard.json on a simulated tape.
    The shard contains enough information to rebuild the global PathMapping index
    from any single tape, without the central database.
    No real hardware access — all writes use TapeMetadataWriter.
    """

    SHARD_PATH = "catalog-shard.json"
    SHARD_CHECKSUM_PATH = "catalog-shard.sha256"
    SHARD_METADATA_PATH = "/.openblade/catalog-shard.json"
    SHARD_CHECKSUM_METADATA_PATH = "/.openblade/catalog-shard.sha256"

    def __init__(self, metadata_writer: TapeMetadataWriter) -> None:
        self.writer = metadata_writer

    def build_shard(
        self,
        barcode: str,
        tape_id: str,
        volume_group: str,
        files: list[CatalogShardFileEntry],
        datasets: list[CatalogShardDatasetEntry],
    ) -> CatalogShard:
        """Build a CatalogShard Pydantic object (no I/O)."""
        return CatalogShard(
            barcode=barcode,
            openblade_tape_id=tape_id,
            volume_group=volume_group,
            generated_at=datetime.utcnow().isoformat() + "Z",
            files=files,
            datasets=datasets,
        )

    def write_shard(self, barcode: str, shard: CatalogShard) -> str:
        """
        Serialize and write /.openblade/catalog-shard.json to the tape.
        Also writes /.openblade/catalog-shard.sha256.
        Returns the sha256 checksum.
        """
        self.writer.ensure_openblade_dirs(barcode)
        payload = shard.model_dump(by_alias=True)
        checksum = self.writer.compute_json_checksum(payload)
        self.writer._write_json(barcode, self.SHARD_METADATA_PATH, payload)
        self.writer._write_text(barcode, self.SHARD_CHECKSUM_METADATA_PATH, checksum)
        return checksum

    def read_shard(self, barcode: str) -> CatalogShard | None:
        """
        Read and parse /.openblade/catalog-shard.json from the tape.
        Returns None if missing or corrupt.
        """
        try:
            payload = self.writer._read_json(barcode, self.SHARD_METADATA_PATH)
            if payload is None:
                return None
            return CatalogShard.model_validate(payload)
        except (json.JSONDecodeError, ValidationError):
            return None

    def build_and_write_shard(
        self,
        barcode: str,
        tape_id: str,
        volume_group: str,
        files: list[CatalogShardFileEntry],
        datasets: list[CatalogShardDatasetEntry],
    ) -> tuple[CatalogShard, str]:
        """
        Convenience: build + write in one call.
        Returns (shard, checksum).
        """
        shard = self.build_shard(barcode, tape_id, volume_group, files, datasets)
        checksum = self.write_shard(barcode, shard)
        return shard, checksum

    def shard_to_path_mappings(self, shard: CatalogShard) -> list[PathMappingRecord]:
        """
        Convert a CatalogShard back into PathMappingRecord objects.
        Used during catalog rebuild to re-populate PathMappingService from tape data.
        """
        records: list[PathMappingRecord] = []
        for file_entry in shard.files:
            try:
                file_state = NasFileState(file_entry.file_state)
            except ValueError:
                file_state = NasFileState.OFFLINE_ON_TAPE
            records.append(
                PathMappingRecord(
                    logical_path=file_entry.logical_path,
                    pool_id=file_entry.pool_id,
                    dataset_id=file_entry.dataset_id,
                    primary_barcode=shard.barcode,
                    all_barcodes=[shard.barcode],
                    file_record_id=file_entry.file_record_id,
                    file_state=file_state,
                    restore_strategy="single_tape",
                    size=file_entry.size,
                    checksum=file_entry.checksum,
                    last_seen_at=shard.generated_at,
                )
            )
        return records

    def file_entries_from_path_mappings(
        self, records: list[PathMappingRecord], barcode: str
    ) -> list[CatalogShardFileEntry]:
        """
        Convert a list of PathMappingRecords (for a specific tape) into CatalogShardFileEntries.
        tape_path defaults to logical_path if not separately tracked.
        """
        entries: list[CatalogShardFileEntry] = []
        for record in records:
            if barcode and barcode not in {record.primary_barcode, *record.all_barcodes}:
                continue
            entries.append(
                CatalogShardFileEntry(
                    logical_path=record.logical_path,
                    tape_path=record.logical_path,
                    dataset_id=record.dataset_id,
                    file_record_id=record.file_record_id,
                    pool_id=record.pool_id,
                    size=record.size,
                    checksum=record.checksum,
                    file_state=record.file_state.value,
                )
            )
        return entries
