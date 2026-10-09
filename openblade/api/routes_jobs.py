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


class DriveMismatchResponse(BaseModel):
    lease_id: str
    job_id: str
    drive_id: int
    expected_barcode: str
    observed_barcode: str | None


class StagedInstanceResponse(BaseModel):
    instance_id: str
    barcode: str
    tape_path: str
    shard_index: int | None
    state: str


class RecoveryReportResponse(BaseModel):
    interrupted_job_ids: list[str]
    released_lease_ids: list[str]
    mismatches: list[DriveMismatchResponse]
    staged_instances: dict[str, list[StagedInstanceResponse]]
    stale_pending_job_ids: list[str]


# Declared before /{job_id} so the literal path wins route matching.
@router.get("/recovery", response_model=RecoveryReportResponse)
async def get_recovery_report(
    context: AppContext = Depends(get_context),
) -> RecoveryReportResponse:
    """What startup recovery did: interrupted jobs, released leases, drive mismatches,
    uncommitted staged instances per recoverable job, and stale pending jobs."""
    report = context.recovery_report
    return RecoveryReportResponse(
        interrupted_job_ids=list(report.interrupted_job_ids),
        released_lease_ids=list(report.released_lease_ids),
        mismatches=[
            DriveMismatchResponse(
                lease_id=m.lease_id,
                job_id=m.job_id,
                drive_id=m.drive_id,
                expected_barcode=m.expected_barcode,
                observed_barcode=m.observed_barcode,
            )
            for m in report.mismatches
        ],
        staged_instances={
            job_id: [
                StagedInstanceResponse(
                    instance_id=i.instance_id,
                    barcode=i.barcode,
                    tape_path=i.tape_path,
                    shard_index=i.shard_index,
                    state=i.state,
                )
                for i in instances
            ]
            for job_id, instances in report.staged_instances.items()
        },
        stale_pending_job_ids=list(report.stale_pending_job_ids),
    )


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
