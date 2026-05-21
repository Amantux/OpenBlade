"""Upload and download routes for File Station workflows."""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from pathlib import Path

# UUID4 pattern — only format accepted as file_id in path parameters
_UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _validate_file_id(file_id: str) -> str:
    """Reject anything that is not a valid UUID4. Prevents path traversal."""
    if not _UUID4_RE.match(file_id):
        raise HTTPException(status_code=400, detail="Invalid file_id format")
    return file_id


def _safe_resolve(base: Path, file_id: str) -> Path:
    """Resolve path and confirm it stays within base dir."""
    resolved = (base / file_id).resolve()
    if not str(resolved).startswith(str(base.resolve())):
        raise HTTPException(status_code=400, detail="Invalid file_id format")
    return resolved

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from openblade.api.routes_aml_auth import require_auth
from openblade.bootstrap import get_catalog
from openblade.catalog.repository import CatalogRepository
from openblade.nas.types import DatasetStatus, IngestMode, NasFileRecord, NasFileState

router = APIRouter(prefix="/api", tags=["upload-download"])

_STAGING_DATASET_PREFIX = "file-station-staging"


class UploadResponse(BaseModel):
    file_id: str
    filename: str
    size_bytes: int
    checksum_sha256: str
    pool_id: str | None
    status: str


class FileChecksumResponse(BaseModel):
    file_id: str
    checksum_sha256: str


class PoolFileEntry(BaseModel):
    file_id: str
    filename: str
    size_bytes: int
    checksum_sha256: str | None = None
    pool_id: str | None
    status: str
    created_at: str | None = None


class PoolFileListResponse(BaseModel):
    pool_id: str
    files: list[PoolFileEntry]


class DeleteFileResponse(BaseModel):
    deleted: bool


def _staging_dir() -> Path:
    return Path(os.environ.get("OPENBLADE_STAGING_DIR", "/tmp/openblade-staging"))


def _restore_dir() -> Path:
    return Path(os.environ.get("OPENBLADE_RESTORE_DIR", "/tmp/openblade-restore"))


def _ensure_dirs() -> None:
    _staging_dir().mkdir(parents=True, exist_ok=True)
    _restore_dir().mkdir(parents=True, exist_ok=True)


def _dataset_id(pool_id: str) -> str:
    return f"{_STAGING_DATASET_PREFIX}-{pool_id}"


def _dataset_name(pool_id: str) -> str:
    return f"Staging Inbox {pool_id}"


def _sanitize_filename(filename: str | None) -> str:
    name = Path(filename or "unknown").name
    return name or "unknown"


def _ensure_pool_dataset(repo: CatalogRepository, pool_id: str) -> dict[str, object]:
    dataset = repo.get_nas_dataset(_dataset_id(pool_id))
    if dataset is not None:
        return dataset
    return repo.upsert_nas_dataset(
        {
            "id": _dataset_id(pool_id),
            "pool_id": pool_id,
            "name": _dataset_name(pool_id),
            "source_path": str(_staging_dir()),
            "ingest_mode": IngestMode.CACHE_DRIVE.value,
            "file_count": 0,
            "total_bytes": 0,
            "status": DatasetStatus.PENDING.value,
        }
    )


def _sync_dataset_summary(repo: CatalogRepository, dataset: dict[str, object]) -> None:
    dataset_id = str(dataset["id"])
    records = repo.list_nas_file_records(dataset_id)
    repo.upsert_nas_dataset(
        {
            **dataset,
            "file_count": len(records),
            "total_bytes": sum(int(record.get("size_bytes") or 0) for record in records),
            "updated_at": None,
        }
    )


def _present_status(record: dict[str, object]) -> str:
    raw_status = str(record.get("status") or "").lower()
    if raw_status == NasFileState.FAILED.value:
        return "failed"
    if raw_status == NasFileState.HYDRATING.value:
        return "hydrating"
    if raw_status in {NasFileState.OFFLINE_ON_TAPE.value, "archived"}:
        return "archived"
    return "pending_archive"


def _serialize_record(record: dict[str, object]) -> PoolFileEntry:
    return PoolFileEntry(
        file_id=str(record["id"]),
        filename=_sanitize_filename(record.get("relative_path") if isinstance(record.get("relative_path"), str) else None),
        size_bytes=int(record.get("size_bytes") or 0),
        checksum_sha256=record.get("checksum_sha256") if isinstance(record.get("checksum_sha256"), str) else None,
        pool_id=record.get("pool_id") if isinstance(record.get("pool_id"), str) else None,
        status=_present_status(record),
        created_at=record.get("created_at") if isinstance(record.get("created_at"), str) else None,
    )


