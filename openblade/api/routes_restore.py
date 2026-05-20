"""Restore API endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from openblade.bootstrap import AppContext, get_context

router = APIRouter()


class RestoreRequest(BaseModel):
    catalog_path: str | None = None
    source_path: str | None = None
    dest_path: str


class EnqueuedJobResponse(BaseModel):
    job_id: str
    status: str


@router.post("/", response_model=EnqueuedJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def enqueue_restore(
    request: RestoreRequest,
    context: AppContext = Depends(get_context),
) -> EnqueuedJobResponse:
    catalog_path = request.catalog_path or request.source_path
    if catalog_path is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="catalog_path or source_path is required")
    job = context.restore_service.enqueue(catalog_path, Path(request.dest_path))
    return EnqueuedJobResponse(job_id=job.id, status="pending")
