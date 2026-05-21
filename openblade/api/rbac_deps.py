"""FastAPI RBAC permission dependencies."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from openblade.api.routes_aml_auth import require_auth
from openblade.catalog.db import get_catalog_repository
from openblade.catalog.models import AmlUser
from openblade.catalog.repository import CatalogRepository
from openblade.nas.rbac_service import RbacService
from openblade.nas.types import RbacPermission, RbacUserRecord


def _rbac_user_for_aml_user(service: RbacService, current_user: AmlUser) -> RbacUserRecord | None:
    payload = service.repo.get_user_by_username(current_user.name)
    if payload is None:
        return None
    return RbacUserRecord.model_validate(payload)


def _is_admin_user(current_user: AmlUser) -> bool:
    return bool(getattr(current_user, "is_admin", False) or getattr(current_user, "role", None) == 0)


def ensure_permission_for_user(
    service: RbacService,
    current_user: AmlUser,
    permission: RbacPermission,
    request: Request | None = None,
) -> None:
    """Check a cookie-authenticated user's RBAC permission or admin fallback."""
    rbac_user = _rbac_user_for_aml_user(service, current_user)
    if rbac_user is not None:
        try:
            service.require_permission(rbac_user.id, permission)
            return
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    if _is_admin_user(current_user):
        return
    service.emit_audit_event(
        event_type="permission_denied",
        user_id=None,
        username=current_user.name,
        resource="rbac",
        action=permission.value,
        outcome="denied",
        details={"permission": permission.value},
        ip_address=request.client.host if request is not None and request.client is not None else None,
    )
    raise HTTPException(status_code=403, detail="Permission denied")


def require_permission(permission: RbacPermission):
    """FastAPI dependency factory. Returns a dependency that checks the cookie-authed user has permission."""

    async def _dep(
        request: Request,
        current_user: AmlUser = Depends(require_auth),
        repo: CatalogRepository = Depends(get_catalog_repository),
    ) -> AmlUser:
        service = RbacService(repo)
        ensure_permission_for_user(service, current_user, permission, request)
        return current_user

    return _dep
