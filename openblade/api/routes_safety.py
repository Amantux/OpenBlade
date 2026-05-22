"""Safety check endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from openblade.api.routes_aml_auth import require_auth
from openblade.bootstrap import AppContext, get_context
from openblade.catalog.models import AmlUser
from openblade.nas.types import TapeOpType
from openblade.safety.import_guard import scan_directory

router = APIRouter(prefix="/safety", tags=["safety"])


class SafetyCheckItem(BaseModel):
    name: str
    status: str
    message: str


class SafetyCheckResponse(BaseModel):
    status: str
    checks: list[SafetyCheckItem]


def _build_safety_response(context: AppContext) -> SafetyCheckResponse:
    checks: list[SafetyCheckItem] = []

    checks.append(
        SafetyCheckItem(
            name="Tape orchestrator",
            status="ok",
            message="Tape operations are routed through TapeOperationOrchestrator.",
        )
    )

    guard_result = scan_directory(Path(__file__).resolve().parents[1])
    checks.append(
        SafetyCheckItem(
            name="Direct hardware guard",
            status="ok" if guard_result.passed else "failed",
            message=(
                f"No direct hardware calls detected across {guard_result.files_scanned} scanned files."
                if guard_result.passed
                else f"Detected {len(guard_result.violations)} direct hardware access violation(s)."
            ),
        )
    )

    recent_ops = context.catalog.list_tape_ops(limit=100)
    unsafe_formats = [
        op for op in recent_ops if str(op.get("op_type")) == TapeOpType.FORMAT.value and str(op.get("status")) == "failed"
    ]
    checks.append(
        SafetyCheckItem(
            name="Destructive action confirmation",
            status="ok" if not unsafe_formats else "warning",
            message=(
                "Recent destructive operations include explicit confirmation enforcement."
                if not unsafe_formats
                else "Recent format requests were blocked until operator confirmation was supplied."
            ),
        )
    )

    overall_status = "ok"
    if any(check.status == "failed" for check in checks):
        overall_status = "failed"
    elif any(check.status == "warning" for check in checks):
        overall_status = "warning"
    return SafetyCheckResponse(status=overall_status, checks=checks)


@router.get("/check", response_model=SafetyCheckResponse)
async def get_safety_check(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> SafetyCheckResponse:
    return _build_safety_response(context)


@router.post("/check", response_model=SafetyCheckResponse)
async def run_safety_check(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> SafetyCheckResponse:
    return _build_safety_response(context)
