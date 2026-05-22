"""Job status endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
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
    library_id: int | None = None


@router.get("/", response_model=list[JobResponse])
async def list_jobs(
    library_id: int | None = Query(None),
    context: AppContext = Depends(get_context),
) -> list[JobResponse]:
    jobs = [
        JobResponse(
            id=job.id,
            state=job.state,
            job_type=job.job_type,
            error=job.error,
            metadata=job.metadata_dict,
            created_at=job.created_at.isoformat(),
            updated_at=job.updated_at.isoformat(),
            library_id=None,
        )
        for job in context.catalog.list_jobs()
    ]
    if library_id is None:
        return jobs
    # null library_id jobs are installation-wide — include in every per-library view
    return [job for job in jobs if job.library_id is None or job.library_id == library_id]


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
        library_id=None,
    )
