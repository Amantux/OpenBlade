from __future__ import annotations

import json

from openblade.nas.ltfs_manifest import (
    ChecksumEntry,
    ManifestFileEntry,
    ManifestJson,
    ShardSetManifest,
    TapeJson,
    TapeMetadataWriter,
    TapeSetManifest,
)
from openblade.simulator.library import MockLibraryBackend
from openblade.simulator.ltfs_volume import MockLTFSBackend


class FakeMetadataBackend:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = set()

    def write_bytes(self, path: str, content: bytes) -> None:
        self.files[path] = content

    def read_bytes(self, path: str) -> bytes | None:
        return self.files.get(path)


def _writer() -> tuple[FakeMetadataBackend, TapeMetadataWriter]:
    backend = FakeMetadataBackend()
    return backend, TapeMetadataWriter(backend)


def test_tape_json_round_trip_preserves_fields() -> None:
    tape_json = TapeJson(
        openblade_tape_id="tape-1",
        barcode="VOL001L8",
        ltfs_volume_uuid="uuid-1",
        created_at="2025-01-01T00:00:00Z",
        last_openblade_write_at="2025-01-01T00:01:00Z",
        volume_group="vg-a",
        pools=["pool-a", "pool-b"],
        notes="seeded",
    )

    restored = TapeJson.model_validate(tape_json.model_dump(by_alias=True))

    assert restored == tape_json
    assert restored.model_dump(by_alias=True)["schema"] == "openblade.tape.v1"


def test_manifest_json_syncs_file_count_and_total_bytes() -> None:
    manifest = ManifestJson(
        barcode="VOL001L8",
        openblade_tape_id="tape-1",
        files=[
            ManifestFileEntry(
                logical_path="a.txt",
                tape_path="/dataset/a.txt",
                dataset_id="dataset-1",
                file_record_id="file-1",
                size=10,
            ),
            ManifestFileEntry(
                logical_path="b.txt",
                tape_path="/dataset/b.txt",
                dataset_id="dataset-1",
                file_record_id="file-2",
                size=20,
            ),
        ],
    )

    assert manifest.file_count == 2
    assert manifest.total_logical_bytes == 30


def test_write_tape_json_stores_expected_path() -> None:
    backend, writer = _writer()

    writer.write_tape_json(
        "VOL001L8",
        TapeJson(
            openblade_tape_id="tape-1",
            barcode="VOL001L8",
            created_at="2025-01-01T00:00:00Z",
            last_openblade_write_at="2025-01-01T00:00:00Z",
        ),
    )

    assert "/.openblade/tape.json" in backend.files


def test_write_manifest_returns_checksum_and_stores_file() -> None:
    backend, writer = _writer()
    manifest = ManifestJson(barcode="VOL001L8", openblade_tape_id="tape-1")

    checksum = writer.write_manifest("VOL001L8", manifest)

    expected = writer.compute_json_checksum(manifest.model_dump(by_alias=True))
    assert checksum == expected
    assert "/.openblade/manifest.json" in backend.files


def test_write_manifest_checksum_stores_checksum_file() -> None:
    backend, writer = _writer()

    writer.write_manifest_checksum("VOL001L8", "abc123")

    assert backend.files["/.openblade/manifest.sha256"] == b"abc123"


def test_write_checksums_file_uses_standard_format() -> None:
    backend, writer = _writer()

    writer.write_checksums_file(
        "VOL001L8",
        [
            ChecksumEntry(checksum="aaa", path="/.openblade/manifest.json"),
            ChecksumEntry(checksum="bbb", path="/.openblade/tape.json"),
        ],
    )

    assert backend.files["/.openblade/checksums.sha256"].decode("utf-8") == (
        "aaa  /.openblade/manifest.json\n"
        "bbb  /.openblade/tape.json\n"
    )


def test_write_dataset_manifest_stores_dataset_json() -> None:
    backend, writer = _writer()

    writer.write_dataset_manifest("VOL001L8", "dataset-1", {"dataset_id": "dataset-1"})

    assert "/.openblade/datasets/dataset-1.json" in backend.files


def test_write_tape_set_manifest_stores_tape_set_json() -> None:
    backend, writer = _writer()

    writer.write_tape_set_manifest(
        "VOL001L8",
        TapeSetManifest(tape_set_id="set-1", dataset_id="dataset-1", ordered_barcodes=["VOL001L8"]),
    )

    assert "/.openblade/tape-sets/set-1.json" in backend.files


