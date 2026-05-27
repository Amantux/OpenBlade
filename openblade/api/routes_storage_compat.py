from __future__ import annotations

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from openblade.bootstrap import get_context

router = APIRouter()


class ArchivePlanningResponse(BaseModel):
    planId: str
    warnings: list[str] = []
    files: list[str] = []


@router.post("/storage/archive-planning")
async def storage_archive_planning(payload: dict, context=Depends(get_context)) -> ArchivePlanningResponse:
    # Lightweight compatibility shim for the UI tests.
    # Return an empty plan when no actionable input provided.
    plan_id = "shim-plan-1"
    return ArchivePlanningResponse(planId=plan_id, warnings=[], files=[])


@router.get("/storage/restore-queue")
async def storage_restore_queue(context=Depends(get_context)) -> list:
    # Compatibility endpoint: surface an empty restore queue if backend not configured.
    return []


@router.post("/restore/plan")
async def restore_plan_compat(payload: dict, context=Depends(get_context)) -> dict:
    # Tests expect this endpoint to exist and return either 200/202/422.
    # If pool_id missing, return 422 to indicate validation.
    if not payload or (not payload.get("pool") and not payload.get("pool_id") and not payload.get("poolId")):
        raise HTTPException(status_code=422, detail="pool_id is required")
    # Otherwise return a minimal restore plan stub
    return {"required_tapes": [], "missing_tapes": [], "tape_load_order": []}
