"""Archive API endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from openblade.bootstrap import AppContext, get_context
from openblade.jobs.scheduler import DriveScheduler
from openblade.jobs.shard import ShardMode
from openblade.jobs.sharded_archive import ShardedArchiveRequest, run_sharded_archive

router = APIRouter()


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
