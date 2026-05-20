"""Archive API endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from uuid import uuid4

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from openblade.api import aml_state
from openblade.bootstrap import AppContext, get_context
from openblade.jobs.archive import ArchiveRequest as ArchiveJobRequest
from openblade.jobs.archive import run_archive_job
from openblade.jobs.scheduler import DriveScheduler
from openblade.jobs.shard import ShardMode
from openblade.jobs.sharded_archive import ShardedArchiveRequest, run_sharded_archive

router = APIRouter()

_TERMINAL_JOB_STATUSES = {"completed", "failed", "failed_recoverable", "cancelled"}


def _timestamp(value: datetime | None) -> str:
    if value is None:
        value = datetime.now(timezone.utc)
    elif value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def _bridge_to_aml(
    context: AppContext,
    *,
    job_id: str,
    status: str,
    source_path: str,
    volume_group: str,
    error: str | None = None,
) -> None:
    aml_state.ensure_initialized(context.config.db_url)
    job = context.catalog.get_job(job_id)
    if job is None:
        return
    result = error or f"Archived {source_path} into volume group {volume_group}"
    aml_state.set_aml_job(
        job.id,
        {
            "type": "archive",
            "status": status,
            "priority": "normal",
            "startTime": _timestamp(job.created_at),
            "completedTime": _timestamp(job.updated_at) if status in _TERMINAL_JOB_STATUSES else None,
            "progress": 100 if status == "completed" else 0,
            "result": result,
        },
    )
    message = f"Archive {status}: {source_path} -> /{volume_group}"
    details = {
        "jobId": job.id,
        "status": status,
        "sourcePath": source_path,
        "volumeGroup": volume_group,
    }
    if error is not None:
        details["error"] = error
    aml_state.append_aml_event(
        {
            "id": str(uuid4()),
            "timestamp": _timestamp(job.updated_at),
            "severity": "error" if error is not None or status.startswith("failed") else "info",
            "component": "archive",
            "message": message,
            "details": details,
        }
    )


class ArchiveRequest(BaseModel):
    source_path: str
    volume_group: str


class ShardedArchiveApiRequest(BaseModel):
    source_path: str
    volume_group: str
    lane_barcodes: list[str]
    mode: ShardMode = ShardMode.STRIPE
    block_size_mb: int = Field(default=128, ge=1)


class EnqueuedJobResponse(BaseModel):
    job_id: str
    status: str


def _iter_archive_source_files(source_path: Path) -> list[Path]:
    if source_path.is_file():
        return [source_path]
    return sorted(path for path in source_path.rglob("*") if path.is_file())


def _cleanup_failed_archive(context: AppContext, source_path: Path, volume_group: str) -> None:
    for file_path in _iter_archive_source_files(source_path):
        relative = (
            file_path.name
            if source_path.is_file()
            else str(file_path.relative_to(source_path))
        )
        catalog_path = str(PurePosixPath("/") / volume_group / relative)
        context.catalog.delete_file_record_if_unarchived(catalog_path)


@router.post("/", response_model=EnqueuedJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def enqueue_archive(
    request: ArchiveRequest,
    context: AppContext = Depends(get_context),
) -> EnqueuedJobResponse:
    source_path = Path(request.source_path)
    job = context.catalog.create_job(
        "archive",
        {"source_path": request.source_path, "volume_group": request.volume_group},
    )
    try:
        run_archive_job(
            ArchiveJobRequest(
                source_path=source_path,
                volume_group_name=request.volume_group,
            ),
            context.library,
            context.ltfs,
            context.catalog,
            job.id,
        )
    except Exception as exc:
        _cleanup_failed_archive(context, source_path, request.volume_group)
        context.catalog.update_job_state(job.id, "failed", str(exc))
        _bridge_to_aml(
            context,
            job_id=job.id,
            status="failed",
            source_path=request.source_path,
            volume_group=request.volume_group,
            error=str(exc),
        )
        raise
    refreshed = context.catalog.get_job(job.id)
    assert refreshed is not None
    _bridge_to_aml(
        context,
        job_id=refreshed.id,
        status=refreshed.state,
        source_path=request.source_path,
        volume_group=request.volume_group,
        error=refreshed.error,
    )
    return EnqueuedJobResponse(job_id=refreshed.id, status="pending")


@router.post(
    "/sharded",
    response_model=EnqueuedJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def enqueue_sharded_archive(
    request: ShardedArchiveApiRequest,
    context: AppContext = Depends(get_context),
) -> EnqueuedJobResponse:
    job = context.catalog.create_job(
        "archive",
        {
            "source_path": request.source_path,
            "volume_group": request.volume_group,
            "lane_barcodes": request.lane_barcodes,
            "mode": request.mode.value,
            "block_size_mb": request.block_size_mb,
        },
    )
    scheduler = DriveScheduler(num_drives=len(context.library.inventory().drives))
    try:
        run_sharded_archive(
            ShardedArchiveRequest(
                source_path=Path(request.source_path),
                volume_group_name=request.volume_group,
                lane_barcodes=request.lane_barcodes,
                mode=request.mode,
                block_size=request.block_size_mb * 1024 * 1024,
            ),
            context.library,
            context.ltfs,
            context.catalog,
            scheduler,
            job.id,
        )
    except Exception as exc:
        context.catalog.update_job_state(job.id, "failed", str(exc))
        _bridge_to_aml(
            context,
            job_id=job.id,
            status="failed",
            source_path=request.source_path,
            volume_group=request.volume_group,
            error=str(exc),
        )
        raise
    refreshed = context.catalog.get_job(job.id)
    assert refreshed is not None
    _bridge_to_aml(
        context,
        job_id=refreshed.id,
        status=refreshed.state,
        source_path=request.source_path,
        volume_group=request.volume_group,
        error=refreshed.error,
    )
    return EnqueuedJobResponse(job_id=refreshed.id, status="pending")
