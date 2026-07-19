from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class TapeJson(BaseModel):
    schema_: str = Field("openblade.tape.v1", alias="schema")
    openblade_tape_id: str
    barcode: str
    ltfs_volume_uuid: str = ""
    library_type: str = "Quantum Scalar i3"
    created_at: str
    last_openblade_write_at: str
    openblade_version: str = "0.1.0"
    volume_group: str = ""
    pools: list[str] = Field(default_factory=list)
    state: str = "active"
    generation: str = "LTO-8"
    notes: str = ""

    model_config = ConfigDict(populate_by_name=True)


class ManifestFileEntry(BaseModel):
    logical_path: str
    tape_path: str
    dataset_id: str
    file_record_id: str
    size: int
    mtime: str = ""
    checksum: str = ""
    copy_index: int = 1
    policy: str = ""
    verified: bool = False


class ManifestJson(BaseModel):
    schema_: str = Field("openblade.manifest.v1", alias="schema")
    barcode: str
    openblade_tape_id: str
    volume_group: str = ""
    pools: list[str] = Field(default_factory=list)
    datasets: list[str] = Field(default_factory=list)
    tape_sets: list[str] = Field(default_factory=list)
    shard_sets: list[str] = Field(default_factory=list)
    file_count: int = 0
    total_logical_bytes: int = 0
    files: list[ManifestFileEntry] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="after")
    def sync_file_totals(self) -> ManifestJson:
        self.file_count = len(self.files)
        self.total_logical_bytes = sum(file.size for file in self.files)
        return self


class TapeSetManifest(BaseModel):
    schema_: str = Field("openblade.tape_set.v1", alias="schema")
    tape_set_id: str
    policy: str = "critical_sequential"
    dataset_id: str
    ordered_barcodes: list[str] = Field(default_factory=list)
    is_complete: bool = True
    file_count: int = 0
    total_bytes: int = 0
    manifest_checksum: str = ""

    model_config = ConfigDict(populate_by_name=True)


class ShardSetManifest(BaseModel):
    schema_: str = Field("openblade.shard_set.v1", alias="schema")
    shard_set_id: str
    policy: str = "noncritical_sharded"
    dataset_id: str
    barcodes: list[str] = Field(default_factory=list)
    shard_strategy: str = "round_robin"
    global_manifest_checksum: str = ""

    model_config = ConfigDict(populate_by_name=True)


class ChecksumEntry(BaseModel):
    checksum: str
    path: str


