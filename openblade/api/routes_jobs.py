"""Job status endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from openblade.bootstrap import AppContext, get_context

router = APIRouter()


class JobResponse(BaseModel):
    id: str
    state: str
    job_type: str
    error: str | None
    metadata: dict[str, object]
    created_at: str
    updated_at: str


@router.get("/", response_model=list[JobResponse])
async def list_jobs(context: AppContext = Depends(get_context)) -> list[JobResponse]:
    return [
        JobResponse(
            id=job.id,
            state=job.state,
            job_type=job.job_type,
            error=job.error,
            metadata=job.metadata_dict,
            created_at=job.created_at.isoformat(),
            updated_at=job.updated_at.isoformat(),
        )
        for job in context.catalog.list_jobs()
    ]


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: str, context: AppContext = Depends(get_context)) -> JobResponse:
    job = context.catalog.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return JobResponse(
        id=job.id,
        state=job.state,
        job_type=job.job_type,
        error=job.error,
        metadata=job.metadata_dict,
        created_at=job.created_at.isoformat(),
        updated_at=job.updated_at.isoformat(),
    )
