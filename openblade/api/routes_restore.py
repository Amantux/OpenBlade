"""Restore API endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from openblade.api import aml_state
from openblade.bootstrap import AppContext, get_context
from openblade.jobs.restore import RestoreRequest as RestoreJobRequest
from openblade.jobs.restore import run_restore_job

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
    catalog_path: str,
    dest_path: str,
    error: str | None = None,
) -> None:
    aml_state.ensure_initialized(context.config.db_url)
    job = context.catalog.get_job(job_id)
    if job is None:
        return
    result = error or f"Restored {catalog_path} to {dest_path}"
    aml_state.set_aml_job(
        job.id,
        {
            "type": "restore",
            "status": status,
            "priority": "normal",
            "startTime": _timestamp(job.created_at),
            "completedTime": _timestamp(job.updated_at) if status in _TERMINAL_JOB_STATUSES else None,
            "progress": 100 if status == "completed" else 0,
            "result": result,
        },
    )
    message = f"Restore {status}: {catalog_path} -> {dest_path}"
    details = {
        "jobId": job.id,
        "status": status,
        "catalogPath": catalog_path,
        "destPath": dest_path,
    }
    if error is not None:
        details["error"] = error
    aml_state.append_aml_event(
        {
            "id": str(uuid4()),
            "timestamp": _timestamp(job.updated_at),
            "severity": "error" if error is not None or status.startswith("failed") else "info",
            "component": "restore",
            "message": message,
            "details": details,
        }
    )


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
    job = context.catalog.create_job(
        "restore",
        {"catalog_path": catalog_path, "dest_path": request.dest_path},
    )
    try:
        run_restore_job(
            RestoreJobRequest(catalog_path=catalog_path, dest_path=Path(request.dest_path)),
            context.library,
            context.ltfs,
            context.catalog,
            job.id,
        )
    except Exception as exc:
        context.catalog.update_job_state(job.id, "failed", str(exc))
        _bridge_to_aml(
            context,
            job_id=job.id,
            status="failed",
            catalog_path=catalog_path,
            dest_path=request.dest_path,
            error=str(exc),
        )
        raise
    refreshed = context.catalog.get_job(job.id)
    assert refreshed is not None
    _bridge_to_aml(
        context,
        job_id=refreshed.id,
        status=refreshed.state,
        catalog_path=catalog_path,
        dest_path=request.dest_path,
        error=refreshed.error,
    )
    return EnqueuedJobResponse(job_id=refreshed.id, status="pending")
