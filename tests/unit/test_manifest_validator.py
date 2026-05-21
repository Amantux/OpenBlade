from __future__ import annotations

import json

import pytest

from openblade.nas.catalog_shard import CatalogShardDatasetEntry, CatalogShardFileEntry, CatalogShardWriter
from openblade.nas.ltfs_manifest import ManifestFileEntry, ManifestJson, TapeJson, TapeMetadataWriter
from openblade.nas.manifest_validator import ManifestValidator, VersionedManifestWriter


class FakeMetadataBackend:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = set()

    def write_bytes(self, path: str, content: bytes) -> None:
        self.files[path] = content

    def read_bytes(self, path: str) -> bytes | None:
        return self.files.get(path)


def _services() -> tuple[FakeMetadataBackend, TapeMetadataWriter, CatalogShardWriter, ManifestValidator, VersionedManifestWriter]:
    backend = FakeMetadataBackend()
    metadata_writer = TapeMetadataWriter(backend)
    shard_writer = CatalogShardWriter(metadata_writer)
    return (
        backend,
        metadata_writer,
        shard_writer,
        ManifestValidator(metadata_writer, shard_writer),
        VersionedManifestWriter(metadata_writer),
    )


def _manifest(**overrides: object) -> ManifestJson:
    payload = {
        "barcode": "VOL001L8",
        "openblade_tape_id": "tape-1",
        "files": [
            ManifestFileEntry(
                logical_path="/datasets/a.txt",
                tape_path="/ltfs/datasets/a.txt",
                dataset_id="dataset-1",
                file_record_id="file-1",
                size=10,
                checksum="abc123",
            )
        ],
    }
    payload.update(overrides)
    return ManifestJson(**payload)


def _shard_file(**overrides: object) -> CatalogShardFileEntry:
    payload = {
        "logical_path": "/datasets/a.txt",
        "tape_path": "/ltfs/datasets/a.txt",
        "dataset_id": "dataset-1",
        "file_record_id": "file-1",
        "size": 10,
        "checksum": "abc123",
    }
    payload.update(overrides)
    return CatalogShardFileEntry(**payload)


def _shard_dataset(**overrides: object) -> CatalogShardDatasetEntry:
    payload = {
        "dataset_id": "dataset-1",
        "file_count": 1,
        "total_bytes": 10,
    }
    payload.update(overrides)
    return CatalogShardDatasetEntry(**payload)


def _write_valid_manifest(metadata_writer: TapeMetadataWriter, barcode: str = "VOL001L8") -> ManifestJson:
    manifest = _manifest(barcode=barcode)
    checksum = metadata_writer.write_manifest(barcode, manifest)
    metadata_writer.write_manifest_checksum(barcode, checksum)
    return manifest


def _write_valid_shard(shard_writer: CatalogShardWriter, barcode: str = "VOL001L8"):
    shard = shard_writer.build_shard(
        barcode,
        "tape-1",
        "vg-1",
        [_shard_file()],
        [_shard_dataset()],
    )
    shard_writer.write_shard(barcode, shard)
    return shard


def test_validate_manifest_returns_invalid_when_manifest_missing() -> None:
    _, _, _, validator, _ = _services()

    result = validator.validate_manifest("VOL001L8")

    assert result.valid is False
    assert any("manifest.json missing" in error for error in result.errors)


def test_validate_manifest_returns_invalid_when_checksum_missing() -> None:
    _, metadata_writer, _, validator, _ = _services()
    metadata_writer.write_manifest("VOL001L8", _manifest())

    result = validator.validate_manifest("VOL001L8")

    assert result.valid is False
    assert any("checksum missing" in error for error in result.errors)


def test_validate_manifest_returns_valid_when_manifest_and_checksum_match() -> None:
    _, metadata_writer, _, validator, _ = _services()
    manifest = _write_valid_manifest(metadata_writer)

    result = validator.validate_manifest(manifest.barcode)

    assert result.valid is True
    assert result.checksum_match is True


def test_validate_manifest_returns_invalid_on_checksum_mismatch() -> None:
    backend, metadata_writer, _, validator, _ = _services()
    _write_valid_manifest(metadata_writer)
    backend.files["/.openblade/manifest.sha256"] = b"deadbeef"

    result = validator.validate_manifest("VOL001L8")

    assert result.valid is False
    assert result.checksum_match is False


