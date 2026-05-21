"""Cartridge management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from openblade.api.routes_aml_auth import require_auth
from openblade.api.service_auth import require_service_token
from openblade.bootstrap import AppContext, get_context

router = APIRouter()


class CartridgeResponse(BaseModel):
    barcode: str
    volume_group_id: str | None
    capacity_bytes: int
    used_bytes: int
    state: str
    formatted: bool


class DryRunResponse(BaseModel):
    operation: str
    target: str
    affected_barcodes: list[str]
    warnings: list[str]
    is_destructive: bool
    token: str


class FormatConfirmRequest(BaseModel):
    barcode: str
    token: str


class OperationResponse(BaseModel):
    success: bool
    message: str
    details: dict[str, str | int | float]


@router.get("/", response_model=list[CartridgeResponse], dependencies=[Depends(require_auth)])
async def list_cartridges(context: AppContext = Depends(get_context)) -> list[CartridgeResponse]:
    return [
        CartridgeResponse(
            barcode=cartridge.barcode,
            volume_group_id=cartridge.volume_group_id,
            capacity_bytes=cartridge.capacity_bytes,
            used_bytes=cartridge.used_bytes,
            state=cartridge.state,
            formatted=cartridge.formatted,
        )
        for cartridge in context.catalog.list_cartridges()
    ]


@router.post("/{barcode}/format/dry-run", response_model=DryRunResponse, dependencies=[Depends(require_auth)])
async def format_dry_run(
    barcode: str,
    context: AppContext = Depends(get_context),
) -> DryRunResponse:
    plan, token = context.format_service.dry_run(barcode)
    return DryRunResponse(
        operation=plan.operation,
        target=plan.target,
        affected_barcodes=plan.affected_barcodes,
        warnings=plan.warnings,
        is_destructive=plan.is_destructive,
        token=token.token,
    )


@router.post(
    "/format/confirm",
    response_model=OperationResponse,
    dependencies=[Depends(require_auth), Depends(require_service_token)],
)
async def format_confirm(
    request: FormatConfirmRequest,
    context: AppContext = Depends(get_context),
) -> OperationResponse:
    result = context.format_service.confirm(request.barcode, request.token)
    return OperationResponse(success=result.success, message=result.message, details=result.details)