def test_write_shard_set_manifest_stores_shard_set_json() -> None:
    backend, writer = _writer()

    writer.write_shard_set_manifest(
        "VOL001L8",
        ShardSetManifest(shard_set_id="shard-1", dataset_id="dataset-1", barcodes=["VOL001L8"]),
    )

    assert "/.openblade/shard-sets/shard-1.json" in backend.files


def test_initialize_tape_creates_reserved_dirs_and_core_metadata() -> None:
    backend, writer = _writer()

    writer.initialize_tape("VOL001L8", "tape-1", volume_group="vg-a", pools=["pool-a"])

    assert backend.dirs == {
        "/.openblade",
        "/.openblade/datasets",
        "/.openblade/tape-sets",
        "/.openblade/shard-sets",
    }
    assert "/.openblade/tape.json" in backend.files
    assert "/.openblade/manifest.json" in backend.files
    assert "/.openblade/checksums.sha256" in backend.files


def test_read_tape_json_round_trips_written_payload() -> None:
    _, writer = _writer()
    expected = TapeJson(
        openblade_tape_id="tape-1",
        barcode="VOL001L8",
        created_at="2025-01-01T00:00:00Z",
        last_openblade_write_at="2025-01-01T00:00:01Z",
    )
    writer.write_tape_json("VOL001L8", expected)

    restored = writer.read_tape_json("VOL001L8")

    assert restored == expected


def test_read_manifest_round_trips_written_payload() -> None:
    _, writer = _writer()
    expected = ManifestJson(
        barcode="VOL001L8",
        openblade_tape_id="tape-1",
        files=[
            ManifestFileEntry(
                logical_path="a.txt",
                tape_path="/dataset/a.txt",
                dataset_id="dataset-1",
                file_record_id="file-1",
                size=10,
                checksum="abc",
            )
        ],
    )
    writer.write_manifest("VOL001L8", expected)

    restored = writer.read_manifest("VOL001L8")

    assert restored == expected


def test_read_manifest_returns_none_when_missing() -> None:
    _, writer = _writer()

    assert writer.read_manifest("VOL001L8") is None


def test_compute_json_checksum_is_deterministic_and_order_independent() -> None:
    _, writer = _writer()

    first = writer.compute_json_checksum({"b": 2, "a": 1})
    second = writer.compute_json_checksum({"a": 1, "b": 2})

    assert first == second


def test_written_json_is_parseable_with_alias_fields() -> None:
    backend, writer = _writer()

    writer.write_tape_set_manifest(
        "VOL001L8",
        TapeSetManifest(tape_set_id="set-1", dataset_id="dataset-1"),
    )

    payload = json.loads(backend.files["/.openblade/tape-sets/set-1.json"].decode("utf-8"))
    assert payload["schema"] == "openblade.tape_set.v1"


def test_mock_ltfs_backend_supports_metadata_write_and_read() -> None:
    library = MockLibraryBackend(num_slots=1, num_drives=1)
    library.add_cartridge(1, "VOL001L8")
    backend = MockLTFSBackend(library)

    backend.write_bytes("VOL001L8", "/.openblade/tape.json", b"{}")

    assert backend.read_bytes("VOL001L8", "/.openblade/tape.json") == b"{}"
    assert backend.read_bytes("/.openblade/tape.json") == b"{}"


def test_initialize_tape_writes_manifest_sha256() -> None:
    """initialize_tape must persist manifest.sha256 for tamper-detection (hotfix Alpha)."""
    backend, writer = _writer()
    writer.initialize_tape("VOL001L8", tape_id="tape-1", volume_group="vg-a")
    assert "/.openblade/manifest.sha256" in backend.files
    stored_checksum = backend.files["/.openblade/manifest.sha256"].decode()
    # checksum must be a 64-char hex string
    assert len(stored_checksum) == 64
    assert all(c in "0123456789abcdef" for c in stored_checksum)


def test_read_tape_json_returns_none_on_corrupt_data() -> None:
    """read_tape_json must return None on corrupt JSON, not raise (hotfix Beta)."""
    backend, writer = _writer()
    backend.files["/.openblade/tape.json"] = b"not-valid-json{{{"
    assert writer.read_tape_json("VOL001L8") is None


def test_read_manifest_returns_none_on_corrupt_data() -> None:
    """read_manifest must return None on corrupt JSON, not raise (hotfix Beta)."""
    backend, writer = _writer()
    backend.files["/.openblade/manifest.json"] = b'{"broken":'
    assert writer.read_manifest("VOL001L8") is None


def test_metadata_path_rejects_traversal_attempt() -> None:
    """_metadata_path must reject path traversal outside /.openblade/ (hotfix Alpha)."""
    _, writer = _writer()
    import pytest
    with pytest.raises(ValueError):
        writer._metadata_path("../etc/passwd")