def test_validate_manifest_sets_schema_version_from_content() -> None:
    _, metadata_writer, _, validator, _ = _services()
    manifest = _write_valid_manifest(metadata_writer)

    result = validator.validate_manifest(manifest.barcode)

    assert result.schema_version == "openblade.manifest.v1"


def test_validate_manifest_sets_file_count_from_manifest() -> None:
    _, metadata_writer, _, validator, _ = _services()
    manifest = _manifest(
        files=[
            ManifestFileEntry(
                logical_path="/datasets/a.txt",
                tape_path="/ltfs/datasets/a.txt",
                dataset_id="dataset-1",
                file_record_id="file-1",
                size=10,
            ),
            ManifestFileEntry(
                logical_path="/datasets/b.txt",
                tape_path="/ltfs/datasets/b.txt",
                dataset_id="dataset-1",
                file_record_id="file-2",
                size=25,
            ),
        ]
    )
    checksum = metadata_writer.write_manifest("VOL001L8", manifest)
    metadata_writer.write_manifest_checksum("VOL001L8", checksum)

    result = validator.validate_manifest("VOL001L8")

    assert result.file_count == 2
    assert result.total_bytes == 35


def test_validate_catalog_shard_returns_invalid_when_json_missing() -> None:
    _, _, _, validator, _ = _services()

    result = validator.validate_catalog_shard("VOL001L8")

    assert result.valid is False
    assert any("catalog-shard.json missing" in error for error in result.errors)


def test_validate_catalog_shard_returns_valid_when_checksum_matches() -> None:
    _, _, shard_writer, validator, _ = _services()
    shard = _write_valid_shard(shard_writer)

    result = validator.validate_catalog_shard(shard.barcode)

    assert result.valid is True
    assert result.checksum_match is True


def test_validate_catalog_shard_returns_invalid_on_checksum_mismatch() -> None:
    backend, _, shard_writer, validator, _ = _services()
    _write_valid_shard(shard_writer)
    backend.files["/.openblade/catalog-shard.sha256"] = b"deadbeef"

    result = validator.validate_catalog_shard("VOL001L8")

    assert result.valid is False
    assert result.checksum_match is False


def test_validate_tape_returns_overall_invalid_when_tape_json_missing() -> None:
    _, metadata_writer, shard_writer, validator, _ = _services()
    _write_valid_manifest(metadata_writer)
    _write_valid_shard(shard_writer)

    result = validator.validate_tape("VOL001L8")

    assert result.tape_json_present is False
    assert result.overall_valid is False


def test_validate_tape_returns_overall_valid_when_all_components_valid() -> None:
    _, metadata_writer, shard_writer, validator, _ = _services()
    metadata_writer.write_tape_json(
        "VOL001L8",
        TapeJson(
            openblade_tape_id="tape-1",
            barcode="VOL001L8",
            created_at="2025-01-01T00:00:00Z",
            last_openblade_write_at="2025-01-01T00:00:00Z",
        ),
    )
    _write_valid_manifest(metadata_writer)
    _write_valid_shard(shard_writer)

    result = validator.validate_tape("VOL001L8")

    assert result.overall_valid is True
    assert result.manifest is not None and result.manifest.valid is True
    assert result.catalog_shard is not None and result.catalog_shard.valid is True


def test_validate_tape_returns_overall_invalid_when_manifest_invalid() -> None:
    _, metadata_writer, shard_writer, validator, _ = _services()
    metadata_writer.write_tape_json(
        "VOL001L8",
        TapeJson(
            openblade_tape_id="tape-1",
            barcode="VOL001L8",
            created_at="2025-01-01T00:00:00Z",
            last_openblade_write_at="2025-01-01T00:00:00Z",
        ),
    )
    metadata_writer.write_manifest("VOL001L8", _manifest())
    _write_valid_shard(shard_writer)

    result = validator.validate_tape("VOL001L8")

    assert result.catalog_shard is not None and result.catalog_shard.valid is True
    assert result.manifest is not None and result.manifest.valid is False
    assert result.overall_valid is False


