"""Upload and download routes for File Station workflows."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

# UUID4 pattern — only format accepted as file_id in path parameters
_UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# Maximum upload size: 10 GiB by default, overridable via env
_MAX_UPLOAD_BYTES = int(os.environ.get("OPENBLADE_MAX_UPLOAD_BYTES", str(10 * 1024 ** 3)))


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

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from openblade.api.routes_aml_auth import require_auth
from openblade.bootstrap import get_catalog, get_context
from openblade.catalog.repository import CatalogRepository
from openblade.nas.ingest import (
    get_ingest_job,
    register_archive_plan,
    run_ingest_job,
    start_ingest_job,
)
from openblade.nas.planner import ArchivePlanner
from openblade.nas.service import NasService
from openblade.nas.types import (
    ArchivePlanRequest,
    DatasetStatus,
    IngestMode,
    NasFileRecord,
    NasFileState,
    NasPool,
    NasShareDefinition,
)

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


class PushToShareRequest(BaseModel):
    share_path: str
    file_ids: list[str] = Field(default_factory=list, max_length=500)
    target_prefix: str = ""
    dataset_name: str | None = None


class PushToShareResponse(BaseModel):
    job_id: str
    dataset_id: str
    plan_id: str
    pushed_files: int
    pool_id: str
    share_path: str
    status: str


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


def _normalize_share_path(path: str) -> str:
    normalized = str(path or "").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="share_path is required")
    return normalized if normalized.startswith("/") else f"/{normalized}"


def _normalize_target_prefix(prefix: str) -> str:
    value = str(prefix or "").strip().strip("/")
    if not value:
        return ""
    path = Path(value)
    if any(part in {"..", ""} for part in path.parts):
        raise HTTPException(status_code=400, detail="target_prefix contains invalid path components")
    return "/".join(path.parts)


def _resolve_share_for_pool(service: NasService, *, pool_id: str, share_path: str) -> NasShareDefinition:
    share = service.get_share(share_path)
    if share is None:
        raise HTTPException(status_code=404, detail=f"Share {share_path} not found")
    if pool_id not in share.pool_ids:
        raise HTTPException(status_code=400, detail=f"Share {share_path} is not mapped to pool {pool_id}")
    return share


def _resolve_pool_for_share(service: NasService, *, pool_id: str, share: NasShareDefinition) -> NasPool:
    pool = service.get_pool(pool_id)
    if pool is None:
        raise HTTPException(status_code=400, detail=f"Pool {pool_id} not found")
    policy_id = share.default_policy_id or pool.default_policy_id
    if not policy_id:
        raise HTTPException(
            status_code=400,
            detail=f"No default policy configured for share {share.path} or pool {pool_id}",
        )
    return pool


def _available_data_tapes(repo: CatalogRepository) -> list[str]:
    barcodes: set[str] = set()
    # The physical library holds the writable media (scratch/loaded tapes); the
    # catalog only tracks already-archived cartridges, so it can be empty even
    # when tapes are available. Source from both.
    try:
        for slot in get_context().library.inventory().slots:
            if slot.barcode is not None:
                barcodes.add(str(slot.barcode))
    except Exception:  # noqa: BLE001 - library unavailable falls back to the catalog
        pass
    for cartridge in repo.list_cartridges():
        barcode = cartridge.get("barcode") if isinstance(cartridge, dict) else None
        value = str(barcode or "").strip()
        if value:
            barcodes.add(value)
    return sorted(value for value in barcodes if not value.upper().startswith("CLN"))


def _safe_workspace_path(workspace: Path, relative_path: str) -> Path:
    target = (workspace / relative_path).resolve()
    if not str(target).startswith(str(workspace.resolve())):
        raise HTTPException(status_code=400, detail="Invalid destination path")
    return target


def _run_share_push_ingest_job(
    *,
    job_id: str,
    nas_service: NasService,
    library,
    ltfs,
    workspace: Path,
    repo: CatalogRepository,
    pool_id: str,
    staged_file_ids: list[str],
) -> None:
    try:
        run_ingest_job(
            job_id,
            nas_service=nas_service,
            library=library,
            ltfs=ltfs,
        )
        job = get_ingest_job(job_id)
        if job is None or job.status is not DatasetStatus.ARCHIVED:
            return
        for file_id in staged_file_ids:
            repo.delete_nas_file_record(file_id)
        dataset = repo.get_nas_dataset(_dataset_id(pool_id))
        if dataset is not None:
            _sync_dataset_summary(repo, dataset)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


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
                size_bytes += len(chunk)
                if size_bytes > _MAX_UPLOAD_BYTES:
                    destination.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds maximum allowed size of {_MAX_UPLOAD_BYTES // (1024 ** 2)} MiB",
                    )
                handle.write(chunk)
                sha256.update(chunk)
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
    """List staged or archived files known for a pool."""
    dataset = repo.get_nas_dataset(_dataset_id(pool_id))
    if dataset is not None:
        records = repo.list_nas_file_records(str(dataset["id"]))
    else:
        records = [record.model_dump(mode="json") for record in NasService(repo).list_pool_file_records(pool_id)]

    files = sorted(
        (_serialize_record(record) for record in records),
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


@router.post(
    "/pools/{pool_id}/push-to-share",
    response_model=PushToShareResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_auth)],
)
async def push_staged_files_to_share(
    pool_id: str,
    request: PushToShareRequest,
    background_tasks: BackgroundTasks,
    repo: CatalogRepository = Depends(get_catalog),
) -> PushToShareResponse:
    service = NasService(repo)
    if not service.get_source_stream_config().enabled:
        raise HTTPException(status_code=503, detail="Source-stream ingest is disabled")

    share_path = _normalize_share_path(request.share_path)
    share = _resolve_share_for_pool(service, pool_id=pool_id, share_path=share_path)
    pool = _resolve_pool_for_share(service, pool_id=pool_id, share=share)

    policy_id = share.default_policy_id or pool.default_policy_id
    assert policy_id is not None
    policy = service.get_policy(policy_id)
    if policy is None:
        raise HTTPException(status_code=400, detail=f"Policy {policy_id} not found")

    dataset = _ensure_pool_dataset(repo, pool_id)
    records = repo.list_nas_file_records(str(dataset["id"]))
    records_by_id = {str(record["id"]): record for record in records}

    selected_ids = request.file_ids or [
        str(record["id"])
        for record in records
        if str(record.get("status") or "") == NasFileState.ONLINE_CACHED.value
    ]
    if not selected_ids:
        raise HTTPException(status_code=400, detail="No staged files available to push")

    target_prefix = _normalize_target_prefix(request.target_prefix)
    workspace = _staging_dir() / "push-jobs" / str(uuid.uuid4())
    workspace.mkdir(parents=True, exist_ok=True)
    used_paths: set[str] = set()
    relative_files: list[str] = []
    file_sizes: dict[str, int] = {}
    staged_file_ids: list[str] = []
    try:
        for file_id in selected_ids:
            _validate_file_id(file_id)
            record = records_by_id.get(file_id)
            if record is None:
                raise HTTPException(status_code=404, detail=f"File {file_id} not found in pool {pool_id}")
            if str(record.get("pool_id") or "") != pool_id:
                raise HTTPException(status_code=400, detail=f"File {file_id} does not belong to pool {pool_id}")
            if str(record.get("status") or "") != NasFileState.ONLINE_CACHED.value:
                raise HTTPException(status_code=400, detail=f"File {file_id} is not pending archive")
            source_path = _resolve_file_path(file_id, record)
            if source_path is None:
                raise HTTPException(status_code=404, detail=f"Staged content for {file_id} was not found")

            base_name = _sanitize_filename(record.get("relative_path") if isinstance(record, dict) else None)
            candidate = f"{target_prefix}/{base_name}" if target_prefix else base_name
            if candidate in used_paths:
                candidate = (
                    f"{target_prefix}/{file_id}-{base_name}" if target_prefix else f"{file_id}-{base_name}"
                )
            used_paths.add(candidate)
            destination_path = _safe_workspace_path(workspace, candidate)
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination_path)

            relative_files.append(candidate)
            file_sizes[candidate] = max(int(destination_path.stat().st_size), 0)
            staged_file_ids.append(file_id)

        available_tapes = _available_data_tapes(repo)
        request_data = {
            "policy_id": policy.id,
            "policy_type": policy.policy_type,
            "ingest_mode": IngestMode.SOURCE_STREAM,
            "source_path": str(workspace),
            "pool": pool_id,
            "files": relative_files,
            "file_sizes": file_sizes,
            "available_tapes": available_tapes,
            "copies": max(int(policy.copies_required), 1),
            "verify_before_archive": bool(policy.verify_before_archive),
            "verify_after_archive": bool(policy.verify_after_archive),
            "max_parallelism": max(int(policy.max_parallelism), 1),
        }
        if policy.shard_size_bytes is not None:
            request_data["shard_size_bytes"] = int(policy.shard_size_bytes)
        if policy.shard_strategy is not None:
            request_data["shard_strategy"] = policy.shard_strategy

        plan = ArchivePlanner().plan(ArchivePlanRequest.model_validate(request_data))
        plan.policy_name = policy.name
        plan.policy_type = policy.policy_type
        if not plan.is_safe_to_enqueue:
            raise HTTPException(
                status_code=400,
                detail=f"Archive plan is not safe to enqueue: {', '.join(plan.enqueue_blockers)}",
            )
        plan = register_archive_plan(plan)

        dataset_name = request.dataset_name or (
            f"share-push-{pool_id}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        )
        job = start_ingest_job(
            plan=plan,
            dataset_name=dataset_name,
            pool_id=pool_id,
            nas_service=service,
            cache_drive_id=None,
        )
        context = get_context()
        background_tasks.add_task(
            _run_share_push_ingest_job,
            job_id=job.job_id,
            nas_service=service,
            library=context.library,
            ltfs=context.ltfs,
            workspace=workspace,
            repo=repo,
            pool_id=pool_id,
            staged_file_ids=staged_file_ids,
        )

        return PushToShareResponse(
            job_id=job.job_id,
            dataset_id=job.dataset_id,
            plan_id=plan.plan_id,
            pushed_files=len(staged_file_ids),
            pool_id=pool_id,
            share_path=share.path,
            status="running",
        )
    except Exception:
        shutil.rmtree(workspace, ignore_errors=True)
        raise
