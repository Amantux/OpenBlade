"""Restore API endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from openblade.api import aml_state
from openblade.bootstrap import AppContext, get_context
from openblade.domain.errors import safe_job_error
from openblade.jobs.restore import RestoreRequest as RestoreJobRequest
from openblade.jobs.restore import run_restore_job
from openblade.jobs.scheduler import DriveScheduler
from openblade.jobs.sharded_restore import ShardedRestoreRequest, run_sharded_restore
from openblade.jobs.tree_restore import TreeRestoreRequest, run_tree_restore

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
            "completedTime": _timestamp(job.updated_at)
            if status in _TERMINAL_JOB_STATUSES
            else None,
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


class TreeRestoreApiRequest(BaseModel):
    catalog_prefix: str = Field(min_length=1)
    dest_dir: str = Field(min_length=1)
    #: Plan only: counts and per-tape totals come back, no media moves.
    dry_run: bool = False


class TreeRestoreFailureResponse(BaseModel):
    catalogPath: str
    error: str


class TreeRestoreResponse(BaseModel):
    """The shape ``openblade restore tree`` already emits (``TreeRestoreResult``)."""

    jobId: str
    catalogPrefix: str
    destDir: str
    dryRun: bool
    filesRestored: int
    filesFailed: int
    bytesRestored: int
    filesVerified: int
    perTapeCounts: dict[str, int]
    tapesUsed: list[str]
    failures: list[TreeRestoreFailureResponse]
    #: Catalogued records with nothing archived. Not failures, but an operator who
    #: asked for a tree and got fewer files back has to be told which ones.
    filesSkipped: int
    skippedPaths: list[str]
    status: str


@router.post("/", response_model=EnqueuedJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def enqueue_restore(
    request: RestoreRequest,
    context: AppContext = Depends(get_context),
) -> EnqueuedJobResponse:
    catalog_path = request.catalog_path or request.source_path
    if catalog_path is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="catalog_path or source_path is required",
        )
    job = context.catalog.create_job(
        "restore",
        {"catalog_path": catalog_path, "dest_path": request.dest_path},
    )
    record = context.catalog.get_file_record(catalog_path)
    use_sharded_restore = False
    if record is not None:
        use_sharded_restore = (
            bool(context.catalog.list_shard_records(record.id)) or (record.shard_count or 1) > 1
        )
    try:
        if use_sharded_restore:
            scheduler = DriveScheduler(num_drives=len(context.library.inventory().drives))
            run_sharded_restore(
                ShardedRestoreRequest(catalog_path=catalog_path, dest_path=Path(request.dest_path)),
                context.library,
                context.ltfs,
                context.catalog,
                scheduler,
                job.id,
            )
        else:
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


@router.post("/tree", response_model=TreeRestoreResponse)
async def restore_tree(
    request: TreeRestoreApiRequest,
    context: AppContext = Depends(get_context),
) -> TreeRestoreResponse:
    """Restore every archived file under a catalog prefix, spanning tapes.

    The HTTP half of ``openblade restore tree``: same request, same
    ``TreeRestoreResult`` payload, same job row. ``dry_run: true`` plans only and
    is what a UI should call first — it is the only preview of how many files and
    which tapes a tree restore will touch.

    Runs to completion before answering (like ``POST /restore/``) rather than
    enqueueing, so the caller gets the authoritative per-tape result; progress
    ticks are the CLI's stderr affordance and have no home in one response.
    """
    job = context.catalog.create_job(
        "restore",
        {
            "catalog_prefix": request.catalog_prefix,
            "dest_dir": request.dest_dir,
            "bulk": True,
            "dry_run": request.dry_run,
        },
    )
    scheduler = DriveScheduler(num_drives=len(context.library.inventory().drives))
    try:
        # Deliberately NOT handed to a worker thread: `context.catalog` wraps one
        # process-global SQLAlchemy Session which is not thread-safe, and every
        # other handler in this router touches it from the event loop thread only
        # (see routes_assist.py for what running it off-loop costs).
        result = run_tree_restore(
            TreeRestoreRequest(
                catalog_prefix=request.catalog_prefix,
                dest_dir=Path(request.dest_dir),
                dry_run=request.dry_run,
            ),
            context.library,
            context.ltfs,
            context.catalog,
            scheduler,
            job.id,
        )
    except Exception as exc:
        # Curated text only: a tree restore wraps mkltfs/mtx output, which must
        # never reach the wire (see safe_job_error).
        message = safe_job_error(exc)
        context.catalog.update_job_state(job.id, "failed", error=message)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=message
        ) from None
    return TreeRestoreResponse.model_validate(result.to_dict())