def test_begin_write_stores_manifest_at_temp_path_only() -> None:
    backend, _, _, _, versioned_writer = _services()

    temp_path = versioned_writer.begin_write("VOL001L8", _manifest())

    assert temp_path in backend.files
    assert temp_path.startswith("/.openblade/versions/manifest.")
    assert temp_path.endswith(".tmp")
    assert "/.openblade/manifest.json" not in backend.files


def test_commit_write_promotes_temp_manifest_and_writes_checksum() -> None:
    backend, _, _, _, versioned_writer = _services()

    temp_path = versioned_writer.begin_write("VOL001L8", _manifest())
    checksum = versioned_writer.commit_write("VOL001L8", temp_path)

    assert backend.files["/.openblade/manifest.json"] == backend.files[temp_path]
    assert backend.files["/.openblade/manifest.sha256"].decode("utf-8") == checksum


def test_commit_write_raises_value_error_when_temp_missing() -> None:
    _, _, _, _, versioned_writer = _services()

    with pytest.raises(ValueError):
        versioned_writer.commit_write("VOL001L8", "/.openblade/versions/manifest.missing.tmp")


def test_abort_write_leaves_temp_file_and_does_not_update_manifest() -> None:
    backend, _, _, _, versioned_writer = _services()

    temp_path = versioned_writer.begin_write("VOL001L8", _manifest())
    versioned_writer.abort_write("VOL001L8", temp_path)

    assert temp_path in backend.files
    assert "/.openblade/manifest.json" not in backend.files


def test_list_versions_returns_temp_path_after_begin_write() -> None:
    _, _, _, _, versioned_writer = _services()

    temp_path = versioned_writer.begin_write("VOL001L8", _manifest())

    assert versioned_writer.list_versions("VOL001L8") == [temp_path]


def test_validate_manifest_reports_inconsistent_raw_file_count() -> None:
    backend, metadata_writer, _, validator, _ = _services()
    payload = _manifest().model_dump(by_alias=True)
    payload["file_count"] = 99
    backend.files["/.openblade/manifest.json"] = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    metadata_writer.write_manifest_checksum(
        "VOL001L8",
        metadata_writer.compute_json_checksum(payload),
    )

    result = validator.validate_manifest("VOL001L8")

    assert result.valid is False
    assert any("file_count inconsistent" in error for error in result.errors)


def test_commit_write_rejects_arbitrary_path() -> None:
    """commit_write must reject temp_path not under /.openblade/versions/ (hotfix Beta)."""
    _, _, _, _, versioned_writer = _services()
    with pytest.raises(ValueError, match="/.openblade/versions/"):
        versioned_writer.commit_write("VOL001L8", "/.openblade/manifest.json")


def test_abort_write_rejects_arbitrary_path() -> None:
    """abort_write must reject temp_path not matching the expected pattern (hotfix Beta)."""
    _, _, _, _, versioned_writer = _services()
    with pytest.raises(ValueError, match="/.openblade/versions/"):
        versioned_writer.abort_write("VOL001L8", "/.openblade/other.json")


def test_commit_write_raises_on_corrupt_temp_json() -> None:
    """commit_write must raise ValueError on corrupt staged JSON, not JSONDecodeError (hotfix Beta)."""
    backend, metadata_writer, _, _, versioned_writer = _services()
    corrupt_path = "/.openblade/versions/manifest.20250101T000000Z.tmp"
    backend.files[corrupt_path] = b"not-valid-json{{{"
    with pytest.raises(ValueError, match="invalid JSON"):
        versioned_writer.commit_write("VOL001L8", corrupt_path)


def test_list_versions_excludes_non_matching_files() -> None:
    """list_versions must filter out non-matching files under /.openblade/versions/ (hotfix Beta)."""
    backend, _, _, _, versioned_writer = _services()
    # Add matching and non-matching files
    backend.files["/.openblade/versions/manifest.20250101T000000Z.tmp"] = b"{}"
    backend.files["/.openblade/versions/other-file.json"] = b"{}"
    backend.files["/.openblade/manifest.json"] = b"{}"
    versions = versioned_writer.list_versions("VOL001L8")
    assert len(versions) == 1
    assert versions[0].endswith(".tmp")
    assert "other-file" not in versions[0]
