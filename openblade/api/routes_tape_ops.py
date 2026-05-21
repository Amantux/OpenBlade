"""Tape operation API routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from openblade.api.rbac_deps import ensure_permission_for_user, require_permission
from openblade.api.routes_aml_auth import AmlUser, require_auth
from openblade.bootstrap import get_context
from openblade.catalog.db import get_catalog_repository
from openblade.catalog.repository import CatalogRepository
from openblade.nas.rbac_service import RbacService
from openblade.nas.tape_orchestrator import OperationNotConfirmedError, TapeOperationOrchestrator
from openblade.nas.types import RbacPermission, TapeOpRecord, TapeOpRequest, TapeOpType

router = APIRouter(prefix="/tape-ops", tags=["tape-ops"])


def get_tape_orchestrator(
    repo: CatalogRepository = Depends(get_catalog_repository),
) -> TapeOperationOrchestrator:
    """Build a tape orchestrator from the active app context."""
    context = get_context()
    return TapeOperationOrchestrator(repo, context.library, context.ltfs)


def _permission_for_op(op_type: TapeOpType) -> RbacPermission:
    if op_type in {TapeOpType.READ, TapeOpType.VERIFY}:
        return RbacPermission.TAPE_READ
    return RbacPermission.TAPE_WRITE


@router.post("/execute", response_model=TapeOpRecord)
async def execute_tape_op(
    request: Request,
    payload: TapeOpRequest,
    current_user: AmlUser = Depends(require_auth),
    repo: CatalogRepository = Depends(get_catalog_repository),
    orchestrator: TapeOperationOrchestrator = Depends(get_tape_orchestrator),
) -> TapeOpRecord:
    """Execute a tape operation through the orchestrator."""
    ensure_permission_for_user(
        RbacService(repo),
        current_user,
        _permission_for_op(payload.op_type),
        request,
    )
    try:
        return orchestrator.execute(payload)
    except OperationNotConfirmedError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/{op_id}", response_model=TapeOpRecord)
async def get_tape_op(
    op_id: str,
    _: AmlUser = Depends(require_permission(RbacPermission.TAPE_READ)),
    orchestrator: TapeOperationOrchestrator = Depends(get_tape_orchestrator),
) -> TapeOpRecord:
    """Return a single tape operation audit record."""
    record = orchestrator.get_op(op_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tape operation not found")
    return record


@router.get("", response_model=list[TapeOpRecord])
async def list_tape_ops(
    barcode: str | None = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    _: AmlUser = Depends(require_permission(RbacPermission.TAPE_READ)),
    orchestrator: TapeOperationOrchestrator = Depends(get_tape_orchestrator),
) -> list[TapeOpRecord]:
    """List tape operation audit records."""
    return orchestrator.list_ops(barcode=barcode, status=status_filter, limit=limit)
