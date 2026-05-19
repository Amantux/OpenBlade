"""Archive API endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from openblade.bootstrap import AppContext, get_context

router = APIRouter()


class ArchiveRequest(BaseModel):
    source_path: str
    volume_group: str


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
