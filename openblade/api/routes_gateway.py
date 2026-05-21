"""REST API for managing the OpenBlade protocol gateway (SFTP/SCP)."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from openblade.api.routes_aml_auth import require_auth
from openblade.nas.protocol_gateway import InboxPath, get_gateway

router = APIRouter(prefix="/api/gateway", tags=["gateway"])


class CredentialCreate(BaseModel):
    username: str
    password: str
    allowed_paths: Optional[List[InboxPath]] = None


class CredentialUpdate(BaseModel):
    password: Optional[str] = None
    enabled: Optional[bool] = None
    allowed_paths: Optional[List[InboxPath]] = None


class GatewayConfigResponse(BaseModel):
    bind_host: str
    bind_port: int
    max_sessions: int
    inbox_root: str
    status: str


@router.get("/config", response_model=GatewayConfigResponse, dependencies=[Depends(require_auth)])
def get_gateway_config():
    return get_gateway().config


@router.get("/status", dependencies=[Depends(require_auth)])
def get_gateway_status():
    return get_gateway().get_stats()


@router.post("/start", dependencies=[Depends(require_auth)])
def start_gateway():
    gw = get_gateway()
    gw.start()
    return {"status": gw.status}


@router.post("/stop", dependencies=[Depends(require_auth)])
def stop_gateway():
    gw = get_gateway()
    gw.stop()
    return {"status": gw.status}


@router.get("/credentials", dependencies=[Depends(require_auth)])
def list_credentials():
    return get_gateway().list_credentials()


@router.post("/credentials", dependencies=[Depends(require_auth)])
def add_credential(data: CredentialCreate):
    try:
        cred = get_gateway().add_credential(
            data.username,
            data.password,
            [path.value for path in data.allowed_paths] if data.allowed_paths is not None else None,
        )
        return {
            "username": cred.username,
            "enabled": cred.enabled,
            "allowed_paths": cred.allowed_paths,
        }
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.put("/credentials/{username}", dependencies=[Depends(require_auth)])
def update_credential(username: str, data: CredentialUpdate):
    try:
        cred = get_gateway().update_credential(
            username,
            password=data.password,
            enabled=data.enabled,
            allowed_paths=[path.value for path in data.allowed_paths] if data.allowed_paths is not None else None,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not cred:
        raise HTTPException(404, f"Credential {username!r} not found")
    return {"username": cred.username, "enabled": cred.enabled, "allowed_paths": cred.allowed_paths}


@router.delete("/credentials/{username}", dependencies=[Depends(require_auth)])
def remove_credential(username: str):
    if not get_gateway().remove_credential(username):
        raise HTTPException(404, f"Credential {username!r} not found")
    return {"deleted": username}


@router.get("/sessions", dependencies=[Depends(require_auth)])
def list_sessions(active_only: bool = False):
    sessions = get_gateway().list_sessions(active_only=active_only)
    return [
        {
            "session_id": s.session_id,
            "username": s.username,
            "remote_addr": s.remote_addr,
            "connected_at": s.connected_at.isoformat(),
            "disconnected_at": s.disconnected_at.isoformat() if s.disconnected_at else None,
            "bytes_uploaded": s.bytes_uploaded,
            "files_uploaded": s.files_uploaded,
            "errors": s.errors,
            "uploads": [
                {
                    "requested_path": upload.requested_path,
                    "routed_path": upload.routed_path,
                    "bytes_uploaded": upload.bytes_uploaded,
                    "uploaded_at": upload.uploaded_at.isoformat(),
                }
                for upload in s.uploads
            ],
        }
        for s in sessions
    ]


@router.get("/inbox-paths", dependencies=[Depends(require_auth)])
def list_inbox_paths():
    """List available inbox path options for credential configuration."""
    return [{"path": path.value, "description": path.name.lower().replace("_", " ")} for path in InboxPath]
