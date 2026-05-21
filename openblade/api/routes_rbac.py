"""RBAC management routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from openblade.api.rbac_deps import require_permission
from openblade.api.routes_aml_auth import WSResultCode, require_auth
from openblade.catalog.db import get_catalog_repository
from openblade.catalog.models import AmlUser
from openblade.catalog.repository import CatalogRepository
from openblade.nas.rbac_service import RbacService
from openblade.nas.types import (
    CreateTokenRequest,
    CreateTokenResult,
    CreateUserRequest,
    RbacAuditEventRecord,
    RbacPermission,
    RbacRoleRecord,
    RbacUserRecord,
    UserSummary,
)

router = APIRouter(prefix="/auth", tags=["rbac"])


class CreateRoleRequest(BaseModel):
    id: str
    name: str
    description: str = ""
    permissions: list[RbacPermission] = Field(default_factory=list)


class UpdateRoleRequest(BaseModel):
    name: str
    description: str = ""
    permissions: list[RbacPermission] = Field(default_factory=list)


def get_rbac_service(repo: CatalogRepository = Depends(get_catalog_repository)) -> RbacService:
    return RbacService(repo)


def _current_rbac_user(service: RbacService, current_user: AmlUser) -> RbacUserRecord | None:
    payload = service.repo.get_user_by_username(current_user.name)
    if payload is None:
        return None
    return RbacUserRecord.model_validate(payload)


def _audit_actor(service: RbacService, current_user: AmlUser) -> tuple[str | None, str]:
    rbac_user = _current_rbac_user(service, current_user)
    if rbac_user is not None:
        return rbac_user.id, rbac_user.username
    return None, current_user.name


def _ensure_any_permission(
    service: RbacService,
    current_user: AmlUser,
    request: Request,
    *permissions: RbacPermission,
) -> None:
    rbac_user = _current_rbac_user(service, current_user)
    if rbac_user is not None and any(service.check_permission(rbac_user.id, permission) for permission in permissions):
        return
    if rbac_user is None and bool(getattr(current_user, "is_admin", False) or current_user.role == 0):
        return
    service.emit_audit_event(
        event_type="permission_denied",
        user_id=rbac_user.id if rbac_user is not None else None,
        username=rbac_user.username if rbac_user is not None else current_user.name,
        resource="rbac",
        action="|".join(permission.value for permission in permissions),
        outcome="denied",
        details={"permissions": [permission.value for permission in permissions]},
        ip_address=request.client.host if request.client is not None else None,
    )
    raise HTTPException(status_code=403, detail="Permission denied")


@router.get("/users", response_model=list[UserSummary])
async def list_users(
    request: Request,
    active_only: bool = False,
    current_user: AmlUser = Depends(require_auth),
    service: RbacService = Depends(get_rbac_service),
) -> list[UserSummary]:
    _ensure_any_permission(service, current_user, request, RbacPermission.SYSTEM_ADMIN, RbacPermission.USER_ADMIN)
    return service.list_users(active_only=active_only)


@router.post("/users", response_model=UserSummary, status_code=status.HTTP_201_CREATED)
async def create_user(
    request: Request,
    payload: CreateUserRequest,
    current_user: AmlUser = Depends(require_auth),
    service: RbacService = Depends(get_rbac_service),
) -> UserSummary:
    _ensure_any_permission(service, current_user, request, RbacPermission.SYSTEM_ADMIN, RbacPermission.USER_ADMIN)
    return service.create_user(payload)


@router.get("/users/{user_id}", response_model=UserSummary)
async def get_user(
    request: Request,
    user_id: str,
    current_user: AmlUser = Depends(require_auth),
    service: RbacService = Depends(get_rbac_service),
) -> UserSummary:
    current_rbac_user = _current_rbac_user(service, current_user)
    if current_rbac_user is None or current_rbac_user.id != user_id:
        _ensure_any_permission(service, current_user, request, RbacPermission.USER_ADMIN)
    summary = service.get_user_summary(user_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="User not found")
    return summary


@router.delete("/users/{user_id}", response_model=WSResultCode)
async def deactivate_user(
    user_id: str,
    current_user: AmlUser = Depends(require_permission(RbacPermission.USER_ADMIN)),
    service: RbacService = Depends(get_rbac_service),
) -> WSResultCode:
    actor = _current_rbac_user(service, current_user)
    actor_id = actor.id if actor is not None else current_user.name
    if not service.deactivate_user(user_id, actor_id):
        raise HTTPException(status_code=404, detail="User not found")
    return WSResultCode(summary="User deactivated")


@router.get("/roles", response_model=list[RbacRoleRecord])
async def list_roles(
    _: AmlUser = Depends(require_auth),
    service: RbacService = Depends(get_rbac_service),
) -> list[RbacRoleRecord]:
    return [RbacRoleRecord.model_validate(role) for role in service.repo.list_roles()]


@router.post("/roles", response_model=RbacRoleRecord, status_code=status.HTTP_201_CREATED)
async def create_role(
    payload: CreateRoleRequest,
    current_user: AmlUser = Depends(require_permission(RbacPermission.SYSTEM_ADMIN)),
    service: RbacService = Depends(get_rbac_service),
) -> RbacRoleRecord:
    created = service.repo.create_role(payload.model_dump(mode="json"))
    actor_id, actor_name = _audit_actor(service, current_user)
    service.emit_audit_event(
        event_type="role_created",
        user_id=actor_id,
        username=actor_name,
        resource="rbac:role",
        action="create",
        outcome="success",
        details={"role_id": created["id"], "name": created["name"]},
    )
    return RbacRoleRecord.model_validate(created)


@router.get("/roles/{role_id}", response_model=RbacRoleRecord)
async def get_role(
    role_id: str,
    _: AmlUser = Depends(require_auth),
    service: RbacService = Depends(get_rbac_service),
) -> RbacRoleRecord:
    role = service.repo.get_role(role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    return RbacRoleRecord.model_validate(role)


@router.put("/roles/{role_id}", response_model=RbacRoleRecord)
async def update_role(
    role_id: str,
    payload: UpdateRoleRequest,
    current_user: AmlUser = Depends(require_permission(RbacPermission.SYSTEM_ADMIN)),
    service: RbacService = Depends(get_rbac_service),
) -> RbacRoleRecord:
    updated = service.repo.update_role(role_id, payload.model_dump(mode="json"))
    if updated is None:
        raise HTTPException(status_code=404, detail="Role not found")
    actor_id, actor_name = _audit_actor(service, current_user)
    service.emit_audit_event(
        event_type="role_updated",
        user_id=actor_id,
        username=actor_name,
        resource="rbac:role",
        action="update",
        outcome="success",
        details={"role_id": updated["id"], "name": updated["name"]},
    )
    return RbacRoleRecord.model_validate(updated)


@router.post("/tokens", response_model=CreateTokenResult, status_code=status.HTTP_201_CREATED)
async def create_token(
    payload: CreateTokenRequest,
    current_user: AmlUser = Depends(require_permission(RbacPermission.TOKEN_MANAGE)),
    service: RbacService = Depends(get_rbac_service),
) -> CreateTokenResult:
    rbac_user = _current_rbac_user(service, current_user)
    if rbac_user is None:
        raise HTTPException(status_code=404, detail="RBAC user not found")
    return service.create_token(rbac_user.id, payload)


@router.get("/tokens", response_model=list[dict])
async def list_tokens(
    current_user: AmlUser = Depends(require_permission(RbacPermission.TOKEN_MANAGE)),
    service: RbacService = Depends(get_rbac_service),
) -> list[dict[str, object]]:
    rbac_user = _current_rbac_user(service, current_user)
    if rbac_user is None:
        raise HTTPException(status_code=404, detail="RBAC user not found")
    return service.repo.list_api_tokens(rbac_user.id)


@router.delete("/tokens/{token_id}", response_model=WSResultCode)
async def revoke_token(
    token_id: str,
    current_user: AmlUser = Depends(require_permission(RbacPermission.TOKEN_MANAGE)),
    service: RbacService = Depends(get_rbac_service),
) -> WSResultCode:
    rbac_user = _current_rbac_user(service, current_user)
    if rbac_user is None:
        raise HTTPException(status_code=404, detail="RBAC user not found")
    token = service.repo.get_api_token(token_id)
    if token is None:
        raise HTTPException(status_code=404, detail="Token not found")
    if str(token.get("user_id")) != rbac_user.id:
        raise HTTPException(status_code=403, detail="Permission denied")
    service.revoke_token(token_id, rbac_user.id)
    return WSResultCode(summary="Token revoked")


@router.get("/audit", response_model=list[RbacAuditEventRecord])
async def list_audit_events(
    user_id: str | None = None,
    event_type: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    _: AmlUser = Depends(require_permission(RbacPermission.AUDIT_READ)),
    service: RbacService = Depends(get_rbac_service),
) -> list[RbacAuditEventRecord]:
    return [
        RbacAuditEventRecord.model_validate(event)
        for event in service.repo.list_audit_events(limit=limit, user_id=user_id, event_type=event_type)
    ]
