"""Protocol Gateway — SFTP/SCP access layer for OpenBlade NAS inbox.

Provides isolated credential management and session auditing for
SFTP ingest. Gateway credentials are separate from web UI credentials.
"""
from __future__ import annotations

import hashlib
import hmac
import importlib.util
import os
import posixpath
import secrets
import socket
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional


class GatewayStatus(str, Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"
    DISABLED = "disabled"


class InboxPath(str, Enum):
    GENERAL = "/openblade/inbox"
    CRITICAL = "/openblade/inbox-critical"
    SHARDED = "/openblade/inbox-sharded"
    RESTORE = "/openblade/restore"


_INBOX_DIRECTORY_NAMES = {
    InboxPath.GENERAL: "inbox",
    InboxPath.CRITICAL: "inbox-critical",
    InboxPath.SHARDED: "inbox-sharded",
    InboxPath.RESTORE: "restore",
}


def _parse_enabled(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _normalize_allowed_paths(allowed_paths: Optional[List[str]]) -> List[str]:
    if not allowed_paths:
        return [InboxPath.GENERAL.value]
    normalized: list[str] = []
    seen: set[str] = set()
    for allowed_path in allowed_paths:
        value = allowed_path.value if isinstance(allowed_path, InboxPath) else str(allowed_path)
        if value not in InboxPath._value2member_map_:
            raise ValueError(f"Unsupported inbox path {value!r}")
        if value not in seen:
            normalized.append(value)
            seen.add(value)
    return normalized


def _normalize_gateway_path(path: str) -> str:
    if not path:
        raise ValueError("Path must not be empty")
    normalized = posixpath.normpath(path if path.startswith("/") else f"/{path}")
    if normalized == ".":
        normalized = "/"
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized


def _match_inbox_prefix(path: str, candidates: List[str]) -> Optional[str]:
    normalized = _normalize_gateway_path(path)
    for candidate in sorted((_normalize_gateway_path(item) for item in candidates), key=len, reverse=True):
        if normalized == candidate or normalized.startswith(f"{candidate}/"):
            return candidate
    return None


@dataclass
class GatewayCredential:
    username: str
    _hashed_password: str
    allowed_paths: List[str] = field(default_factory=lambda: [InboxPath.GENERAL.value])
    enabled: bool = True
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_seen_at: Optional[datetime] = None

    @classmethod
    def create(
        cls, username: str, password: str, allowed_paths: Optional[List[str]] = None
    ) -> "GatewayCredential":
        """Create a credential with a salted password hash."""
        return cls(
            username=username,
            _hashed_password=cls._hash_password(password),
            allowed_paths=_normalize_allowed_paths(allowed_paths),
        )

    @staticmethod
    def _hash_password(password: str) -> str:
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
        return f"{salt.hex()}:{digest.hex()}"

    def verify_password(self, password: str) -> bool:
        """Timing-safe password check."""
        try:
            salt_hex, digest_hex = self._hashed_password.split(":", 1)
            salt = bytes.fromhex(salt_hex)
        except ValueError:
            expected = hashlib.sha256(password.encode("utf-8")).hexdigest()
            return hmac.compare_digest(self._hashed_password, expected)
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000).hex()
        return hmac.compare_digest(digest_hex, candidate)


@dataclass
class GatewayUpload:
    requested_path: str
    routed_path: str
    bytes_uploaded: int
    uploaded_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class GatewaySession:
    session_id: str
    username: str
    remote_addr: str
    connected_at: datetime
    disconnected_at: Optional[datetime] = None
    bytes_uploaded: int = 0
    files_uploaded: int = 0
    errors: int = 0
    uploads: List[GatewayUpload] = field(default_factory=list)


class ProtocolGateway:
    """In-process protocol gateway manager for emulated SFTP/SCP ingest."""

    def __init__(self):
        self._credentials: Dict[str, GatewayCredential] = {}
        self._sessions: List[GatewaySession] = []
        self._last_error: Optional[str] = None
        self._bind_host = os.environ.get("OPENBLADE_SFTP_HOST", "0.0.0.0")
        self._bind_port = int(os.environ.get("OPENBLADE_SFTP_PORT", "2222"))
        self._max_sessions = int(os.environ.get("OPENBLADE_SFTP_MAX_SESSIONS", "10"))
        self._inbox_root = os.environ.get("OPENBLADE_INBOX_ROOT", "/var/lib/openblade")
        self._status = GatewayStatus.STOPPED if _parse_enabled(os.environ.get("OPENBLADE_SFTP_ENABLED")) else GatewayStatus.DISABLED

    @property
    def status(self) -> GatewayStatus:
        return self._status

    @property
    def config(self) -> dict:
        return {
            "bind_host": self._bind_host,
            "bind_port": self._bind_port,
            "max_sessions": self._max_sessions,
            "inbox_root": self._inbox_root,
            "status": self._status,
            "last_error": self._last_error,
        }

    def add_credential(
        self, username: str, password: str, allowed_paths: Optional[List[str]] = None
    ) -> GatewayCredential:
        if username in self._credentials:
            raise ValueError(f"Credential for {username!r} already exists")
        cred = GatewayCredential.create(username, password, allowed_paths)
        self._credentials[username] = cred
        return cred

    def remove_credential(self, username: str) -> bool:
        return bool(self._credentials.pop(username, None))

    def update_credential(
        self,
        username: str,
        password: Optional[str] = None,
        enabled: Optional[bool] = None,
        allowed_paths: Optional[List[str]] = None,
    ) -> Optional[GatewayCredential]:
        cred = self._credentials.get(username)
        if not cred:
            return None
        if password is not None:
            cred._hashed_password = GatewayCredential._hash_password(password)
        if enabled is not None:
            cred.enabled = enabled
        if allowed_paths is not None:
            cred.allowed_paths = _normalize_allowed_paths(allowed_paths)
        return cred

    def authenticate(self, username: str, password: str) -> Optional[GatewayCredential]:
        """Authenticate a gateway user. Returns credential or None."""
        cred = self._credentials.get(username)
        if not cred or not cred.enabled:
            return None
        if cred.verify_password(password):
            cred.last_seen_at = datetime.utcnow()
            return cred
        return None

    def check_path_allowed(self, username: str, path: str) -> bool:
        """Check if username is allowed to access the given inbox path."""
        cred = self._credentials.get(username)
        if not cred or not cred.enabled:
            return False
        return _match_inbox_prefix(path, cred.allowed_paths) is not None

    def route_upload_path(self, username: str, path: str) -> str:
        """Resolve a virtual SFTP path to an inbox path under the local inbox root."""
        cred = self._credentials.get(username)
        if not cred or not cred.enabled:
            raise PermissionError(f"Credential {username!r} is not available")
        normalized = _normalize_gateway_path(path)
        matched_prefix = _match_inbox_prefix(normalized, cred.allowed_paths)
        if matched_prefix is None:
            raise PermissionError(f"Path {normalized!r} is not allowed for {username!r}")
        suffix = normalized[len(matched_prefix) :].lstrip("/")
        inbox = InboxPath(matched_prefix)
        destination = self._inbox_root.rstrip("/")
        routed = posixpath.join(destination, _INBOX_DIRECTORY_NAMES[inbox])
        if suffix:
            routed = posixpath.join(routed, suffix)
        return routed

    def open_session(self, username: str, remote_addr: str) -> GatewaySession:
        if self._status is GatewayStatus.DISABLED:
            raise RuntimeError("Protocol gateway is disabled")
        active_sessions = self.list_sessions(active_only=True)
        if len(active_sessions) >= self._max_sessions:
            self.set_error("Maximum concurrent gateway sessions reached")
            raise RuntimeError("Maximum concurrent gateway sessions reached")
        if username not in self._credentials:
            raise KeyError(f"Credential {username!r} not found")
        session = GatewaySession(
            session_id=secrets.token_hex(16),
            username=username,
            remote_addr=remote_addr,
            connected_at=datetime.utcnow(),
        )
        self._sessions.append(session)
        return session

    def record_upload(self, session_id: str, requested_path: str, bytes_uploaded: int) -> GatewayUpload:
        session = self._get_session(session_id)
        if session is None:
            raise KeyError(f"Session {session_id!r} not found")
        routed_path = self.route_upload_path(session.username, requested_path)
        upload = GatewayUpload(
            requested_path=_normalize_gateway_path(requested_path),
            routed_path=routed_path,
            bytes_uploaded=bytes_uploaded,
        )
        session.uploads.append(upload)
        session.files_uploaded += 1
        session.bytes_uploaded += bytes_uploaded
        return upload

    def close_session(
        self,
        session_id: str,
        bytes_uploaded: int = 0,
        files_uploaded: int = 0,
        errors: int = 0,
    ) -> None:
        session = self._get_session(session_id)
        if session is None:
            return
        session.disconnected_at = datetime.utcnow()
        session.bytes_uploaded = max(session.bytes_uploaded, bytes_uploaded)
        session.files_uploaded = max(session.files_uploaded, files_uploaded)
        session.errors = errors

    def list_sessions(self, active_only: bool = False) -> List[GatewaySession]:
        if active_only:
            return [s for s in self._sessions if s.disconnected_at is None]
        return list(self._sessions)

    def list_credentials(self) -> List[dict]:
        return [
            {
                "username": c.username,
                "enabled": c.enabled,
                "allowed_paths": list(c.allowed_paths),
                "created_at": c.created_at.isoformat(),
                "last_seen_at": c.last_seen_at.isoformat() if c.last_seen_at else None,
            }
            for c in self._credentials.values()
        ]

    def start(self) -> None:
        """Validate the SFTP runtime and mark the gateway as started."""
        if self._status is GatewayStatus.DISABLED:
            return
        if self._status is GatewayStatus.RUNNING:
            return
        try:
            if importlib.util.find_spec("asyncssh") is None:
                raise RuntimeError("SFTP gateway backend is unavailable because asyncssh is not installed")
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((self._bind_host, self._bind_port))
            self._last_error = None
            self._status = GatewayStatus.RUNNING
        except OSError as exc:
            message = f"Unable to start SFTP gateway on {self._bind_host}:{self._bind_port}: {exc}"
            self.set_error(message)
            raise RuntimeError(message) from exc
        except RuntimeError as exc:
            self.set_error(str(exc))
            raise

    def stop(self) -> None:
        """Mark gateway as stopped."""
        if self._status is GatewayStatus.DISABLED:
            return
        self._status = GatewayStatus.STOPPED

    def disable(self) -> None:
        self._status = GatewayStatus.DISABLED

    def set_error(self, message: str) -> None:
        self._last_error = message
        self._status = GatewayStatus.ERROR

    def get_stats(self) -> dict:
        sessions = self._sessions
        active = [s for s in sessions if s.disconnected_at is None]
        return {
            "status": self._status.value,
            "total_sessions": len(sessions),
            "active_sessions": len(active),
            "total_files_uploaded": sum(s.files_uploaded for s in sessions),
            "total_bytes_uploaded": sum(s.bytes_uploaded for s in sessions),
            "credentials_count": len(self._credentials),
            "last_error": self._last_error,
        }

    def _get_session(self, session_id: str) -> Optional[GatewaySession]:
        for session in self._sessions:
            if session.session_id == session_id:
                return session
        return None


_gateway = ProtocolGateway()


def get_gateway() -> ProtocolGateway:
    return _gateway
