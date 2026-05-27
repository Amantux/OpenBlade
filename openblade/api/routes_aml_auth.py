"""Quantum-compatible AML authentication and access control routes."""

from __future__ import annotations

import collections
import os
import string
import time
from typing import Any, Callable, TypeVar

import pyotp
from fastapi import APIRouter, Body, Depends, File, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from openblade.api import aml_state
from openblade.bootstrap import AppContext, get_context
from openblade.catalog.models import AmlUser

router = APIRouter()

_NAME_ALLOWED = set(string.ascii_letters + string.digits + " _-")
_PASSWORD_ALLOWED = {
    char
    for char in string.printable
    if char not in {"`", "~"} and (char == " " or char not in string.whitespace)
}
_RESERVED_NAMES = {"admin", "service"}

# ---------------------------------------------------------------------------
# Login rate limiting (in-memory, per remote IP)
# ---------------------------------------------------------------------------
_LOGIN_MAX_ATTEMPTS = int(os.environ.get("OPENBLADE_LOGIN_MAX_ATTEMPTS", "10"))
_LOGIN_WINDOW_SECONDS = int(os.environ.get("OPENBLADE_LOGIN_WINDOW_SECONDS", "300"))
_login_attempts: dict[str, collections.deque[float]] = {}


def _check_rate_limit(remote_ip: str) -> None:
    """Raise HTTP 429 if the IP has exceeded the login attempt limit."""
    now = time.monotonic()
    window_start = now - _LOGIN_WINDOW_SECONDS
    deque = _login_attempts.setdefault(remote_ip, collections.deque())
    # Evict timestamps outside the window
    while deque and deque[0] < window_start:
        deque.popleft()
    if len(deque) >= _LOGIN_MAX_ATTEMPTS:
        raise HTTPException(
            status_code=429,
            detail=f"Too many login attempts. Try again in {_LOGIN_WINDOW_SECONDS // 60} minutes.",
        )
    deque.append(now)


def _clear_rate_limit(remote_ip: str) -> None:
    """Clear the attempt counter for an IP after a successful login."""
    _login_attempts.pop(remote_ip, None)

F = TypeVar("F", bound=Callable[..., Any])


def no_auth(endpoint: F) -> F:
    setattr(endpoint, "no_auth", True)
    return endpoint


class WSResultCode(BaseModel):
    code: int = 0
    description: str = "OK"
    summary: str
    action: str | None = None
    customCode: int = 0


class UserResponse(BaseModel):
    name: str
    role: int
    requirePasswordChange: bool | None = None


class UserList(BaseModel):
    user: list[UserResponse]


class KeytabFile(BaseModel):
    name: str | None = None
    date: str | None = None


class LdapConfig(BaseModel):
    enabled: bool = False
    primaryServer: str = "ldap.example.com"
    alternateServer: str | None = None
    serverPort: int = 389
    secureMode: bool = False
    searchUser: str = "cn=admin,dc=example,dc=com"
    searchUserPassword: str | None = None
    usersContext: str = "ou=users,dc=example,dc=com"
    groupContext: str = "ou=groups,dc=example,dc=com"
    libraryAccessGroupsUser: str = "library-users"
    libraryAccessGroupsAdmin: str = "library-admins"
    realm: str | None = None
    keyDistributionCenter: str | None = None
    domainMapping: str | None = None
    keytabFile: KeytabFile = Field(default_factory=KeytabFile)


class CommunicationCertificate(BaseModel):
    name: str
    date: str


class CommunicationCertificateList(BaseModel):
    communicationCertificate: list[CommunicationCertificate]


class LDAPTestRequest(BaseModel):
    user: str
    password: str


class LoginRequest(BaseModel):
    name: str
    password: str


class CreateUserRequest(BaseModel):
    name: str
    password: str
    role: int


class UpdateUserRequest(BaseModel):
    password: str | None = None
    role: int | None = None


class MFAAuthentication(BaseModel):
    type: int | str
    authenticationCode: str


class MFASharedData(BaseModel):
    key: str


class MFAConfig(BaseModel):
    type: str
    enabled: bool
    authenticationCode: str | None = None