def _resolve_file_path(file_id: str, record: dict[str, object] | None = None) -> Path | None:
    # Always resolve through safe_resolve to prevent traversal
    candidates: list[Path] = [
        _safe_resolve(_staging_dir(), file_id),
        _safe_resolve(_restore_dir(), file_id),
    ]
    if record is not None:
        for key in ("cache_path", "source_path"):
            value = record.get(key)
            if isinstance(value, str) and value:
                candidates.append(Path(value))
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


@router.post("/pools/{pool_id}/upload", response_model=UploadResponse, dependencies=[Depends(require_auth)])
async def upload_file_to_pool(
    pool_id: str,
    file: UploadFile = File(...),
    expected_checksum: str | None = Form(default=None),
    repo: CatalogRepository = Depends(get_catalog),
) -> UploadResponse:
    """Upload a file into the staging area for a NAS pool."""
    _ensure_dirs()
    file_id = str(uuid.uuid4())
    filename = _sanitize_filename(file.filename)
    destination = _staging_dir() / file_id
    sha256 = hashlib.sha256()
    size_bytes = 0

    try:
        with destination.open("wb") as handle:
            while chunk := await file.read(65_536):
                handle.write(chunk)
                sha256.update(chunk)
                size_bytes += len(chunk)
    finally:
        await file.close()

    checksum = sha256.hexdigest()
    if expected_checksum and expected_checksum.lower() != checksum:
        destination.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail=f"Checksum mismatch: expected {expected_checksum}, got {checksum}",
        )

    dataset = _ensure_pool_dataset(repo, pool_id)
    repo.upsert_nas_file_record(
        NasFileRecord(
            id=file_id,
            dataset_id=str(dataset["id"]),
            pool_id=pool_id,
            relative_path=filename,
            source_path=str(destination),
            size_bytes=size_bytes,
            checksum_sha256=checksum,
            status=NasFileState.ONLINE_CACHED,
            cache_path=str(destination),
        ).model_dump(mode="json")
    )
    _sync_dataset_summary(repo, dataset)

    return UploadResponse(
        file_id=file_id,
        filename=filename,
        size_bytes=size_bytes,
        checksum_sha256=checksum,
        pool_id=pool_id,
        status="pending_archive",
    )


@router.get("/files/{file_id}/download", dependencies=[Depends(require_auth)])
async def download_file(
    file_id: str,
    repo: CatalogRepository = Depends(get_catalog),
) -> FileResponse:
    """Download a staged or restored file by ID."""
    _validate_file_id(file_id)
    record = repo.get_nas_file_record(file_id)
    path = _resolve_file_path(file_id, record)
    if path is None:
        raise HTTPException(status_code=404, detail=f"File {file_id} not found")
    filename = _sanitize_filename(record.get("relative_path") if record else file_id)
    return FileResponse(path=path, filename=filename)


@router.get("/files/{file_id}/checksum", response_model=FileChecksumResponse, dependencies=[Depends(require_auth)])
async def get_file_checksum(
    file_id: str,
    repo: CatalogRepository = Depends(get_catalog),
) -> FileChecksumResponse:
    """Return the SHA-256 checksum for a staged or restored file."""
    _validate_file_id(file_id)
    record = repo.get_nas_file_record(file_id)
    path = _resolve_file_path(file_id, record)
    if path is None:
        raise HTTPException(status_code=404, detail=f"File {file_id} not found")

    sha256 = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(65_536):
            sha256.update(chunk)

    return FileChecksumResponse(file_id=file_id, checksum_sha256=sha256.hexdigest())


@router.get("/pools/{pool_id}/files", response_model=PoolFileListResponse, dependencies=[Depends(require_auth)])
async def list_pool_files(
    pool_id: str,
    repo: CatalogRepository = Depends(get_catalog),
) -> PoolFileListResponse:
    """List staged files for a pool."""
    dataset = repo.get_nas_dataset(_dataset_id(pool_id))
    if dataset is None:
        return PoolFileListResponse(pool_id=pool_id, files=[])

    files = sorted(
        (_serialize_record(record) for record in repo.list_nas_file_records(str(dataset["id"]))),
        key=lambda record: (record.created_at or "", record.filename),
        reverse=True,
    )
    return PoolFileListResponse(pool_id=pool_id, files=files)


@router.delete("/files/{file_id}", response_model=DeleteFileResponse, dependencies=[Depends(require_auth)])
async def delete_file(
    file_id: str,
    repo: CatalogRepository = Depends(get_catalog),
) -> DeleteFileResponse:
    """Remove a staged file and its catalog entry."""
    _validate_file_id(file_id)
    record = repo.delete_nas_file_record(file_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"File {file_id} not found")

    path = _resolve_file_path(file_id, record)
    if path is not None:
        path.unlink(missing_ok=True)

    dataset_id = record.get("dataset_id")
    if isinstance(dataset_id, str):
        dataset = repo.get_nas_dataset(dataset_id)
        if dataset is not None:
            _sync_dataset_summary(repo, dataset)

    return DeleteFileResponse(deleted=True)
