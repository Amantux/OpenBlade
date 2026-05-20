"""Archive API endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from openblade.api import aml_state
from openblade.bootstrap import AppContext, get_context
from openblade.jobs.scheduler import DriveScheduler
from openblade.jobs.shard import ShardMode
from openblade.jobs.sharded_archive import ShardedArchiveRequest, run_sharded_archive

router = APIRouter()


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
            "completedTime": _timestamp(job.updated_at) if status in {"completed", "failed", "cancelled"} else None,
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
            "severity": "info",
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


@router.post("/", response_model=EnqueuedJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def enqueue_archive(
    request: ArchiveRequest,
    context: AppContext = Depends(get_context),
) -> EnqueuedJobResponse:
    job = context.archive_service.enqueue(request.volume_group, Path(request.source_path))
    _bridge_to_aml(
        context,
        job_id=job.id,
        status=job.state,
        source_path=request.source_path,
        volume_group=request.volume_group,
        error=job.error,
    )
    return EnqueuedJobResponse(job_id=job.id, status="pending")


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
    return EnqueuedJobResponse(job_id=job.id, status="pending")