class TapeMetadataWriter:
    """
    Writes /.openblade/ metadata to a simulated LTFS tape.
    No real hardware access. All metadata I/O goes through injected backend helpers.
    """

    METADATA_ROOT = "/.openblade"
    RESERVED_DIRS = (
        METADATA_ROOT,
        f"{METADATA_ROOT}/datasets",
        f"{METADATA_ROOT}/tape-sets",
        f"{METADATA_ROOT}/shard-sets",
    )

    def __init__(self, backend: Any) -> None:
        self.backend = backend
        self._backend_write = backend.write_bytes
        self._backend_read = backend.read_bytes

    def write_tape_json(self, barcode: str, tape_json: TapeJson) -> None:
        """Serialize and write /.openblade/tape.json to the simulated tape."""
        self.ensure_openblade_dirs(barcode)
        self._write_json(barcode, self._metadata_path("tape.json"), tape_json.model_dump(by_alias=True))

    def write_manifest(self, barcode: str, manifest: ManifestJson) -> str:
        """Write /.openblade/manifest.json and return its sha256 checksum."""
        self.ensure_openblade_dirs(barcode)
        payload = manifest.model_dump(by_alias=True)
        checksum = self.compute_json_checksum(payload)
        self._write_json(barcode, self._metadata_path("manifest.json"), payload)
        return checksum

    def write_manifest_checksum(self, barcode: str, checksum: str) -> None:
        """Write /.openblade/manifest.sha256 for tamper-detection."""
        self.ensure_openblade_dirs(barcode)
        self._write_text(barcode, self._metadata_path("manifest.sha256"), checksum)

    def write_checksums_file(self, barcode: str, entries: list[ChecksumEntry]) -> None:
        """Write /.openblade/checksums.sha256 in '<sha256>  <path>' format (two spaces)."""
        self.ensure_openblade_dirs(barcode)
        lines = [f"{entry.checksum}  {entry.path}" for entry in entries]
        body = "\n".join(lines)
        if body:
            body += "\n"
        self._write_text(barcode, self._metadata_path("checksums.sha256"), body)

    def write_dataset_manifest(self, barcode: str, dataset_id: str, data: dict[str, Any]) -> None:
        """Write /.openblade/datasets/<dataset_id>.json to the simulated tape."""
        self.ensure_openblade_dirs(barcode)
        self._write_json(barcode, self._metadata_path("datasets", f"{self._safe_name(dataset_id)}.json"), data)

    def write_tape_set_manifest(self, barcode: str, tape_set: TapeSetManifest) -> None:
        """Write /.openblade/tape-sets/<tape_set_id>.json to the simulated tape."""
        self.ensure_openblade_dirs(barcode)
        self._write_json(
            barcode,
            self._metadata_path("tape-sets", f"{self._safe_name(tape_set.tape_set_id)}.json"),
            tape_set.model_dump(by_alias=True),
        )

    def write_shard_set_manifest(self, barcode: str, shard_set: ShardSetManifest) -> None:
        """Write /.openblade/shard-sets/<shard_set_id>.json to the simulated tape."""
        self.ensure_openblade_dirs(barcode)
        self._write_json(
            barcode,
            self._metadata_path("shard-sets", f"{self._safe_name(shard_set.shard_set_id)}.json"),
            shard_set.model_dump(by_alias=True),
        )

    def ensure_openblade_dirs(self, barcode: str) -> None:
        """Ensure all reserved /.openblade/ subdirectories exist in the backend."""
        for directory in self.RESERVED_DIRS:
            self._ensure_dir(barcode, directory)

    def compute_json_checksum(self, data: dict[str, Any]) -> str:
        """Return sha256 of canonicalized JSON (sorted keys, no whitespace) — deterministic."""
        return hashlib.sha256(
            json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def read_tape_json(self, barcode: str) -> TapeJson | None:
        """Read and deserialize /.openblade/tape.json; returns None on missing or corrupt data."""
        try:
            payload = self._read_json(barcode, self._metadata_path("tape.json"))
            if payload is None:
                return None
            return TapeJson.model_validate(payload)
        except (json.JSONDecodeError, ValidationError):
            return None

    def read_manifest(self, barcode: str) -> ManifestJson | None:
        """Read and deserialize /.openblade/manifest.json; returns None on missing or corrupt data."""
        try:
            payload = self._read_json(barcode, self._metadata_path("manifest.json"))
            if payload is None:
                return None
            return ManifestJson.model_validate(payload)
        except (json.JSONDecodeError, ValidationError):
            return None

    def initialize_tape(
        self,
        barcode: str,
        tape_id: str,
        volume_group: str = "",
        pools: list[str] | None = None,
    ) -> None:
        """
        Full tape initialization: write tape.json, manifest.json, manifest.sha256,
        checksums.sha256, and ensure all reserved /.openblade/ subdirectories.
        """
        normalized_pools = list(pools or [])
        created_at = self._utcnow_iso()
        self.ensure_openblade_dirs(barcode)
        self.write_tape_json(
            barcode,
            TapeJson(
                openblade_tape_id=tape_id,
                barcode=barcode,
                created_at=created_at,
                last_openblade_write_at=created_at,
                volume_group=volume_group,
                pools=normalized_pools,
            ),
        )
        manifest = ManifestJson(
            barcode=barcode,
            openblade_tape_id=tape_id,
            volume_group=volume_group,
            pools=normalized_pools,
        )
        checksum = self.write_manifest(barcode, manifest)
        self.write_manifest_checksum(barcode, checksum)
        self.write_checksums_file(barcode, [])

    def _write_json(self, barcode: str, path: str, payload: dict[str, Any]) -> None:
        self._write_text(
            barcode,
            path,
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
        )

    def _read_json(self, barcode: str, path: str) -> dict[str, Any] | None:
        content = self._read_text(barcode, path)
        if content is None:
            return None
        return json.loads(content)

    def _write_text(self, barcode: str, path: str, content: str) -> None:
        self._write_bytes(barcode, path, content.encode("utf-8"))

    def _read_text(self, barcode: str, path: str) -> str | None:
        content = self._read_bytes(barcode, path)
        if content is None:
            return None
        return content.decode("utf-8")

    def _write_bytes(self, barcode: str, path: str, content: bytes) -> None:
        try:
            self._backend_write(barcode, path, content)
        except TypeError:
            self._backend_write(path, content)

    def _read_bytes(self, barcode: str, path: str) -> bytes | None:
        try:
            return self._backend_read(barcode, path)
        except TypeError:
            return self._backend_read(path)

    def list_metadata_files(self, barcode: str, prefix: str) -> list[str]:
        """Return all /.openblade/ paths whose key starts with the given prefix."""
        files: dict[str, bytes] = {}
        backend_files = getattr(self.backend, "files", None)
        if isinstance(backend_files, dict):
            files = backend_files
        elif hasattr(self.backend, "ensure_tape"):
            # MockLTFSBackend per-tape storage
            files = getattr(self.backend.ensure_tape(barcode), "files", {})
        return sorted(k for k in files if k.startswith(prefix))

    def _ensure_dir(self, barcode: str, directory: str) -> None:
        dirs = getattr(self.backend, "dirs", None)
        if isinstance(dirs, set):
            dirs.add(directory)
            return
        marker_path = f"{directory.rstrip('/')}/"
        if self._read_bytes(barcode, marker_path) is None:
            self._write_bytes(barcode, marker_path, b"")

    def _metadata_path(self, *parts: str) -> str:
        path = PurePosixPath(self.METADATA_ROOT)
        for part in parts:
            path /= self._safe_name(part)
        normalized = str(path)
        if normalized != self.METADATA_ROOT and not normalized.startswith(f"{self.METADATA_ROOT}/"):
            raise ValueError("metadata path must stay under /.openblade")
        return normalized

    @staticmethod
    def _safe_name(value: str) -> str:
        name = str(value).strip().strip("/")
        if not name or name in {".", ".."} or "/" in name:
            raise ValueError(f"invalid metadata name: {value!r}")
        return name

    @staticmethod
    def _utcnow_iso() -> str:
        return datetime.utcnow().isoformat() + "Z"