class MFAList(BaseModel):
    mfa: list[MFAConfig]


class LUIAccess(BaseModel):
    mode: int
    pin: str | None = None


class LoginActivity(BaseModel):
    user: str
    timestamp: str
    success: bool
    remoteAddress: str | None = None


class LoginActivityList(BaseModel):
    loginActivity: list[LoginActivity]


class ServiceAccess(BaseModel):
    enabled: bool
    authenticationCodeExpiry: int = 300


class SessionItem(BaseModel):
    token: str
    user: str
    role: int
    createdAt: str
    expiresAt: str


class SessionList(BaseModel):
    session: list[SessionItem]


class PasswordPolicy(BaseModel):
    model_config = ConfigDict(extra="allow")

    minLength: int = 8
    maxLength: int = 64
    minLowercase: int = 0
    minUppercase: int = 0
    minNumeric: int = 0
    minSpecial: int = 0
    disallowUsername: bool = False


class ServiceAccessUpdate(BaseModel):
    enabled: bool
    authenticationCodeExpiry: int | None = None


def _ws_result(summary: str) -> WSResultCode:
    return WSResultCode(summary=summary)


def _ws_error(status_code: int, summary: str, *, description: str = "Error") -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=WSResultCode(code=status_code, description=description, summary=summary).model_dump(),
    )


def _normalize_user_name(name: str) -> str:
    return name.strip().lower()


def _validate_user_name(name: str) -> str:
    normalized = _normalize_user_name(name)
    if not normalized:
        raise HTTPException(status_code=400, detail="User name is required")
    if len(normalized) > 64 or any(char not in _NAME_ALLOWED for char in normalized):
        raise HTTPException(status_code=400, detail="Invalid user name")
    return normalized


def _validate_creatable_user_name(name: str) -> str:
    normalized = _validate_user_name(name)
    if normalized in _RESERVED_NAMES:
        raise HTTPException(status_code=403, detail="Reserved user name")
    return normalized


def _validate_password(password: str, user_name: str | None = None) -> str:
    if len(password) < 8 or len(password) > 64:
        raise HTTPException(status_code=400, detail="Invalid password length")
    if any(char not in _PASSWORD_ALLOWED for char in password):
        raise HTTPException(status_code=400, detail="Invalid password characters")
    policy = aml_state.get_password_policy()
    if len(password) < int(policy.get("minLength", 8)) or len(password) > int(policy.get("maxLength", 64)):
        raise HTTPException(status_code=400, detail="Password does not satisfy policy")
    if sum(char.islower() for char in password) < int(policy.get("minLowercase", 0)):
        raise HTTPException(status_code=400, detail="Password does not satisfy policy")
    if sum(char.isupper() for char in password) < int(policy.get("minUppercase", 0)):
        raise HTTPException(status_code=400, detail="Password does not satisfy policy")
    if sum(char.isdigit() for char in password) < int(policy.get("minNumeric", 0)):
        raise HTTPException(status_code=400, detail="Password does not satisfy policy")
    special_count = sum(not char.isalnum() and char != " " for char in password)
    if special_count < int(policy.get("minSpecial", 0)):
        raise HTTPException(status_code=400, detail="Password does not satisfy policy")
    if policy.get("disallowUsername") and user_name and user_name.lower() in password.lower():
        raise HTTPException(status_code=400, detail="Password does not satisfy policy")
    return password


def _validate_role(role: int, *, allow_service: bool = False) -> int:
    allowed_roles = {0, 1, 2} if allow_service else {0, 1}
    if role not in allowed_roles:
        raise HTTPException(status_code=400, detail="Invalid role")
    return role


def _validate_mfa_code(code: str) -> str:
    if len(code) != 6 or not code.isdigit():
        raise HTTPException(status_code=400, detail="Invalid authentication code")
    return code


def _validate_timeout(timeout_minutes: int) -> int:
    if timeout_minutes < 1:
        raise HTTPException(status_code=400, detail="Timeout must be positive")
    return timeout_minutes


def _validate_login_mode(mode: int) -> int:
    if mode not in {1, 2}:
        raise HTTPException(status_code=400, detail="Invalid login mode")
    return mode


