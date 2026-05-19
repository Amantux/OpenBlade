"""Restore API endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from openblade.bootstrap import AppContext, get_context

router = APIRouter()


class RestoreRequest(BaseModel):
    catalog_path: str
    dest_path: str


class EnqueuedJobResponse(BaseModel):
    job_id: str
    status: str


@router.post("/", response_model=EnqueuedJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def enqueue_restore(
    request: RestoreRequest,
    context: AppContext = Depends(get_context),
) -> EnqueuedJobResponse:
    job = context.restore_service.enqueue(request.catalog_path, Path(request.dest_path))
    return EnqueuedJobResponse(job_id=job.id, status="pending")
