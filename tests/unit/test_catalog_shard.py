from __future__ import annotations

import json

from openblade.nas.catalog_shard import (
    CatalogShard,
    CatalogShardDatasetEntry,
    CatalogShardFileEntry,
    CatalogShardWriter,
)
from openblade.nas.ltfs_manifest import TapeMetadataWriter
from openblade.nas.types import NasFileState, PathMappingRecord


class FakeMetadataBackend:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = set()

    def write_bytes(self, path: str, content: bytes) -> None:
        self.files[path] = content

    def read_bytes(self, path: str) -> bytes | None:
        return self.files.get(path)


def _writer() -> tuple[FakeMetadataBackend, CatalogShardWriter]:
    backend = FakeMetadataBackend()
    return backend, CatalogShardWriter(TapeMetadataWriter(backend))


def _file_entry(**overrides: object) -> CatalogShardFileEntry:
    payload = {
        "logical_path": "/datasets/a.txt",
        "tape_path": "/ltfs/datasets/a.txt",
        "dataset_id": "dataset-1",
        "file_record_id": "file-1",
        "pool_id": "pool-1",
        "size": 10,
        "mtime": "2025-01-01T00:00:00Z",
        "checksum": "abc123",
        "file_state": "offline_on_tape",
        "policy": "critical_sequential",
        "copy_index": 1,
        "verified": True,
    }
    payload.update(overrides)
    return CatalogShardFileEntry(**payload)


def _dataset_entry(**overrides: object) -> CatalogShardDatasetEntry:
    payload = {
        "dataset_id": "dataset-1",
        "pool_id": "pool-1",
        "volume_group": "vg-1",
        "policy": "critical_sequential",
        "ingest_mode": "cache_drive",
        "file_count": 1,
        "total_bytes": 10,
        "tape_set": ["VOL001L8"],
        "shard_set": [],
    }
    payload.update(overrides)
    return CatalogShardDatasetEntry(**payload)


def _path_mapping(**overrides: object) -> PathMappingRecord:
    payload = {
        "logical_path": "/datasets/a.txt",
        "pool_id": "pool-1",
        "dataset_id": "dataset-1",
        "primary_barcode": "VOL001L8",
        "all_barcodes": ["VOL001L8"],
        "file_record_id": "file-1",
        "file_state": NasFileState.OFFLINE_ON_TAPE,
        "size": 10,
        "checksum": "abc123",
    }
    payload.update(overrides)
    return PathMappingRecord(**payload)


def test_catalog_shard_schema_alias_uses_schema_field() -> None:
    shard = CatalogShard(barcode="VOL001L8", openblade_tape_id="tape-1")

    assert shard.model_dump(by_alias=True)["schema"] == "openblade.catalog_shard.v1"


def test_catalog_shard_sync_totals_auto_computes_counts() -> None:
    shard = CatalogShard(
        barcode="VOL001L8",
        openblade_tape_id="tape-1",
        files=[_file_entry(size=10), _file_entry(logical_path="/datasets/b.txt", file_record_id="file-2", size=20)],
    )

    assert shard.file_count == 2
    assert shard.total_bytes == 30


def test_build_shard_returns_catalog_shard_with_expected_metadata() -> None:
    _, writer = _writer()

    shard = writer.build_shard("VOL001L8", "tape-1", "vg-1", [_file_entry()], [_dataset_entry()])

    assert shard.barcode == "VOL001L8"
    assert shard.openblade_tape_id == "tape-1"
    assert shard.file_count == 1


def test_write_shard_stores_catalog_json() -> None:
    backend, writer = _writer()
    shard = writer.build_shard("VOL001L8", "tape-1", "vg-1", [_file_entry()], [_dataset_entry()])

    writer.write_shard("VOL001L8", shard)

    assert "/.openblade/catalog-shard.json" in backend.files


def test_write_shard_stores_catalog_checksum_file() -> None:
    backend, writer = _writer()
    shard = writer.build_shard("VOL001L8", "tape-1", "vg-1", [_file_entry()], [_dataset_entry()])

    writer.write_shard("VOL001L8", shard)

    assert "/.openblade/catalog-shard.sha256" in backend.files


def test_write_shard_returns_hex_checksum() -> None:
    _, writer = _writer()
    shard = writer.build_shard("VOL001L8", "tape-1", "vg-1", [_file_entry()], [_dataset_entry()])

    checksum = writer.write_shard("VOL001L8", shard)

    assert len(checksum) == 64
    assert all(char in "0123456789abcdef" for char in checksum)


def test_read_shard_returns_none_when_missing() -> None:
    _, writer = _writer()

    assert writer.read_shard("VOL001L8") is None


