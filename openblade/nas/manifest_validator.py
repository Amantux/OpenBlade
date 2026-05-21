from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel, Field, ValidationError

from openblade.nas.catalog_shard import CatalogShard, CatalogShardWriter
from openblade.nas.ltfs_manifest import ManifestJson, TapeMetadataWriter


class ManifestValidationResult(BaseModel):
    """Result of validating a manifest on a simulated tape."""

    barcode: str
    valid: bool
    schema_version: str = ""
    checksum_match: bool = False
    stored_checksum: str = ""
    computed_checksum: str = ""
    file_count: int = 0
    total_bytes: int = 0
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CatalogShardValidationResult(BaseModel):
    """Result of validating a catalog shard on a simulated tape."""

    barcode: str
    valid: bool
    schema_version: str = ""
    checksum_match: bool = False
    stored_checksum: str = ""
    computed_checksum: str = ""
    file_count: int = 0
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class TapeValidationSummary(BaseModel):
    """Combined validation summary for a tape."""

    barcode: str
    tape_json_present: bool = False
    manifest: ManifestValidationResult | None = None
    catalog_shard: CatalogShardValidationResult | None = None
    overall_valid: bool = False
    errors: list[str] = Field(default_factory=list)


class ManifestValidator:
    """
    Validates manifests and catalog shards on simulated LTFS tapes.
    Reads from TapeMetadataWriter; no direct backend access.
    No real hardware access.
    """

    MANIFEST_PATH = "/.openblade/manifest.json"
    MANIFEST_CHECKSUM_PATH = "/.openblade/manifest.sha256"
    CATALOG_SHARD_PATH = "/.openblade/catalog-shard.json"
    CATALOG_SHARD_CHECKSUM_PATH = "/.openblade/catalog-shard.sha256"
    MANIFEST_SCHEMA = "openblade.manifest.v1"
    CATALOG_SHARD_SCHEMA = "openblade.catalog_shard.v1"

    def __init__(self, metadata_writer: TapeMetadataWriter, shard_writer: CatalogShardWriter) -> None:
        self.writer = metadata_writer
        self.shard_writer = shard_writer

    def validate_manifest(self, barcode: str) -> ManifestValidationResult:
        """
        Validate /.openblade/manifest.json against /.openblade/manifest.sha256.
        Checks: manifest present, schema version correct, checksum match, file_count consistent.
        """
        result = ManifestValidationResult(barcode=barcode, valid=False)
        payload = self._read_json_payload(barcode, self.MANIFEST_PATH, "manifest.json", result.errors)
        if payload is None:
            return result

        result.schema_version = str(payload.get("schema", ""))
        try:
            manifest = ManifestJson.model_validate(payload)
        except ValidationError:
            result.errors.append("manifest.json failed schema validation")
            return result

        result.file_count = manifest.file_count
        result.total_bytes = manifest.total_logical_bytes
        if result.schema_version != self.MANIFEST_SCHEMA:
            result.errors.append(f"manifest schema mismatch: {result.schema_version}")

        raw_files = payload.get("files", [])
        if not isinstance(raw_files, list):
            result.errors.append("manifest files field must be a list")
        elif payload.get("file_count", len(raw_files)) != len(raw_files):
            result.errors.append("manifest file_count inconsistent with files list")

        self._apply_checksum_validation(
            barcode=barcode,
            payload=payload,
            checksum_path=self.MANIFEST_CHECKSUM_PATH,
            label="manifest",
            result=result,
        )
        result.valid = not result.errors
        return result

    def validate_catalog_shard(self, barcode: str) -> CatalogShardValidationResult:
        """
        Validate /.openblade/catalog-shard.json against /.openblade/catalog-shard.sha256.
        Checks: shard present, schema version correct, checksum match.
        """
        result = CatalogShardValidationResult(barcode=barcode, valid=False)
        payload = self._read_json_payload(
            barcode,
            self.CATALOG_SHARD_PATH,
            "catalog-shard.json",
            result.errors,
        )
        if payload is None:
            return result

        result.schema_version = str(payload.get("schema", ""))
        try:
            shard = CatalogShard.model_validate(payload)
        except ValidationError:
            result.errors.append("catalog-shard.json failed schema validation")
            return result

        result.file_count = shard.file_count
        if result.schema_version != self.CATALOG_SHARD_SCHEMA:
            result.errors.append(f"catalog shard schema mismatch: {result.schema_version}")

        self._apply_checksum_validation(
            barcode=barcode,
            payload=payload,
            checksum_path=self.CATALOG_SHARD_CHECKSUM_PATH,
            label="catalog shard",
            result=result,
        )
        result.valid = not result.errors
        return result

    def validate_tape(self, barcode: str) -> TapeValidationSummary:
        """
        Full tape validation: tape.json present, manifest valid, catalog shard valid.
        Returns TapeValidationSummary with overall_valid=True only if all checks pass.
        """
        summary = TapeValidationSummary(barcode=barcode)
        summary.tape_json_present = self.writer.read_tape_json(barcode) is not None
        if not summary.tape_json_present:
            summary.errors.append("tape.json missing or invalid")

        summary.manifest = self.validate_manifest(barcode)
        summary.catalog_shard = self.validate_catalog_shard(barcode)
        summary.overall_valid = (
            summary.tape_json_present
            and summary.manifest.valid
            and summary.catalog_shard.valid
            and not summary.errors
        )
        return summary

    def _read_json_payload(
        self,
        barcode: str,
        path: str,
        label: str,
        errors: list[str],
    ) -> dict[str, object] | None:
        content = self.writer._read_text(barcode, path)
        if content is None:
            errors.append(f"{label} missing")
            return None
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            errors.append(f"{label} is not valid JSON")
            return None
        if not isinstance(payload, dict):
            errors.append(f"{label} must contain a JSON object")
            return None
        return payload

    def _apply_checksum_validation(
        self,
        *,
        barcode: str,
        payload: dict[str, object],
        checksum_path: str,
        label: str,
        result: ManifestValidationResult | CatalogShardValidationResult,
    ) -> None:
        stored_checksum = self.writer._read_text(barcode, checksum_path)
        result.computed_checksum = self.writer.compute_json_checksum(payload)
        if stored_checksum is None:
            result.errors.append(f"{label} checksum missing")
            return
        result.stored_checksum = stored_checksum.strip()
        result.checksum_match = result.stored_checksum == result.computed_checksum
        if not result.checksum_match:
            result.errors.append(f"{label} checksum mismatch")


