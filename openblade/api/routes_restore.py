"""Restore API endpoints."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from anyio import to_thread
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from openblade.api import aml_state
from openblade.api.routes_archive import _ARCHIVE_REQUEST_LOCK
from openblade.bootstrap import AppContext, get_context
from openblade.catalog.db import get_session
from openblade.catalog.repository import CatalogRepository
from openblade.domain.errors import OpenBladeError, safe_job_error
from openblade.jobs.restore import RestoreRequest as RestoreJobRequest
from openblade.jobs.restore import run_restore_job
from openblade.jobs.scheduler import DriveScheduler
from openblade.jobs.sharded_restore import ShardedRestoreRequest, run_sharded_restore
from openblade.jobs.tree_restore import TreeRestoreRequest, TreeRestoreResult, run_tree_restore

_log = logging.getLogger(__name__)

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

    The restore itself runs on a worker thread with its own database session. A
    tree restore is minutes of blocking work, and holding the event loop for that
    long stalls every other request in this process -- including ``/health``,
    which the container health check gives 5s, and the AML emulator parity surface
    this repo exists to serve. The per-thread session is the pattern
    :mod:`openblade.api.routes_assist` established for exactly this reason:
    ``AppContext.catalog`` wraps ONE process-global SQLAlchemy ``Session`` which
    is not thread-safe, so the worker gets its own rather than borrowing it.
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
    tree_request = TreeRestoreRequest(
        catalog_prefix=request.catalog_prefix,
        dest_dir=Path(request.dest_dir),
        dry_run=request.dry_run,
    )

    def _run() -> TreeRestoreResult:
        # Off-loop execution made concurrency REAL: before this ran on the
        # event loop, the loop itself serialized media work. Take the same
        # process-wide media lock the archive routes hold, or a tree restore
        # interleaves drive loads with POST /archive/ (plain files always
        # target drive 0) and two callers fight over one drive.
        db_session = get_session()
        try:
            with _ARCHIVE_REQUEST_LOCK:
                return run_tree_restore(
                    tree_request,
                    context.library,
                    context.ltfs,
                    CatalogRepository(db_session),
                    scheduler,
                    job.id,
                )
        finally:
            db_session.close()

    try:
        result = await to_thread.run_sync(_run)
    except Exception as exc:
        # Curated text only: a tree restore wraps mkltfs/mtx output, which must
        # never reach the wire (see safe_job_error). Logging the full exception
        # server-side is the other half of that contract.
        _log.exception(
            "tree restore failed", extra={"job_id": job.id, "prefix": request.catalog_prefix}
        )
        message = safe_job_error(exc)
        context.catalog.update_job_state(job.id, "failed", error=message)
        if isinstance(exc, OpenBladeError):
            # Typed domain errors carry operator-written messages AND a status
            # mapping in the app's handler -- an unsafe catalog path is a 400, not
            # a 500. Let it through rather than flattening it.
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=message
        ) from None
    return TreeRestoreResponse.model_validate(result.to_dict())