def test_read_shard_returns_none_on_corrupt_json() -> None:
    backend, writer = _writer()
    backend.files["/.openblade/catalog-shard.json"] = b"{"

    assert writer.read_shard("VOL001L8") is None


def test_read_shard_round_trips_written_shard() -> None:
    _, writer = _writer()
    expected = writer.build_shard("VOL001L8", "tape-1", "vg-1", [_file_entry()], [_dataset_entry()])
    writer.write_shard("VOL001L8", expected)

    restored = writer.read_shard("VOL001L8")

    assert restored == expected


def test_build_and_write_shard_returns_matching_checksum_tuple() -> None:
    backend, writer = _writer()

    shard, checksum = writer.build_and_write_shard(
        "VOL001L8", "tape-1", "vg-1", [_file_entry()], [_dataset_entry()]
    )

    assert shard.barcode == "VOL001L8"
    assert backend.files["/.openblade/catalog-shard.sha256"].decode("utf-8") == checksum


def test_shard_to_path_mappings_produces_expected_record_fields() -> None:
    _, writer = _writer()
    shard = writer.build_shard("VOL001L8", "tape-1", "vg-1", [_file_entry()], [_dataset_entry()])

    records = writer.shard_to_path_mappings(shard)

    assert records[0].logical_path == "/datasets/a.txt"
    assert records[0].primary_barcode == "VOL001L8"
    assert records[0].file_state is NasFileState.OFFLINE_ON_TAPE


def test_shard_to_path_mappings_coerces_file_state_string_to_enum() -> None:
    _, writer = _writer()
    shard = writer.build_shard(
        "VOL001L8",
        "tape-1",
        "vg-1",
        [_file_entry(file_state="hydrating")],
        [_dataset_entry()],
    )

    records = writer.shard_to_path_mappings(shard)

    assert records[0].file_state is NasFileState.HYDRATING


def test_shard_to_path_mappings_falls_back_for_unknown_file_state() -> None:
    _, writer = _writer()
    shard = writer.build_shard(
        "VOL001L8",
        "tape-1",
        "vg-1",
        [_file_entry(file_state="future_state")],
        [_dataset_entry()],
    )

    records = writer.shard_to_path_mappings(shard)

    assert records[0].file_state is NasFileState.OFFLINE_ON_TAPE


def test_file_entries_from_path_mappings_produces_expected_entry_fields() -> None:
    _, writer = _writer()

    entries = writer.file_entries_from_path_mappings([_path_mapping()], "VOL001L8")

    assert len(entries) == 1
    assert entries[0].logical_path == "/datasets/a.txt"
    assert entries[0].dataset_id == "dataset-1"


def test_file_entries_from_path_mappings_defaults_tape_path_to_logical_path() -> None:
    _, writer = _writer()

    entries = writer.file_entries_from_path_mappings([_path_mapping()], "VOL001L8")

    assert entries[0].tape_path == entries[0].logical_path


def test_file_entries_from_path_mappings_filters_to_requested_barcode() -> None:
    _, writer = _writer()

    entries = writer.file_entries_from_path_mappings([_path_mapping(primary_barcode="VOL999L8", all_barcodes=["VOL999L8"])], "VOL001L8")

    assert entries == []


def test_end_to_end_shard_round_trip_rebuilds_path_mappings() -> None:
    _, writer = _writer()
    shard, _ = writer.build_and_write_shard(
        "VOL001L8",
        "tape-1",
        "vg-1",
        [
            _file_entry(),
            _file_entry(
                logical_path="/datasets/b.txt",
                tape_path="/ltfs/datasets/b.txt",
                file_record_id="file-2",
                size=25,
                checksum="def456",
                file_state="online_cached",
            ),
        ],
        [_dataset_entry(file_count=2, total_bytes=35)],
    )

    restored = writer.read_shard("VOL001L8")
    assert restored is not None

    records = writer.shard_to_path_mappings(restored)

    assert [record.logical_path for record in records] == ["/datasets/a.txt", "/datasets/b.txt"]
    assert all(record.primary_barcode == "VOL001L8" for record in records)
    assert records[1].file_state is NasFileState.ONLINE_CACHED


def test_write_shard_payload_uses_schema_alias() -> None:
    backend, writer = _writer()
    shard = writer.build_shard("VOL001L8", "tape-1", "vg-1", [_file_entry()], [_dataset_entry()])

    writer.write_shard("VOL001L8", shard)

    payload = json.loads(backend.files["/.openblade/catalog-shard.json"].decode("utf-8"))
    assert payload["schema"] == "openblade.catalog_shard.v1"
