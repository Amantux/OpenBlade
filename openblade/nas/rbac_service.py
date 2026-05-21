"""RBAC service for permission checks, user management, and audit emission."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import secrets
from typing import Any

import structlog

from openblade.catalog.repository import CatalogRepository
from openblade.nas.types import (
    CreateTokenRequest,
    CreateTokenResult,
    CreateUserRequest,
    RbacApiTokenRecord,
    RbacPermission,
    RbacUserRecord,
    UserSummary,
)

logger = structlog.get_logger(__name__)
_SENSITIVE_DETAIL_KEYS = frozenset({"password", "token", "raw_token", "hashed_password"})


class RbacService:
    """
    Handles RBAC enforcement, user/role/token management, and audit event emission.
    Never stores raw passwords or tokens. All token verification uses sha256 hash lookup.
    """

    def __init__(self, repo: CatalogRepository) -> None:
        """Inject repository dependency and seed built-in roles. No mutable class-level state."""
        self.repo = repo
        self.repo.seed_default_roles()

    def check_permission(self, user_id: str, permission: RbacPermission) -> bool:
        """Return True if user's role grants the given permission."""
        user = self._get_user(user_id)
        if user is None or not user.is_active:
            return False
        if user.is_admin:
            return True
        role = self.repo.get_role(user.role_id)
        if role is None:
            return False
        permissions = {RbacPermission(value) for value in list(role.get("permissions", []))}
        return permission in permissions

    def require_permission(self, user_id: str, permission: RbacPermission) -> None:
        """Raise PermissionError with safe message if permission not granted. Emit audit event."""
        if self.check_permission(user_id, permission):
            return
        user = self._get_user(user_id)
        self.emit_audit_event(
            event_type="permission_denied",
            user_id=user.id if user is not None else user_id,
            username=user.username if user is not None else "",
            resource="rbac",
            action=permission.value,
            outcome="denied",
            details={"permission": permission.value},
        )
        logger.warning("rbac_permission_denied", user_id=user_id, permission=permission.value)
        raise PermissionError("Permission denied")

    def authenticate_by_token(self, raw_token: str) -> RbacUserRecord | None:
        """Look up token by sha256 hash, check not revoked/expired, update last_used_at. Return user or None."""
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        token_payload = self.repo.get_api_token_by_hash(token_hash)
        if token_payload is None:
            return None
        token = RbacApiTokenRecord.model_validate(token_payload)
        if token.revoked or self._is_expired(token.expires_at):
            return None
        user = self._get_user(token.user_id)
        if user is None or not user.is_active:
            return None
        self.repo.update_token_last_used(token.id, self._utcnow_iso())
        return user

    def create_user(self, request: CreateUserRequest) -> UserSummary:
        """Hash password with PBKDF2-HMAC-SHA256, create user record, emit audit event."""
        if self.repo.get_role(request.role_id) is None:
            raise ValueError("Role not found")
        salt = secrets.token_hex(16)
        hashed = hashlib.pbkdf2_hmac(
            "sha256", request.password.encode(), salt.encode(), 260_000
        ).hex()
        created = self.repo.create_user(
            {
                "username": request.username,
                "hashed_password": f"pbkdf2$260000${salt}${hashed}",
                "role_id": request.role_id,
                "email": request.email,
                "full_name": request.full_name,
                "is_admin": request.is_admin,
            }
        )
        summary = UserSummary.model_validate(created)
        self.emit_audit_event(
            event_type="user_created",
            user_id=summary.id,
            username=summary.username,
            resource="rbac:user",
            action="create",
            outcome="success",
            details={"role_id": summary.role_id, "email": summary.email, "full_name": summary.full_name},
        )
        return summary

    def create_token(self, user_id: str, request: CreateTokenRequest) -> CreateTokenResult:
        """Generate secure random token, store hash only, emit audit event. Return CreateTokenResult with raw_token."""
        user = self._get_user(user_id)
        if user is None:
            raise ValueError("User not found")
        raw_token = secrets.token_hex(32)
        created = self.repo.create_api_token(
            {
                "user_id": user_id,
                "name": request.name,
                "token_hash": hashlib.sha256(raw_token.encode()).hexdigest(),
                "permissions": request.permissions,
                "expires_at": request.expires_at,
            }
        )
        token_record = RbacApiTokenRecord.model_validate(created)
        if token_record.id not in user.api_token_ids:
            self.repo.update_user(
                user.id,
                {"api_token_ids": [*user.api_token_ids, token_record.id]},
            )
        self.emit_audit_event(
            event_type="token_created",
            user_id=user.id,
            username=user.username,
            resource="rbac:token",
            action="create",
            outcome="success",
            details={
                "token_id": token_record.id,
                "name": token_record.name,
                "permissions": token_record.permissions,
                "expires_at": token_record.expires_at,
            },
        )
        return CreateTokenResult(token_id=token_record.id, raw_token=raw_token, token_record=token_record)

    def revoke_token(self, token_id: str, revoked_by_user_id: str) -> bool:
        """Mark token revoked, emit audit event."""
        actor = self._get_user(revoked_by_user_id)
        token_payload = self.repo.get_api_token(token_id)
        if token_payload is None:
            self.emit_audit_event(
                event_type="token_revoked",
                user_id=actor.id if actor is not None else revoked_by_user_id,
                username=actor.username if actor is not None else "",
                resource="rbac:token",
                action="revoke",
                outcome="not_found",
                details={"token_id": token_id},
            )
            return False
        revoked = self.repo.revoke_api_token(token_id)
        self.emit_audit_event(
            event_type="token_revoked",
            user_id=actor.id if actor is not None else revoked_by_user_id,
            username=actor.username if actor is not None else "",
            resource="rbac:token",
            action="revoke",
            outcome="success" if revoked else "failed",
            details={"token_id": token_id, "target_user_id": token_payload.get("user_id")},
        )
        return revoked

    def list_users(self, active_only: bool = False) -> list[UserSummary]:
        """Return UserSummary list — no hashed_password."""
        return [UserSummary.model_validate(user) for user in self.repo.list_users(active_only=active_only)]

    def get_user_summary(self, user_id: str) -> UserSummary | None:
        """Return UserSummary or None."""
        user = self.repo.get_user(user_id)
        if user is None:
            return None
        return UserSummary.model_validate(user)

    def deactivate_user(self, user_id: str, deactivated_by: str) -> bool:
        """Deactivate user, emit audit event."""
        actor = self._get_user(deactivated_by)
        deactivated = self.repo.deactivate_user(user_id)
        self.emit_audit_event(
            event_type="user_deactivated",
            user_id=actor.id if actor is not None else deactivated_by,
            username=actor.username if actor is not None else "",
            resource="rbac:user",
            action="deactivate",
            outcome="success" if deactivated else "not_found",
            details={"target_user_id": user_id},
        )
        return deactivated

    def emit_audit_event(
        self,
        event_type: str,
        user_id: str | None,
        username: str,
        resource: str,
        action: str,
        outcome: str,
        details: dict | None = None,
        ip_address: str | None = None,
    ) -> None:
        """Create audit event — strip any password/token fields from details before storing."""
        sanitized = self._sanitize_details(details or {})
        self.repo.create_audit_event(
            {
                "event_type": event_type,
                "user_id": user_id,
                "username": username,
                "resource": resource,
                "action": action,
                "outcome": outcome,
                "details": sanitized,
                "ip_address": ip_address,
            }
        )
        logger.info(
            "rbac_audit_event",
            event_type=event_type,
            user_id=user_id,
            username=username,
            resource=resource,
            action=action,
            outcome=outcome,
        )

    def _get_user(self, user_id: str) -> RbacUserRecord | None:
        payload = self.repo.get_user(user_id)
        if payload is None:
            return None
        return RbacUserRecord.model_validate(payload)

    def _is_expired(self, expires_at: str | None) -> bool:
        if expires_at is None:
            return False
        expires = self._parse_datetime(expires_at)
        return expires <= datetime.now(timezone.utc)

    def _parse_datetime(self, value: str) -> datetime:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _sanitize_details(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): self._sanitize_details(item)
                for key, item in value.items()
                if str(key) not in _SENSITIVE_DETAIL_KEYS
            }
        if isinstance(value, list):
            return [self._sanitize_details(item) for item in value]
        return value

    def _utcnow_iso(self) -> str:
        return datetime.utcnow().isoformat()