class VersionedManifestWriter:
    """
    Wraps TapeMetadataWriter to implement a safe two-phase write:
    1. Write to a temp path: /.openblade/versions/manifest.<timestamp>.tmp
    2. On commit: copy to /.openblade/manifest.json and /.openblade/manifest.sha256
    3. On abort: leave temp file in place (for audit), do not promote to final

    This prevents partial writes from corrupting the canonical manifest.
    """

    VERSIONS_DIR = "/.openblade/versions"
    MANIFEST_PATH = "/.openblade/manifest.json"
    MANIFEST_CHECKSUM_PATH = "/.openblade/manifest.sha256"

    def __init__(self, metadata_writer: TapeMetadataWriter) -> None:
        self.writer = metadata_writer

    def begin_write(self, barcode: str, manifest: ManifestJson) -> str:
        """
        Phase 1: Write manifest to temp path.
        Returns the temp path (e.g. /.openblade/versions/manifest.20250101T120000Z.tmp).
        """
        self.writer.ensure_openblade_dirs(barcode)
        self.writer._ensure_dir(barcode, self.VERSIONS_DIR)
        timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        temp_path = f"{self.VERSIONS_DIR}/manifest.{timestamp}.tmp"
        self.writer._write_json(barcode, temp_path, manifest.model_dump(by_alias=True))
        return temp_path

    def commit_write(self, barcode: str, temp_path: str) -> str:
        """
        Phase 2: Promote temp manifest to /.openblade/manifest.json.
        Also writes /.openblade/manifest.sha256 and a committed versioned manifest.
        Returns the final checksum.
        Raises ValueError if temp_path not found or not a valid temp path.
        """
        self._validate_temp_path(temp_path)
        content = self.writer._read_text(barcode, temp_path)
        if content is None:
            raise ValueError(f"temp manifest not found: {temp_path}")
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"temp manifest at {temp_path} contains invalid JSON") from exc
        checksum = self.writer.compute_json_checksum(payload)
        version_path = temp_path.removesuffix(".tmp") + ".json"
        self.writer.ensure_openblade_dirs(barcode)
        self.writer._ensure_dir(barcode, self.VERSIONS_DIR)
        self.writer._write_text(barcode, version_path, content)
        self.writer._write_text(barcode, self.MANIFEST_PATH, content)
        self.writer._write_text(barcode, self.MANIFEST_CHECKSUM_PATH, checksum)
        return checksum

    def abort_write(self, barcode: str, temp_path: str) -> None:
        """
        Abandon the staged temp write. Temp file remains for audit purposes.
        Raises ValueError if temp_path not found or not a valid temp path.
        """
        self._validate_temp_path(temp_path)
        if self.writer._read_bytes(barcode, temp_path) is None:
            raise ValueError(f"temp manifest not found: {temp_path}")

    def list_versions(self, barcode: str) -> list[str]:
        """
        List all versioned temp manifest paths for a tape.
        Returns paths matching /.openblade/versions/manifest.*.tmp pattern.
        """
        prefix = f"{self.VERSIONS_DIR}/manifest."
        return [
            p for p in self.writer.list_metadata_files(barcode, prefix)
            if p.endswith(".tmp")
        ]

    @staticmethod
    def _validate_temp_path(temp_path: str) -> None:
        """Enforce that temp_path is strictly under /.openblade/versions/manifest.*.tmp."""
        expected_prefix = "/.openblade/versions/manifest."
        if not temp_path.startswith(expected_prefix) or not temp_path.endswith(".tmp"):
            raise ValueError(
                f"temp_path must be under /.openblade/versions/ and end with .tmp, got: {temp_path!r}"
            )