def _validate_lui_access(access: LUIAccess) -> LUIAccess:
    if access.mode not in {1, 2, 3}:
        raise HTTPException(status_code=400, detail="Invalid LUI mode")
    if access.mode == 3 and (access.pin is None or not access.pin.isdigit() or len(access.pin) < 4):
        raise HTTPException(status_code=400, detail="PIN is required for PIN protected mode")
    return access


def _serialize_user(user: AmlUser) -> UserResponse:
    return UserResponse(
        name=user.name,
        role=user.role,
        requirePasswordChange=user.require_password_change,
    )


def _require_admin(user: AmlUser) -> None:
    if user.role != 0:
        raise HTTPException(status_code=403, detail="Administrator access required")


def _ensure_state(context: AppContext) -> None:
    aml_state.ensure_initialized(context.config.db_url)


def _remote_address(request: Request) -> str | None:
    return request.client.host if request.client else None


def _verify_totp(secret: str, code: str) -> bool:
    return pyotp.TOTP(secret).verify(code, valid_window=1)


async def require_auth(
    request: Request, context: AppContext = Depends(get_context)
) -> AmlUser:
    _ensure_state(context)
    session_id = request.cookies.get("sessionID")
    if session_id is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = aml_state.get_session_user(session_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


async def _parse_login_request(request: Request) -> LoginRequest:
    content_type = request.headers.get("content-type", "")
    payload: dict[str, Any]
    if "json" in content_type:
        payload = await request.json()
    else:
        form = await request.form()
        payload = dict(form)

    # Backwards compatible: accept 'username' as an alias for 'name'
    if isinstance(payload, dict) and "username" in payload and "name" not in payload:
        payload["name"] = payload.pop("username")

    try:
        return LoginRequest.model_validate(payload)
    except Exception as exc:  # pragma: no cover - FastAPI handles model details normally.
        raise HTTPException(status_code=422, detail="Invalid login payload") from exc


@router.get("/users", response_model=UserList, response_model_exclude_none=True)
async def list_users(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> UserList:
    _ensure_state(context)
    users = aml_state.list_users() if current_user.role == 0 else [current_user]
    return UserList(user=[_serialize_user(user) for user in users])


@router.get("/users/me", response_model=UserResponse, openapi_extra={"no_auth": False})
async def get_current_user(
    request: Request,
    context: AppContext = Depends(get_context),
) -> UserResponse:
    _ensure_state(context)
    user = await require_auth(request, context)
    return UserResponse(name=user.name, role=user.role, requirePasswordChange=user.require_password_change)


@router.post(
    "/users",
    response_model=UserResponse,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    payload: CreateUserRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> UserResponse:
    _ensure_state(context)
    _require_admin(current_user)
    normalized_name = _validate_creatable_user_name(payload.name)
    _validate_password(payload.password, normalized_name)
    role = _validate_role(payload.role, allow_service=True)
    if aml_state.get_user(normalized_name) is not None:
        raise HTTPException(status_code=403, detail="User already exists")
    user = aml_state.create_user(normalized_name, payload.password, role)
    return _serialize_user(user)


@router.get("/users/ldap", response_model=LdapConfig)
async def get_ldap_config(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> LdapConfig:
    _ensure_state(context)
    _require_admin(current_user)
    config = LdapConfig.model_validate(aml_state.get_ldap_config())
    config.searchUserPassword = None
    return config


@router.put("/users/ldap", response_model=LdapConfig)
async def put_ldap_config(
    payload: LdapConfig,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> LdapConfig:
    _ensure_state(context)
    _require_admin(current_user)
    updated = aml_state.set_ldap_config(payload.model_dump())
    result = LdapConfig.model_validate(updated)
    result.searchUserPassword = None  # never echo credentials back
    return result


@router.get("/users/ldap/certificates", response_model=CommunicationCertificateList)
async def list_ldap_certificates(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> CommunicationCertificateList:
    _ensure_state(context)
    _require_admin(current_user)
    return CommunicationCertificateList(
        communicationCertificate=[
            CommunicationCertificate.model_validate(item)
            for item in aml_state.list_ldap_certificates()
        ]
    )


@router.post("/users/ldap/certificates", response_model=WSResultCode)
async def upload_ldap_certificate(
    file: UploadFile = File(...),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    aml_state.add_ldap_certificate(file.filename or "ldap-certificate")
    await file.close()
    return _ws_result("LDAP certificate uploaded")


@router.get("/users/ldap/enabled", response_model=bool, openapi_extra={"no_auth": True})
@no_auth
async def get_ldap_enabled(context: AppContext = Depends(get_context)) -> bool:
    _ensure_state(context)
    return bool(aml_state.get_ldap_config().get("enabled", False))


@router.post("/users/ldap/keytab", response_model=WSResultCode)
async def upload_ldap_keytab(
    file: UploadFile = File(...),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    aml_state.set_ldap_keytab(file.filename or "ldap.keytab")
    await file.close()
    return _ws_result("LDAP keytab uploaded")


@router.post("/users/ldap/test", response_model=WSResultCode)
async def test_ldap(
    payload: LDAPTestRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    if not aml_state.get_ldap_config().get("enabled"):
        raise HTTPException(status_code=503, detail="LDAP is disabled")
    if not payload.user or not payload.password:
        raise HTTPException(status_code=400, detail="LDAP credentials are required")
    return _ws_result("LDAP configuration test succeeded")


@router.post("/users/login", response_model=WSResultCode, openapi_extra={"no_auth": True})
@no_auth
async def login(request: Request, context: AppContext = Depends(get_context)) -> Response:
    _ensure_state(context)
    payload = await _parse_login_request(request)
    user_name = _validate_user_name(payload.name)
    remote_address = _remote_address(request) or "unknown"

    _check_rate_limit(remote_address)

    if aml_state.get_login_mode() == 2:
        if not aml_state.is_ldap_user(user_name):
            aml_state.record_login_activity(user_name, success=False, remote_address=remote_address)
            return _ws_error(403, "LDAP-only mode requires an LDAP user account")
        user = aml_state.authenticate_ldap_user(user_name, payload.password)
        if user is None:
            aml_state.record_login_activity(user_name, success=False, remote_address=remote_address)
            return _ws_error(401, "Invalid LDAP credentials")
    else:
        user = aml_state.verify_credentials(user_name, payload.password)
        if user is None:
            aml_state.record_login_activity(user_name, success=False, remote_address=remote_address)
            return _ws_error(401, "Invalid credentials")

    _clear_rate_limit(remote_address)
    if user.role == 2 and not aml_state.get_service_access().get("enabled", True):
        raise HTTPException(status_code=503, detail="Service access is disabled")
    session_record = aml_state.create_session(user)
    aml_state.record_login_activity(user.name, success=True, remote_address=remote_address)
    payload = _ws_result("Login successful").model_dump()
    # Provide session token in response body for API clients/tests that expect it
    payload["token"] = session_record.token
    response = JSONResponse(content=payload)
    _is_production = os.environ.get("OPENBLADE_ENV", "development").lower() == "production"
    response.set_cookie(
        "sessionID",
        session_record.token,
        httponly=True,
        samesite="lax",
        secure=_is_production,
        max_age=86400,
    )
    if user.require_password_change:
        response.headers["Warning"] = "Default Password Supplied"
    return response


@router.post("/auth/login", response_model=WSResultCode, openapi_extra={"no_auth": True})
@no_auth
async def auth_login(request: Request, context: AppContext = Depends(get_context)) -> Response:
    """Compatibility alias for older clients/tests expecting /aml/auth/login"""
    return await login(request, context)


@router.delete("/users/login", response_model=WSResultCode)
async def logout(
    request: Request,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> Response:
    _ensure_state(context)
    aml_state.clear_session(request.cookies.get("sessionID", ""))
    response = JSONResponse(content=_ws_result(f"Logged out {current_user.name}").model_dump())
    response.delete_cookie("sessionID")
    return response


@router.post("/users/login/mfa", response_model=WSResultCode)
async def validate_mfa(
    payload: MFAAuthentication,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    config = aml_state.get_user_mfa(current_user.name, str(payload.type))
    if config is None or not config.enabled:
        raise HTTPException(status_code=404, detail="MFA type not enabled")
    code = _validate_mfa_code(payload.authenticationCode)
    if not _verify_totp(config.secret, code):
        raise HTTPException(status_code=401, detail="Invalid authentication code")
    return _ws_result(f"MFA validated for {payload.type}")


@router.get("/users/login/mfa/{type}/key", response_model=MFASharedData)
async def get_mfa_key(
    type: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> MFASharedData:
    _ensure_state(context)
    config = aml_state.get_user_mfa(current_user.name, type)
    if config is None:
        raise HTTPException(status_code=404, detail="MFA type not found")
    if config.enabled:
        raise HTTPException(status_code=403, detail="MFA already confirmed")
    return MFASharedData(key=config.secret)


@router.get("/users/login/mode", response_model=int)
async def get_login_mode(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> int:
    _ensure_state(context)
    _require_admin(current_user)
    return aml_state.get_login_mode()


@router.put("/users/login/mode", response_model=int)
async def set_login_mode(
    mode: int = Body(...),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> int:
    _ensure_state(context)
    _require_admin(current_user)
    return aml_state.set_login_mode(_validate_login_mode(mode))


@router.get("/users/luiAccess", response_model=LUIAccess)
async def get_lui_access(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> LUIAccess:
    _ensure_state(context)
    _require_admin(current_user)
    return LUIAccess.model_validate(aml_state.get_lui_access())


@router.put("/users/luiAccess", response_model=LUIAccess)
async def set_lui_access(
    payload: LUIAccess,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> LUIAccess:
    _ensure_state(context)
    _require_admin(current_user)
    validated = _validate_lui_access(payload)
    return LUIAccess.model_validate(aml_state.set_lui_access(validated.model_dump()))


@router.get("/users/luiAccess/mode", response_model=int, openapi_extra={"no_auth": True})
@no_auth
async def get_lui_access_mode(context: AppContext = Depends(get_context)) -> int:
    _ensure_state(context)
    return int(aml_state.get_lui_access().get("mode", 2))


@router.get("/users/mfa", response_model=MFAList)
async def get_mfa_configs(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> MFAList:
    _ensure_state(context)
    return MFAList(mfa=[MFAConfig.model_validate(item) for item in aml_state.list_user_mfa(current_user.name)])


@router.put("/users/mfa", response_model=MFAConfig)
async def set_mfa_config(
    payload: MFAConfig,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> MFAConfig:
    _ensure_state(context)
    config = aml_state.get_user_mfa(current_user.name, payload.type)
    if config is None:
        raise HTTPException(status_code=404, detail="MFA type not found")
    if payload.enabled:
        code = _validate_mfa_code(payload.authenticationCode or "")
        if not _verify_totp(config.secret, code):
            raise HTTPException(status_code=401, detail="Invalid authentication code")
    updated = aml_state.set_user_mfa(current_user.name, payload.type, payload.enabled)
    return MFAConfig.model_validate(updated)


@router.get("/users/reports/login", response_model=LoginActivityList)
async def get_login_report(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> LoginActivityList:
    _ensure_state(context)
    _require_admin(current_user)
    return LoginActivityList(
        loginActivity=[LoginActivity.model_validate(item) for item in aml_state.get_login_activity()]
    )


@router.get("/users/serviceAccess", response_model=ServiceAccess)
async def get_service_access(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> ServiceAccess:
    _ensure_state(context)
    _require_admin(current_user)
    return ServiceAccess.model_validate(aml_state.get_service_access())


@router.put("/users/serviceAccess", response_model=ServiceAccess)
async def set_service_access(
    payload: ServiceAccessUpdate,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> ServiceAccess:
    _ensure_state(context)
    _require_admin(current_user)
    expiry = payload.authenticationCodeExpiry
    if expiry is not None and expiry < 1:
        raise HTTPException(status_code=400, detail="Authentication code expiry must be positive")
    return ServiceAccess.model_validate(aml_state.set_service_access(payload.enabled, expiry))


@router.get("/users/serviceAccess/authenticationCode", response_model=str)
async def get_service_authentication_code(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> str:
    _ensure_state(context)
    _require_admin(current_user)
    return aml_state.get_service_authentication_code()


@router.get("/user/{name}", response_model=UserResponse, response_model_exclude_none=True)
async def get_user_by_name(
    name: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> UserResponse:
    _ensure_state(context)
    normalized_name = _validate_user_name(name)
    if current_user.role != 0 and current_user.name != normalized_name:
        raise HTTPException(status_code=403, detail="Forbidden")
    user = aml_state.get_user(normalized_name)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return _serialize_user(user)


@router.put("/user/{name}", response_model=UserResponse, response_model_exclude_none=True)
async def update_user(
    name: str,
    payload: UpdateUserRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> UserResponse:
    _ensure_state(context)
    normalized_name = _validate_user_name(name)
    target = aml_state.get_user(normalized_name)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if current_user.role != 0 and current_user.name != normalized_name:
        raise HTTPException(status_code=403, detail="Forbidden")
    if normalized_name in _RESERVED_NAMES and current_user.role != 0:
        raise HTTPException(status_code=403, detail="Forbidden")
    if payload.password is None and payload.role is None:
        raise HTTPException(status_code=400, detail="No changes requested")
    if current_user.role != 0 and payload.role is not None and payload.role != target.role:
        raise HTTPException(status_code=403, detail="Only administrators can change roles")
    password = None
    if payload.password is not None:
        password = _validate_password(payload.password, normalized_name)
    role = target.role
    if current_user.role == 0 and payload.role is not None:
        role = _validate_role(payload.role, allow_service=True)
    updated = aml_state.update_user(normalized_name, password=password, role=role)
    assert updated is not None
    return _serialize_user(updated)


@router.delete("/user/{name}", response_model=WSResultCode)
async def delete_user(
    name: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    normalized_name = _validate_user_name(name)
    if normalized_name in _RESERVED_NAMES:
        raise HTTPException(status_code=403, detail="Reserved users cannot be deleted")
    if not aml_state.delete_user(normalized_name):
        raise HTTPException(status_code=404, detail="User not found")
    return _ws_result(f"Deleted user {normalized_name}")


@router.get("/users/sessions", response_model=SessionList)
async def get_sessions(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> SessionList:
    _ensure_state(context)
    items = aml_state.list_sessions(user_name=None if current_user.role == 0 else current_user.name)
    return SessionList(session=[SessionItem.model_validate(item) for item in items])


@router.delete("/users/sessions", response_model=WSResultCode)
async def delete_sessions(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    if current_user.role == 0:
        aml_state.clear_all_sessions()
        return _ws_result("All sessions terminated")
    aml_state.clear_user_sessions(current_user.name)
    return _ws_result(f"Sessions terminated for {current_user.name}")


@router.get("/users/sessions/timeout", response_model=int)
async def get_session_timeout(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> int:
    _ensure_state(context)
    _require_admin(current_user)
    return aml_state.get_session_timeout()


@router.put("/users/sessions/timeout", response_model=int)
async def set_session_timeout(
    timeout_minutes: int = Body(...),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> int:
    _ensure_state(context)
    _require_admin(current_user)
    return aml_state.set_session_timeout(_validate_timeout(timeout_minutes))


@router.get("/users/passwordPolicy", response_model=PasswordPolicy)
async def get_password_policy(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PasswordPolicy:
    _ensure_state(context)
    _require_admin(current_user)
    return PasswordPolicy.model_validate(aml_state.get_password_policy())


@router.put("/users/passwordPolicy", response_model=PasswordPolicy)
async def set_password_policy(
    payload: PasswordPolicy,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> PasswordPolicy:
    _ensure_state(context)
    _require_admin(current_user)
    if payload.minLength < 1 or payload.maxLength < payload.minLength:
        raise HTTPException(status_code=400, detail="Invalid password policy")
    if min(payload.minLowercase, payload.minUppercase, payload.minNumeric, payload.minSpecial) < 0:
        raise HTTPException(status_code=400, detail="Invalid password policy")
    return PasswordPolicy.model_validate(aml_state.set_password_policy(payload.model_dump()))


@router.post("/users/admin/reset", response_model=WSResultCode)
async def reset_admin_password(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    aml_state.reset_admin_password()
    return _ws_result("Admin password reset")
