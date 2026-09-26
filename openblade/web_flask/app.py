from __future__ import annotations

import json
import os
import re
import secrets
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict
from datetime import timedelta
from ipaddress import ip_address
from pathlib import PurePosixPath
from urllib.parse import urlparse

from flask import (
    Flask,
    Response,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    stream_with_context,
    url_for,
)
from flask.typing import ResponseReturnValue

from .client import BackendClient, BackendError
from .models import Device

MODEL_OPTIONS: tuple[str, ...] = ("Scalar i3", "Scalar i6", "Scalar i6H")
ROLE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("primary", "Primary Archive"),
    ("archive", "Deep Archive"),
    ("cold_storage", "Cold Storage (Offline)"),
)
MANAGE_SECTIONS: tuple[tuple[str, str], ...] = (
    ("overview", "Overview"),
    ("inventory", "Inventory"),
    ("drives", "Drives"),
    ("partitions", "Partitions"),
    ("jobs", "Jobs"),
    ("operations", "Operations"),
    ("reports", "Reports"),
    ("settings", "Settings"),
)
STORAGE_SECTIONS: tuple[tuple[str, str], ...] = (
    ("overview", "Overview"),
    ("access", "Client Access"),
    ("write-path", "Write Path"),
    ("archive", "Archive"),
    ("catalog", "Catalog"),
    ("restore", "Restore"),
)
NAS_NAV: tuple[tuple[str, str], ...] = (
    ("Storage", "/storage"),
    ("Jobs", "/jobs"),
    ("Reports", "/reports"),
    ("System", "/system"),
)

_ASCII_PRINTABLE = re.compile(r"^[\x20-\x7E]{1,64}$")
_SERIAL_ALLOWED = re.compile(r"^[A-Za-z0-9-]{1,32}$")
_DEVICE_USERNAME_ALLOWED = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_OPERATION_TOKEN = re.compile(r"^[A-Za-z0-9,._:-]{1,64}$")
_BARCODE_TOKEN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_POOL_ID_ALLOWED = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
_SHARE_NAME_ALLOWED = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_JOB_TYPES = ("archive", "restore", "format", "verify", "inventory", "import", "export")
_JOB_STATES = ("pending", "running", "completed", "failed", "failed_recoverable", "cancelled")
_AML_ROLE_OPTIONS: tuple[tuple[int, str], ...] = (
    (0, "Administrator"),
    (1, "Operator"),
    (2, "Service"),
)
_POLICY_TYPE_OPTIONS = ("balanced", "critical_sequential", "noncritical_sharded")
_INGEST_MODE_OPTIONS = ("cache_drive", "source_stream")
_SHARD_STRATEGY_OPTIONS = (
    "round_robin",
    "capacity_weighted",
    "directory_batch",
    "hash_prefix",
    "restore_parallelism_optimized",
)
_CACHE_EVICTION_OPTIONS = ("never", "after_verified", "after_days", "lru", "manual")
_SOURCE_CHECKSUM_MODES = ("precompute", "streaming", "post_verify", "precompute_and_post_verify")
_SHARE_TYPE_OPTIONS = ("inbox", "restore", "catalog", "virtual", "pool")
_SHARE_ACCESS_OPTIONS = ("read_only", "read_write")
_MAX_PATH_LENGTH = 512
_LOGIN_WINDOW_SECONDS = 300
_LOGIN_MAX_ATTEMPTS = 8
_LOGIN_LOCK_SECONDS = 120
_DANGEROUS_DEVICE_HOSTS = {
    "localhost",
    "127.0.0.1",
    "::1",
    "0.0.0.0",
    "169.254.169.254",
    "metadata.google.internal",
    "metadata.aws.internal",
}


def _bool_env(name: str, *, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _secret_key() -> str:
    configured = os.environ.get("FLASK_SECRET_KEY")
    if configured:
        return configured
    if os.environ.get("OPENBLADE_ENV", "development").lower() == "production":
        raise RuntimeError("FLASK_SECRET_KEY is required in production")
    return secrets.token_hex(32)


def _backend_client_factory() -> BackendClient:
    return BackendClient(
        base_url=os.environ.get("OPENBLADE_WEB_BACKEND_URL", "http://127.0.0.1:8000"),
        timeout_seconds=float(os.environ.get("OPENBLADE_WEB_TIMEOUT_SECONDS", "8")),
        service_token=os.environ.get("OPENBLADE_SERVICE_TOKEN"),
        # Must equal the backend's OPENBLADE_API_TOKEN. Without it every
        # OpenBlade-native endpoint 401s the moment an operator enables native
        # auth -- see BackendClient._headers for why the session token cannot
        # serve double duty here.
        api_token=os.environ.get("OPENBLADE_API_TOKEN"),
    )


def _ensure_csrf_token() -> str:
    token = session.get("_csrf_token")
    if isinstance(token, str) and token:
        return token
    token = secrets.token_urlsafe(32)
    session["_csrf_token"] = token
    return token


def _validate_csrf() -> bool:
    expected = session.get("_csrf_token")
    supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    if not isinstance(expected, str) or not isinstance(supplied, str):
        return False
    return secrets.compare_digest(expected, supplied)


def _is_login_required(endpoint: str | None) -> bool:
    if endpoint is None:
        return True
    return endpoint not in {"login", "static"}


def _parse_int(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _client_ip() -> str:
    """Identify the caller for login rate limiting and security logging.

    The peer address only. ``X-Forwarded-For`` is deliberately ignored, matching
    ``openblade.api.routes_assist.client_key``: it is caller-controlled, so
    honouring it would let one client mint unlimited identities and defeat the
    login lockout in ``_is_login_blocked`` entirely. Deploy behind a reverse proxy
    that rate-limits on its own if you need per-real-client accounting.
    """
    return request.remote_addr or "unknown"


def _is_safe_redirect_target(target: str) -> bool:
    if not target or not target.startswith("/") or target.startswith("//"):
        return False
    if any(ord(char) < 0x20 for char in target):
        return False
    parsed = urlparse(target)
    return not (parsed.scheme or parsed.netloc)


def _validate_device_url(connection_url: str) -> str | None:
    parsed_url = urlparse(connection_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        return "Connection URL must start with http:// or https://"
    if parsed_url.username or parsed_url.password:
        return "Connection URL must not include embedded credentials."
    hostname = (parsed_url.hostname or "").strip().lower()
    if not hostname:
        return "Connection URL must include a hostname."
    allow_unsafe_targets = _bool_env("OPENBLADE_WEB_ALLOW_UNSAFE_DEVICE_TARGETS", default=False)
    if not allow_unsafe_targets and hostname in _DANGEROUS_DEVICE_HOSTS:
        return "Connection URL host is not allowed by web security policy."
    try:
        ip_value = ip_address(hostname)
    except ValueError:
        ip_value = None
    if (
        ip_value is not None
        and not allow_unsafe_targets
        and (
            ip_value.is_loopback
            or ip_value.is_link_local
            or ip_value.is_unspecified
            or ip_value.is_multicast
        )
    ):
        return "Connection URL host is not allowed by web security policy."
    return None


def _is_safe_storage_path(path: str) -> bool:
    if not path or len(path) > _MAX_PATH_LENGTH or "\x00" in path:
        return False
    if not path.startswith("/"):
        return False
    return ".." not in PurePosixPath(path).parts


def _attempt_bucket(app: Flask) -> dict[str, dict[str, object]]:
    bucket = app.extensions.setdefault("login_attempts", {})
    if isinstance(bucket, dict):
        return bucket
    raise RuntimeError("login attempt bucket is misconfigured")


def _record_login_failure(app: Flask, key: str) -> None:
    now = time.monotonic()
    bucket = _attempt_bucket(app)
    entry = bucket.get(key)
    if not isinstance(entry, dict):
        entry = {"attempts": [], "blocked_until": 0.0}
        bucket[key] = entry
    attempts = entry.get("attempts")
    if not isinstance(attempts, list):
        attempts = []
        entry["attempts"] = attempts
    attempts.append(now)
    window_start = now - _LOGIN_WINDOW_SECONDS
    attempts[:] = [item for item in attempts if isinstance(item, float) and item >= window_start]
    if len(attempts) >= _LOGIN_MAX_ATTEMPTS:
        entry["blocked_until"] = now + _LOGIN_LOCK_SECONDS


def _is_login_blocked(app: Flask, key: str) -> bool:
    now = time.monotonic()
    bucket = _attempt_bucket(app)
    stale_cutoff = now - (_LOGIN_WINDOW_SECONDS * 2)
    stale_keys: list[str] = []
    for bucket_key, entry in bucket.items():
        if not isinstance(entry, dict):
            stale_keys.append(bucket_key)
            continue
        blocked_until = entry.get("blocked_until")
        attempts = entry.get("attempts")
        if not isinstance(blocked_until, float) or not isinstance(attempts, list):
            stale_keys.append(bucket_key)
            continue
        filtered = [item for item in attempts if isinstance(item, float) and item >= stale_cutoff]
        entry["attempts"] = filtered
        if not filtered and blocked_until < now:
            stale_keys.append(bucket_key)
    for stale in stale_keys:
        bucket.pop(stale, None)

    current_entry = bucket.get(key)
    if not isinstance(current_entry, dict):
        return False
    blocked_until = current_entry.get("blocked_until")
    return isinstance(blocked_until, float) and blocked_until > now


def _clear_login_failures(app: Flask, key: str) -> None:
    _attempt_bucket(app).pop(key, None)


def _role_label(role: str) -> str:
    for key, label in ROLE_OPTIONS:
        if key == role:
            return label
    return "Unknown Role"


def _aml_role_label(role: int | str) -> str:
    try:
        normalized = int(role)
    except (TypeError, ValueError):
        return "Unknown Role"
    for key, label in _AML_ROLE_OPTIONS:
        if key == normalized:
            return label
    return "Unknown Role"


def _status_class(status: str) -> str:
    normalized = status.lower()
    if normalized in {"online", "good", "healthy"}:
        return "status-online"
    if normalized in {"degraded", "warning"}:
        return "status-warning"
    if normalized in {"running", "active"}:
        return "status-running"
    if normalized in {"offline", "error", "failed", "critical", "unhealthy"}:
        return "status-offline"
    return "status-unknown"


def _status_text(status: str) -> str:
    normalized = status.replace("_", " ").strip()
    if normalized:
        return normalized.capitalize()
    return "Unknown"


def _job_state_class(state: str) -> str:
    return {
        "pending": "status-unknown",
        "running": "status-running",
        "completed": "status-online",
        "failed": "status-offline",
        "failed_recoverable": "status-warning",
        "cancelled": "status-unknown",
    }.get(state.lower(), "status-unknown")


def _job_type_label(job_type: str) -> str:
    return {
        "archive": "Archive",
        "restore": "Restore",
        "format": "Format",
        "verify": "Verify",
        "inventory": "Inventory",
        "import": "Import",
        "export": "Export",
    }.get(job_type.lower(), _status_text(job_type))


def _share_type_label(share_type: str) -> str:
    return {
        "inbox": "Ingest Inbox",
        "restore": "Restore Output",
        "catalog": "Catalog Browser",
        "virtual": "Virtual Pool",
        "pool": "Pool Share",
    }.get(share_type.lower(), _status_text(share_type))


def _connect_host() -> str:
    override = os.environ.get("OPENBLADE_CONNECT_HOST", "").strip()
    if override:
        return override
    host = request.host.split(":", 1)[0].strip().lower()
    if not host or host in {"0.0.0.0", "::"}:
        return "127.0.0.1"
    return host


def _parse_csv_tokens(raw_value: str) -> list[str]:
    tokens = [item.strip() for item in raw_value.split(",")]
    return [item for item in tokens if item]


def _drive_media_index(
    *,
    media: list[dict[str, object]],
    drives: list[dict[str, object]],
    catalog_files: list[dict[str, object]],
) -> list[dict[str, object]]:
    drive_by_barcode: dict[str, str] = {}
    for drive in drives:
        barcode = str(drive.get("loadedMedia") or "").strip()
        if not barcode:
            continue
        drive_id = str(drive.get("serialNumber") or drive.get("id") or "—")
        drive_by_barcode[barcode] = drive_id

    files_by_barcode: dict[str, list[str]] = {}
    for item in catalog_files:
        barcode = str(item.get("primary_barcode") or "").strip()
        path = str(item.get("path") or "").strip()
        if not barcode or not path:
            continue
        bucket = files_by_barcode.setdefault(barcode, [])
        if len(bucket) < 4:
            bucket.append(path)

    index: list[dict[str, object]] = []
    seen_barcodes: set[str] = set()
    for item in media:
        barcode = str(item.get("barcode") or "").strip()
        if not barcode:
            continue
        seen_barcodes.add(barcode)
        linked_files = files_by_barcode.get(barcode, [])
        index.append(
            {
                "barcode": barcode,
                "slot": str(item.get("slot") or item.get("location") or "—"),
                "partition": str(item.get("partition") or "—"),
                "state": str(item.get("state") or "unknown"),
                "drive": drive_by_barcode.get(barcode, "—"),
                "file_count": len(linked_files),
                "sample_paths": linked_files,
            }
        )
    for barcode, drive_name in drive_by_barcode.items():
        if barcode in seen_barcodes:
            continue
        linked_files = files_by_barcode.get(barcode, [])
        index.append(
            {
                "barcode": barcode,
                "slot": "—",
                "partition": "—",
                "state": "mounted",
                "drive": drive_name,
                "file_count": len(linked_files),
                "sample_paths": linked_files,
            }
        )
    return index


def _write_path_recommendations(
    *,
    pools: list[dict[str, object]],
    policies: list[dict[str, object]],
    source_stream: dict[str, object],
) -> list[str]:
    recommendations: list[str] = []

    def _int_or_default(value: object, default: int) -> int:
        try:
            if isinstance(value, bool):
                return int(value)
            if isinstance(value, int):
                return value
            if isinstance(value, float):
                return int(value)
            if isinstance(value, str):
                return int(value.strip())
            return default
        except (TypeError, ValueError):
            return default

    if not any(_int_or_default(pool.get("replication_factor"), 1) >= 2 for pool in pools):
        recommendations.append(
            "Set replication factor to at least 2 on critical pools for tape copy resiliency."
        )
    if not any(bool(policy.get("allow_sharding")) for policy in policies):
        recommendations.append(
            "Enable sharding on at least one policy to parallelize multi-drive writes."
        )
    if not any(_int_or_default(policy.get("shard_size_bytes"), 0) > 0 for policy in policies):
        recommendations.append(
            "Set shard_size_bytes on sharded policies to control per-drive shard wave sizing."
        )
    if not any(bool(policy.get("auto_clean_before_archive", True)) for policy in policies):
        recommendations.append(
            "Keep auto-clean enabled for active policies to avoid ingest failures from overdue drives."
        )
    if not any(
        str(policy.get("default_ingest_mode") or "").lower() == "source_stream"
        for policy in policies
    ):
        recommendations.append(
            "Add a source-stream policy for low-latency direct-to-tape ingest workflows."
        )

    checksum_mode = str(source_stream.get("checksum_mode") or "").lower()
    if checksum_mode not in {"streaming", "precompute_and_post_verify"}:
        recommendations.append(
            "Use streaming or precompute+post-verify checksum mode to reduce integrity blind spots."
        )
    if not bool(source_stream.get("preflight_read_check", True)):
        recommendations.append(
            "Enable source preflight read checks to fail fast before expensive tape movement."
        )
    if _int_or_default(source_stream.get("max_retries"), 0) < 2:
        recommendations.append(
            "Set source-stream retries to at least 2 to absorb transient network/read errors."
        )

    if not recommendations:
        recommendations.append(
            "Current write-path profile is balanced for replication, integrity checks, and throughput."
        )
    return recommendations


def _write_path_metrics(
    *,
    pools: list[dict[str, object]],
    policies: list[dict[str, object]],
    shares: list[dict[str, object]],
    source_stream: dict[str, object],
) -> dict[str, int]:
    def _int_or_default(value: object, default: int) -> int:
        try:
            if isinstance(value, bool):
                return int(value)
            if isinstance(value, int):
                return value
            if isinstance(value, float):
                return int(value)
            if isinstance(value, str):
                return int(value.strip())
            return default
        except (TypeError, ValueError):
            return default

    replicated_pools = sum(
        1 for pool in pools if _int_or_default(pool.get("replication_factor"), 1) >= 2
    )
    sharded_policies = sum(1 for policy in policies if bool(policy.get("allow_sharding")))
    auto_clean_policies = sum(
        1 for policy in policies if bool(policy.get("auto_clean_before_archive", True))
    )
    folder_rules = 0
    for share in shares:
        mappings = share.get("folder_mappings")
        if isinstance(mappings, list):
            folder_rules += len(mappings)
    writable_shares = sum(1 for share in shares if bool(share.get("writable")))
    source_stream_enabled = 1 if bool(source_stream.get("enabled")) else 0
    return {
        "replicated_pools": replicated_pools,
        "sharded_policies": sharded_policies,
        "auto_clean_policies": auto_clean_policies,
        "folder_rules": folder_rules,
        "writable_shares": writable_shares,
        "source_stream_enabled": source_stream_enabled,
    }


def _summary(devices: list[Device]) -> dict[str, int]:
    online = sum(1 for item in devices if item.status == "online")
    warning = sum(1 for item in devices if item.status in {"degraded", "warning"})
    offline = sum(1 for item in devices if item.status in {"offline", "error", "failed"})
    unknown = max(len(devices) - online - warning - offline, 0)
    return {
        "total": len(devices),
        "online": online,
        "warning": warning,
        "offline": offline,
        "unknown": unknown,
    }


def _pick_device(devices: list[Device], requested_id: int | None) -> Device | None:
    if requested_id is not None:
        for device in devices:
            if device.id == requested_id:
                return device
    return devices[0] if devices else None


def _backend(app: Flask) -> BackendClient:
    client = app.extensions.get("backend_client")
    if not isinstance(client, BackendClient):
        raise RuntimeError("Backend client is not configured")
    return client


def create_app(client_factory: Callable[[], BackendClient] | None = None) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.update(
        SECRET_KEY=_secret_key(),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=_bool_env(
            "OPENBLADE_WEB_SECURE_COOKIES",
            default=os.environ.get("OPENBLADE_ENV", "development").lower() == "production",
        ),
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        SESSION_REFRESH_EACH_REQUEST=False,
        MAX_CONTENT_LENGTH=1_000_000,
    )
    app.extensions["backend_client"] = (client_factory or _backend_client_factory)()

    @app.before_request
    def _load_context() -> ResponseReturnValue | None:
        g.nas_nav = NAS_NAV
        g.current_user = session.get("username")
        g.csrf_token = _ensure_csrf_token()
        if _is_login_required(request.endpoint) and "api_token" not in session:
            return redirect(url_for("login", next=request.path))
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not _validate_csrf():
            app.logger.warning(
                "CSRF validation failed", extra={"endpoint": request.endpoint, "ip": _client_ip()}
            )
            abort(400, description="Invalid CSRF token")
        return None

    @app.after_request
    def _security_headers(response: Response) -> Response:
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        csp_directives = [
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self'",
            "img-src 'self' data:",
            "connect-src 'self'",
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
        ]
        if app.config.get("SESSION_COOKIE_SECURE"):
            csp_directives.append("upgrade-insecure-requests")
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Content-Security-Policy"] = "; ".join(csp_directives)
        if "api_token" in session:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    @app.context_processor
    def _inject_template_context() -> dict[str, object]:
        return {
            "nas_nav": NAS_NAV,
            "csrf_token": _ensure_csrf_token(),
            "current_user": session.get("username"),
            "status_class": _status_class,
            "status_text": _status_text,
            "role_label": _role_label,
            "job_state_class": _job_state_class,
            "job_type_label": _job_type_label,
            "share_type_label": _share_type_label,
            "aml_role_label": _aml_role_label,
            "job_types": _JOB_TYPES,
            "job_states": _JOB_STATES,
            "manage_sections": MANAGE_SECTIONS,
            "storage_sections": STORAGE_SECTIONS,
            "role_options": ROLE_OPTIONS,
            "aml_role_options": _AML_ROLE_OPTIONS,
            "policy_type_options": _POLICY_TYPE_OPTIONS,
            "ingest_mode_options": _INGEST_MODE_OPTIONS,
            "shard_strategy_options": _SHARD_STRATEGY_OPTIONS,
            "cache_eviction_options": _CACHE_EVICTION_OPTIONS,
            "source_checksum_modes": _SOURCE_CHECKSUM_MODES,
            "share_type_options": _SHARE_TYPE_OPTIONS,
            "share_access_options": _SHARE_ACCESS_OPTIONS,
        }

    def _token() -> str:
        token = session.get("api_token")
        if not isinstance(token, str) or not token:
            abort(401)
        return token

    def _handle_backend_error(error: BackendError) -> ResponseReturnValue:
        if error.status_code == 401:
            session.clear()
            return redirect(url_for("login"))
        flash(error.detail, "error")
        return redirect(url_for("devices_index"))

    @app.get("/")
    def home() -> ResponseReturnValue:
        if "api_token" not in session:
            return redirect(url_for("login"))
        return redirect(url_for("devices_index"))

    @app.route("/login", methods=["GET", "POST"])
    def login() -> ResponseReturnValue:
        next_path = request.values.get("next", "").strip()
        if request.method == "GET":
            return Response(render_template("login.html", next_path=next_path), status=200)

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        login_key = f"{_client_ip()}:{username.lower()[:64]}"
        if _is_login_blocked(app, login_key):
            flash("Too many login attempts. Try again in a few minutes.", "error")
            return Response(render_template("login.html", next_path=next_path), status=429)
        if not username or not password:
            _record_login_failure(app, login_key)
            flash("Username and password are required.", "error")
            return Response(render_template("login.html", next_path=next_path), status=400)

        try:
            token = _backend(app).login(username=username, password=password)
        except BackendError as error:
            _record_login_failure(app, login_key)
            app.logger.warning("Login failed", extra={"username": username, "ip": _client_ip()})
            flash(error.detail, "error")
            return Response(render_template("login.html", next_path=next_path), status=401)

        session.clear()
        session["username"] = username
        session["api_token"] = token
        session.permanent = True
        _ensure_csrf_token()
        _clear_login_failures(app, login_key)
        if _is_safe_redirect_target(next_path):
            return redirect(next_path)
        return redirect(url_for("devices_index"))

    @app.post("/logout")
    def logout() -> ResponseReturnValue:
        session.clear()
        return redirect(url_for("login"))

    @app.get("/devices")
    def devices_index() -> ResponseReturnValue:
        try:
            devices = _backend(app).list_devices(_token())
        except BackendError as error:
            return _handle_backend_error(error)
        return Response(
            render_template(
                "devices/index.html",
                devices=devices,
                summary=_summary(devices),
                page_id="devices-index",
            ),
            status=200,
        )

    @app.route("/devices/register", methods=["GET", "POST"])
    def devices_register() -> ResponseReturnValue:
        values = {
            "name": request.form.get("name", "").strip(),
            "connection_url": request.form.get("connection_url", "").strip(),
            "serial_number": request.form.get("serial_number", "").strip(),
            "model": request.form.get("model", MODEL_OPTIONS[0]),
            "device_username": request.form.get("device_username", "").strip(),
        }
        device_password = request.form.get("device_password", "")
        errors: dict[str, str] = {}

        if request.method == "POST":
            if not _ASCII_PRINTABLE.fullmatch(values["name"]):
                errors["name"] = "Device name must be 1-64 printable ASCII characters."
            connection_error = _validate_device_url(values["connection_url"])
            if connection_error:
                errors["connection_url"] = connection_error
            if values["serial_number"] and not _SERIAL_ALLOWED.fullmatch(values["serial_number"]):
                errors["serial_number"] = "Serial number accepts letters, numbers, and dashes only."
            if values["model"] not in MODEL_OPTIONS:
                errors["model"] = "Unsupported device model."
            if values["device_username"] and not _DEVICE_USERNAME_ALLOWED.fullmatch(
                values["device_username"]
            ):
                errors["device_username"] = (
                    "Device username accepts letters, numbers, dot, underscore, and dash."
                )
            if device_password and not values["device_username"]:
                errors["device_username"] = (
                    "Provide a device username when testing authenticated connection."
                )

            if not errors:
                try:
                    _backend(app).probe_device_endpoint(
                        connection_url=values["connection_url"],
                        username=values["device_username"] or None,
                        password=device_password or None,
                    )
                except BackendError as error:
                    errors["connection_url"] = error.detail

            if not errors:
                try:
                    devices = _backend(app).list_devices(_token())
                    next_order = max((item.sort_order for item in devices), default=0) + 1
                    _backend(app).create_device(
                        _token(),
                        {
                            "name": values["name"],
                            "emulator_url": values["connection_url"],
                            "serial_number": values["serial_number"] or None,
                            "model": values["model"],
                            "role": "primary",
                            "enabled": True,
                            "sort_order": next_order,
                        },
                    )
                except BackendError as error:
                    return _handle_backend_error(error)
                flash("Device registered successfully.", "success")
                return redirect(url_for("devices_index"))

        status = 400 if request.method == "POST" and errors else 200
        return Response(
            render_template(
                "devices/register.html",
                model_options=MODEL_OPTIONS,
                values=values,
                errors=errors,
                page_id="devices-register",
            ),
            status=status,
        )

    @app.get("/devices/<int:device_id>/manage")
    def devices_manage(device_id: int) -> ResponseReturnValue:
        return redirect(url_for("devices_manage_section", device_id=device_id, section="overview"))

    @app.post("/devices/<int:device_id>/settings")
    def devices_update_settings(device_id: int) -> ResponseReturnValue:
        name = request.form.get("name", "").strip()
        connection_url = request.form.get("connection_url", "").strip()
        enabled = request.form.get("enabled", "").strip().lower() == "true"
        if not _ASCII_PRINTABLE.fullmatch(name):
            flash("Device name must be 1-64 printable ASCII characters.", "error")
            return redirect(
                url_for("devices_manage_section", device_id=device_id, section="settings")
            )
        connection_error = _validate_device_url(connection_url)
        if connection_error:
            flash(connection_error, "error")
            return redirect(
                url_for("devices_manage_section", device_id=device_id, section="settings")
            )

        try:
            current = _backend(app).get_device(_token(), device_id)
            if current is None:
                abort(404, description="Device not found")
            _backend(app).probe_device_endpoint(connection_url=connection_url)
            _backend(app).update_device(
                _token(),
                device_id,
                {
                    "name": name,
                    "emulator_url": connection_url,
                    "model": current.model,
                    "role": current.role or "primary",
                    "enabled": enabled,
                    "sort_order": current.sort_order,
                },
            )
        except BackendError as error:
            return _handle_backend_error(error)
        flash("Device settings updated.", "success")
        return redirect(url_for("devices_manage_section", device_id=device_id, section="settings"))

    @app.post("/devices/<int:device_id>/operations/inventory")
    def devices_trigger_inventory(device_id: int) -> ResponseReturnValue:
        try:
            _backend(app).trigger_inventory(_token(), library_id=device_id)
        except BackendError as error:
            return _handle_backend_error(error)
        flash("Inventory scan started.", "success")
        return redirect(
            url_for("devices_manage_section", device_id=device_id, section="operations")
        )

    @app.post("/devices/<int:device_id>/operations/move")
    def devices_move_media(device_id: int) -> ResponseReturnValue:
        source = request.form.get("source", "").strip()
        destination = request.form.get("destination", "").strip()
        barcode = request.form.get("barcode", "").strip()
        if not source or not destination:
            flash("Source and destination are required.", "error")
            return redirect(
                url_for("devices_manage_section", device_id=device_id, section="operations")
            )
        if not _OPERATION_TOKEN.fullmatch(source) or not _OPERATION_TOKEN.fullmatch(destination):
            flash(
                "Source and destination accept letters, numbers, comma, dot, underscore, dash, and colon.",
                "error",
            )
            return redirect(
                url_for("devices_manage_section", device_id=device_id, section="operations")
            )
        if barcode and not _BARCODE_TOKEN.fullmatch(barcode):
            flash("Barcode accepts letters, numbers, dot, underscore, and dash only.", "error")
            return redirect(
                url_for("devices_manage_section", device_id=device_id, section="operations")
            )
        try:
            _backend(app).move_media_operation(
                _token(),
                library_id=device_id,
                source=source,
                destination=destination,
                barcode=barcode or None,
            )
        except BackendError as error:
            return _handle_backend_error(error)
        flash("Move operation queued.", "success")
        return redirect(
            url_for("devices_manage_section", device_id=device_id, section="operations")
        )

    @app.post("/devices/<int:device_id>/operations/mount")
    def devices_mount_media(device_id: int) -> ResponseReturnValue:
        barcode = request.form.get("barcode", "").strip()
        drive = request.form.get("drive", "").strip()
        partition = request.form.get("partition", "").strip()
        if not barcode or not drive:
            flash("Barcode and drive are required.", "error")
            return redirect(
                url_for("devices_manage_section", device_id=device_id, section="operations")
            )
        if not _BARCODE_TOKEN.fullmatch(barcode):
            flash("Barcode accepts letters, numbers, dot, underscore, and dash only.", "error")
            return redirect(
                url_for("devices_manage_section", device_id=device_id, section="operations")
            )
        if not _OPERATION_TOKEN.fullmatch(drive):
            flash(
                "Drive accepts letters, numbers, comma, dot, underscore, dash, and colon.", "error"
            )
            return redirect(
                url_for("devices_manage_section", device_id=device_id, section="operations")
            )
        if partition and not _OPERATION_TOKEN.fullmatch(partition):
            flash(
                "Partition accepts letters, numbers, comma, dot, underscore, dash, and colon.",
                "error",
            )
            return redirect(
                url_for("devices_manage_section", device_id=device_id, section="operations")
            )
        try:
            _backend(app).mount_media_operation(
                _token(),
                library_id=device_id,
                barcode=barcode,
                drive=drive,
                partition=partition or None,
            )
        except BackendError as error:
            return _handle_backend_error(error)
        flash("Mount operation queued.", "success")
        return redirect(
            url_for("devices_manage_section", device_id=device_id, section="operations")
        )

    @app.post("/devices/<int:device_id>/operations/unmount")
    def devices_unmount_media(device_id: int) -> ResponseReturnValue:
        barcode = request.form.get("barcode", "").strip()
        drive = request.form.get("drive", "").strip()
        if not barcode or not drive:
            flash("Barcode and drive are required.", "error")
            return redirect(
                url_for("devices_manage_section", device_id=device_id, section="operations")
            )
        if not _BARCODE_TOKEN.fullmatch(barcode):
            flash("Barcode accepts letters, numbers, dot, underscore, and dash only.", "error")
            return redirect(
                url_for("devices_manage_section", device_id=device_id, section="operations")
            )
        if not _OPERATION_TOKEN.fullmatch(drive):
            flash(
                "Drive accepts letters, numbers, comma, dot, underscore, dash, and colon.", "error"
            )
            return redirect(
                url_for("devices_manage_section", device_id=device_id, section="operations")
            )
        try:
            _backend(app).unmount_media_operation(
                _token(),
                library_id=device_id,
                barcode=barcode,
                drive=drive,
            )
        except BackendError as error:
            return _handle_backend_error(error)
        flash("Unmount operation queued.", "success")
        return redirect(
            url_for("devices_manage_section", device_id=device_id, section="operations")
        )

    @app.post("/devices/<int:device_id>/operations/import")
    def devices_import_media(device_id: int) -> ResponseReturnValue:
        partition = request.form.get("partition", "").strip()
        ie_station = request.form.get("ie_station", "").strip()
        if not partition or not ie_station:
            flash("Partition and I/E station are required.", "error")
            return redirect(
                url_for("devices_manage_section", device_id=device_id, section="operations")
            )
        if not _OPERATION_TOKEN.fullmatch(partition) or not _OPERATION_TOKEN.fullmatch(ie_station):
            flash(
                "Partition and I/E station accept letters, numbers, comma, dot, underscore, dash, and colon.",
                "error",
            )
            return redirect(
                url_for("devices_manage_section", device_id=device_id, section="operations")
            )
        try:
            _backend(app).start_import_operation(
                _token(),
                library_id=device_id,
                partition=partition,
                ie_station=ie_station,
            )
        except BackendError as error:
            return _handle_backend_error(error)
        flash("Import operation queued.", "success")
        return redirect(
            url_for("devices_manage_section", device_id=device_id, section="operations")
        )

    @app.post("/devices/<int:device_id>/operations/export")
    def devices_export_media(device_id: int) -> ResponseReturnValue:
        raw_barcodes = request.form.get("barcodes", "").strip()
        ie_station = request.form.get("ie_station", "").strip()
        barcodes = [item for item in re.split(r"[\s,]+", raw_barcodes) if item]
        if not barcodes or not ie_station:
            flash("At least one barcode and an I/E station are required.", "error")
            return redirect(
                url_for("devices_manage_section", device_id=device_id, section="operations")
            )
        if not _OPERATION_TOKEN.fullmatch(ie_station):
            flash(
                "I/E station accepts letters, numbers, comma, dot, underscore, dash, and colon.",
                "error",
            )
            return redirect(
                url_for("devices_manage_section", device_id=device_id, section="operations")
            )
        if any(_BARCODE_TOKEN.fullmatch(item) is None for item in barcodes):
            flash("Each barcode must use letters, numbers, dot, underscore, or dash.", "error")
            return redirect(
                url_for("devices_manage_section", device_id=device_id, section="operations")
            )
        try:
            _backend(app).start_export_operation(
                _token(),
                library_id=device_id,
                barcodes=barcodes,
                ie_station=ie_station,
            )
        except BackendError as error:
            return _handle_backend_error(error)
        flash("Export operation queued.", "success")
        return redirect(
            url_for("devices_manage_section", device_id=device_id, section="operations")
        )

    @app.post("/devices/<int:device_id>/operations/restore")
    def devices_restore_from_tape(device_id: int) -> ResponseReturnValue:
        catalog_path = request.form.get("catalog_path", "").strip()
        dest_path = request.form.get("dest_path", "").strip()
        if not catalog_path or not dest_path:
            flash("Catalog path and destination path are required.", "error")
            return redirect(
                url_for("devices_manage_section", device_id=device_id, section="operations")
            )
        if not _is_safe_storage_path(catalog_path) or not _is_safe_storage_path(dest_path):
            flash(
                "Catalog and destination paths must be absolute normalized paths within policy limits.",
                "error",
            )
            return redirect(
                url_for("devices_manage_section", device_id=device_id, section="operations")
            )
        try:
            _backend(app).create_restore_job(
                _token(), catalog_path=catalog_path, dest_path=dest_path
            )
        except BackendError as error:
            return _handle_backend_error(error)
        flash(
            "Restore hydration job submitted. Tape loading will be handled by job workflow.",
            "success",
        )
        return redirect(
            url_for("devices_manage_section", device_id=device_id, section="operations")
        )

    @app.post("/devices/<int:device_id>/operations/pool")
    def devices_create_pool(device_id: int) -> ResponseReturnValue:
        pool_id = request.form.get("pool_id", "").strip()
        pool_name = request.form.get("pool_name", "").strip()
        description = request.form.get("description", "").strip()
        mount_path = request.form.get("mount_path", "").strip()
        restore_target_path = (
            request.form.get("restore_target_path", "").strip() or "/openblade/restore"
        )
        volume_group_ids_raw = request.form.get("volume_group_ids", "").strip()
        access_mode = request.form.get("access_mode", "read_only").strip().lower()
        backup_order_mode = request.form.get("backup_order_mode", "sequential").strip().lower()
        replication_factor_raw = request.form.get("replication_factor", "1").strip()
        virtual_mount_enabled = request.form.get("virtual_mount_enabled", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

        if not _POOL_ID_ALLOWED.fullmatch(pool_id):
            flash("Pool ID accepts letters, numbers, dot, underscore, dash, and colon.", "error")
            return redirect(
                url_for("devices_manage_section", device_id=device_id, section="operations")
            )
        if not _ASCII_PRINTABLE.fullmatch(pool_name):
            flash("Pool name must be 1-64 printable ASCII characters.", "error")
            return redirect(
                url_for("devices_manage_section", device_id=device_id, section="operations")
            )
        if mount_path and not _is_safe_storage_path(mount_path):
            flash("Pool mount path must be an absolute normalized path.", "error")
            return redirect(
                url_for("devices_manage_section", device_id=device_id, section="operations")
            )
        if not _is_safe_storage_path(restore_target_path):
            flash("Restore target path must be an absolute normalized path.", "error")
            return redirect(
                url_for("devices_manage_section", device_id=device_id, section="operations")
            )
        if access_mode not in {"read_only", "read_write"}:
            flash("Pool access mode must be read_only or read_write.", "error")
            return redirect(
                url_for("devices_manage_section", device_id=device_id, section="operations")
            )
        if backup_order_mode not in {"sequential", "parallel"}:
            flash("Backup order mode must be sequential or parallel.", "error")
            return redirect(
                url_for("devices_manage_section", device_id=device_id, section="operations")
            )
        try:
            replication_factor = int(replication_factor_raw)
        except ValueError:
            flash("Replication factor must be a number between 1 and 4.", "error")
            return redirect(
                url_for("devices_manage_section", device_id=device_id, section="operations")
            )
        if replication_factor < 1 or replication_factor > 4:
            flash("Replication factor must be between 1 and 4.", "error")
            return redirect(
                url_for("devices_manage_section", device_id=device_id, section="operations")
            )

        volume_group_ids = _parse_csv_tokens(volume_group_ids_raw)
        if any(_POOL_ID_ALLOWED.fullmatch(item) is None for item in volume_group_ids):
            flash(
                "Volume group IDs accept letters, numbers, dot, underscore, dash, and colon.",
                "error",
            )
            return redirect(
                url_for("devices_manage_section", device_id=device_id, section="operations")
            )

        payload: dict[str, object] = {
            "id": pool_id,
            "name": pool_name,
            "description": description or None,
            "volume_group_ids": volume_group_ids,
            "mount_path": mount_path or None,
            "virtual_mount_enabled": virtual_mount_enabled,
            "restore_target_path": restore_target_path,
            "access_mode": access_mode,
            "replication_factor": replication_factor,
            "backup_order_mode": backup_order_mode,
        }
        try:
            _backend(app).create_nas_pool(_token(), payload)
        except BackendError as error:
            return _handle_backend_error(error)
        flash("Pool created or updated.", "success")
        return redirect(
            url_for("devices_manage_section", device_id=device_id, section="operations")
        )

    @app.post("/devices/<int:device_id>/operations/share")
    def devices_create_pool_share(device_id: int) -> ResponseReturnValue:
        pool_id = request.form.get("pool_id", "").strip()
        share_name = request.form.get("share_name", "").strip()
        writable = request.form.get("writable", "").strip().lower() in {"1", "true", "yes", "on"}
        if not _POOL_ID_ALLOWED.fullmatch(pool_id):
            flash("Pool ID accepts letters, numbers, dot, underscore, dash, and colon.", "error")
            return redirect(
                url_for("devices_manage_section", device_id=device_id, section="operations")
            )
        if not _SHARE_NAME_ALLOWED.fullmatch(share_name):
            flash("Share name accepts letters, numbers, dot, underscore, and dash.", "error")
            return redirect(
                url_for("devices_manage_section", device_id=device_id, section="operations")
            )
        share_path = f"/pools/{pool_id}"
        payload = {
            "path": share_path,
            "name": share_name,
            "share_type": "pool",
            "writable": writable,
            "description": f"Pool mapped share for {pool_id}",
        }
        try:
            _backend(app).create_nas_share(_token(), payload)
        except BackendError as error:
            return _handle_backend_error(error)
        flash("Pool share created or updated.", "success")
        return redirect(
            url_for("devices_manage_section", device_id=device_id, section="operations")
        )

    @app.post("/devices/<int:device_id>/operations/magazine/<magazine_id>/eject")
    def devices_eject_magazine(device_id: int, magazine_id: str) -> ResponseReturnValue:
        if not _OPERATION_TOKEN.fullmatch(magazine_id):
            flash("Magazine ID is invalid.", "error")
            return redirect(
                url_for("devices_manage_section", device_id=device_id, section="operations")
            )
        try:
            _backend(app).eject_magazine(_token(), library_id=device_id, magazine_id=magazine_id)
        except BackendError as error:
            return _handle_backend_error(error)
        flash(f"Magazine {magazine_id} eject requested.", "success")
        return redirect(
            url_for("devices_manage_section", device_id=device_id, section="operations")
        )

    @app.post("/devices/<int:device_id>/operations/magazine/<magazine_id>/insert")
    def devices_insert_magazine(device_id: int, magazine_id: str) -> ResponseReturnValue:
        if not _OPERATION_TOKEN.fullmatch(magazine_id):
            flash("Magazine ID is invalid.", "error")
            return redirect(
                url_for("devices_manage_section", device_id=device_id, section="operations")
            )
        try:
            _backend(app).insert_magazine(_token(), library_id=device_id, magazine_id=magazine_id)
        except BackendError as error:
            return _handle_backend_error(error)
        flash(f"Magazine {magazine_id} insert requested.", "success")
        return redirect(
            url_for("devices_manage_section", device_id=device_id, section="operations")
        )

    @app.get("/devices/<int:device_id>/manage/<section>")
    def devices_manage_section(device_id: int, section: str) -> ResponseReturnValue:
        section_keys = {key for key, _ in MANAGE_SECTIONS}
        if section not in section_keys:
            abort(404)
        try:
            device = _backend(app).get_device(_token(), device_id)
            if device is None:
                abort(404, description="Device not found")
            section_data: dict[str, object] = {}
            if section == "overview":
                section_data["library_status"] = _backend(app).get_library_status(
                    _token(), library_id=device_id
                )
                section_data["drives"] = _backend(app).list_drives(_token(), library_id=device_id)
                section_data["media_count"] = len(
                    _backend(app).list_media(_token(), library_id=device_id, limit=500)
                )
            elif section == "inventory":
                section_data["media"] = _backend(app).list_media(
                    _token(), library_id=device_id, limit=100
                )
            elif section == "drives":
                section_data["drives"] = _backend(app).list_drives(_token(), library_id=device_id)
            elif section == "partitions":
                section_data["partitions"] = _backend(app).list_partitions(
                    _token(), library_id=device_id
                )
            elif section == "jobs":
                section_data["jobs"] = _backend(app).list_jobs(_token(), library_id=device_id)
            elif section == "operations":
                jobs = _backend(app).list_jobs(_token(), library_id=device_id)
                drives = _backend(app).list_drives(_token(), library_id=device_id)
                media = _backend(app).list_media(_token(), library_id=device_id, limit=200)
                catalog_files = (
                    _backend(app).list_catalog_files(_token(), limit=200).get("files") or []
                )
                if not isinstance(catalog_files, list):
                    catalog_files = []
                section_data["drives"] = drives
                section_data["media"] = media
                section_data["partitions"] = _backend(app).list_partitions(
                    _token(), library_id=device_id
                )
                section_data["ie_stations"] = _backend(app).list_ie_stations(
                    _token(), library_id=device_id
                )
                section_data["mounts"] = _backend(app).list_mounts(_token(), library_id=device_id)
                section_data["pools"] = _backend(app).list_nas_pools(_token())
                section_data["shares"] = _backend(app).list_nas_shares(_token())
                section_data["volume_groups"] = _backend(app).list_volume_groups(_token())
                magazines = _backend(app).list_magazines(_token(), library_id=device_id)
                magazines_with_mapping: list[dict[str, object]] = []
                for magazine in magazines:
                    magazine_id = str(magazine.get("id") or "").strip()
                    if not magazine_id:
                        continue
                    slots = _backend(app).list_magazine_slots(
                        _token(), library_id=device_id, magazine_id=magazine_id
                    )
                    entry = dict(magazine)
                    entry["mappedSlotCount"] = len(slots)
                    entry["slotMapAligned"] = int(magazine.get("slotCount") or 0) == len(slots)
                    magazines_with_mapping.append(entry)
                section_data["magazines"] = magazines_with_mapping
                section_data["catalog_files"] = [
                    item for item in catalog_files if isinstance(item, dict)
                ]
                section_data["drive_media_index"] = _drive_media_index(
                    media=[item for item in media if isinstance(item, dict)],
                    drives=[item for item in drives if isinstance(item, dict)],
                    catalog_files=[item for item in catalog_files if isinstance(item, dict)],
                )
                section_data["inventory_status"] = _backend(app).get_inventory_status(
                    _token(), library_id=device_id
                )
                section_data["import_status"] = _backend(app).get_import_status(
                    _token(), library_id=device_id
                )
                section_data["export_status"] = _backend(app).get_export_status(
                    _token(), library_id=device_id
                )
                section_data["active_ops"] = [
                    job
                    for job in jobs
                    if str(job.get("state", "")).lower() in {"pending", "running"}
                ]
            elif section == "reports":
                section_data["event_summary"] = _backend(app).get_event_summary(
                    _token(), library_id=device_id
                )
                section_data["alert_summary"] = _backend(app).get_alert_summary(
                    _token(), library_id=device_id
                )
                section_data["events"] = _backend(app).list_events(
                    _token(), library_id=device_id, limit=30
                )
            elif section == "settings":
                section_data["device"] = device
        except BackendError as error:
            return _handle_backend_error(error)
        return Response(
            render_template(
                "devices/manage_section.html",
                device=device,
                section=section,
                sections=MANAGE_SECTIONS,
                section_data=section_data,
                page_id=f"devices-manage-section-{section}",
            ),
            status=200,
        )

    @app.get("/storage")
    @app.get("/storage/<section>")
    def storage(section: str = "overview") -> ResponseReturnValue:
        section_keys = {key for key, _ in STORAGE_SECTIONS}
        if section not in section_keys:
            abort(404)
        selected_id = _parse_int(request.args.get("library_id"))
        try:
            devices = _backend(app).list_devices(_token())
            active_device = _pick_device(devices, selected_id)
            section_data: dict[str, object] = {}
            if section == "overview":
                if active_device is not None:
                    section_data["library_status"] = _backend(app).get_library_status(
                        _token(), library_id=active_device.id
                    )
                    section_data["media_pools"] = _backend(app).list_media_pools(
                        _token(), library_id=active_device.id
                    )
                section_data["catalog_status"] = _backend(app).get_catalog_status(_token())
            elif section == "access":
                shares = _backend(app).list_nas_shares(_token())
                pools = _backend(app).list_nas_pools(_token())
                gateway_config = _backend(app).get_gateway_config(_token())
                gateway_status = _backend(app).get_gateway_status(_token())
                gateway_credentials = _backend(app).list_gateway_credentials(_token())
                connect_host = _connect_host()
                enabled_credentials = [
                    item
                    for item in gateway_credentials
                    if bool(item.get("enabled", True)) and item.get("username")
                ]
                example_username = (
                    str(enabled_credentials[0]["username"])
                    if enabled_credentials
                    else str(session.get("username") or "admin")
                )
                share_access: list[dict[str, object]] = []
                for share in shares:
                    share_name = str(share.get("name") or "").strip()
                    share_path = str(share.get("path") or "").strip()
                    if not share_name or not share_path:
                        continue
                    mount_point = f"/mnt/openblade/{share_name}"
                    windows_unc = f"\\\\{connect_host}\\{share_name}"
                    share_access.append(
                        {
                            "name": share_name,
                            "path": share_path,
                            "share_type": str(share.get("share_type") or "share"),
                            "writable": bool(share.get("writable", False)),
                            "windows_unc": windows_unc,
                            "windows_command": (
                                f"net use Z: {windows_unc} /user:{example_username} /persistent:yes"
                            ),
                            "linux_cifs_command": (
                                f"sudo mount -t cifs //{connect_host}/{share_name} {mount_point} "
                                f"-o username={example_username},uid=$(id -u),gid=$(id -g),vers=3.0"
                            ),
                            "linux_nfs_command": f"sudo mount -t nfs {connect_host}:{share_path} {mount_point}",
                        }
                    )
                section_data["connect_host"] = connect_host
                section_data["gateway_config"] = gateway_config
                section_data["gateway_status"] = gateway_status
                section_data["gateway_credentials"] = gateway_credentials
                section_data["nas_shares"] = shares
                section_data["nas_pools"] = pools
                section_data["share_access"] = share_access
                section_data["example_username"] = example_username
            elif section == "write-path":
                pools = _backend(app).list_nas_pools(_token())
                shares = _backend(app).list_nas_shares(_token())
                policies = _backend(app).list_nas_policies(_token())
                cache_drives = _backend(app).list_cache_drives(_token())
                source_stream = _backend(app).get_source_stream_config(_token())
                section_data["nas_pools"] = pools
                section_data["nas_shares"] = shares
                section_data["policies"] = policies
                section_data["cache_drives"] = cache_drives
                section_data["source_stream"] = source_stream
                section_data["recommendations"] = _write_path_recommendations(
                    pools=[item for item in pools if isinstance(item, dict)],
                    policies=[item for item in policies if isinstance(item, dict)],
                    source_stream=source_stream if isinstance(source_stream, dict) else {},
                )
                section_data["metrics"] = _write_path_metrics(
                    pools=[item for item in pools if isinstance(item, dict)],
                    policies=[item for item in policies if isinstance(item, dict)],
                    shares=[item for item in shares if isinstance(item, dict)],
                    source_stream=source_stream if isinstance(source_stream, dict) else {},
                )
            elif section == "archive":
                section_data["archive_jobs"] = _backend(app).list_jobs(_token(), job_type="archive")
            elif section == "catalog":
                section_data["catalog_status"] = _backend(app).get_catalog_status(_token())
                section_data["catalog"] = _backend(app).list_catalog_files(_token(), limit=50)
                section_data["datasets"] = _backend(app).list_nas_datasets(_token(), limit=100)
                verify_snapshot = session.get("nas_verify_snapshot")
                section_data["verify_snapshot"] = (
                    verify_snapshot if isinstance(verify_snapshot, dict) else None
                )
            elif section == "restore":
                section_data["restore_jobs"] = _backend(app).list_jobs(_token(), job_type="restore")
        except BackendError as error:
            return _handle_backend_error(error)

        return Response(
            render_template(
                "storage/index.html",
                section=section,
                devices=devices,
                active_device=active_device,
                section_data=section_data,
                page_id="nas-storage",
            ),
            status=200,
        )

    @app.post("/storage/archive")
    def storage_archive_submit() -> ResponseReturnValue:
        source_path = request.form.get("source_path", "").strip()
        volume_group = request.form.get("volume_group", "").strip()
        if not source_path or not volume_group:
            flash("Source path and volume group are required.", "error")
            return redirect(url_for("storage", section="archive"))
        if not _is_safe_storage_path(source_path):
            flash("Source path must be an absolute normalized path within policy limits.", "error")
            return redirect(url_for("storage", section="archive"))
        try:
            _backend(app).create_archive_job(
                _token(), source_path=source_path, volume_group=volume_group
            )
        except BackendError as error:
            return _handle_backend_error(error)
        flash("Archive job submitted.", "success")
        return redirect(url_for("jobs"))

    @app.post("/storage/restore")
    def storage_restore_submit() -> ResponseReturnValue:
        catalog_path = request.form.get("catalog_path", "").strip()
        dest_path = request.form.get("dest_path", "").strip()
        if not catalog_path or not dest_path:
            flash("Catalog path and destination path are required.", "error")
            return redirect(url_for("storage", section="restore"))
        if not _is_safe_storage_path(catalog_path) or not _is_safe_storage_path(dest_path):
            flash(
                "Catalog and destination paths must be absolute normalized paths within policy limits.",
                "error",
            )
            return redirect(url_for("storage", section="restore"))
        try:
            _backend(app).create_restore_job(
                _token(), catalog_path=catalog_path, dest_path=dest_path
            )
        except BackendError as error:
            return _handle_backend_error(error)
        flash("Restore job submitted.", "success")
        return redirect(url_for("jobs"))

    @app.post("/storage/datasets/<dataset_id>/verify")
    def storage_dataset_verify(dataset_id: str) -> ResponseReturnValue:
        normalized_dataset_id = dataset_id.strip()
        if not _POOL_ID_ALLOWED.fullmatch(normalized_dataset_id):
            flash("Dataset ID is invalid.", "error")
            return redirect(url_for("storage", section="catalog"))
        try:
            result = _backend(app).verify_nas_dataset(_token(), dataset_id=normalized_dataset_id)
        except BackendError as error:
            flash(error.detail, "error")
            return redirect(url_for("storage", section="catalog"))
        files_verified = int(result.get("files_verified", 0))
        files_corrupt = int(result.get("files_corrupt", 0))
        files_updated = int(result.get("files_updated", 0))
        session["nas_verify_snapshot"] = {
            "dataset_id": normalized_dataset_id,
            "files_verified": files_verified,
            "files_corrupt": files_corrupt,
            "files_updated": files_updated,
            "updated_at": int(time.time()),
        }
        if files_corrupt > 0:
            flash(
                f"Verify completed for {normalized_dataset_id}: {files_verified} verified, {files_corrupt} corrupt.",
                "warning",
            )
        else:
            flash(
                f"Verify completed for {normalized_dataset_id}: {files_verified} verified, {files_updated} checksums updated.",
                "success",
            )
        return redirect(url_for("storage", section="catalog"))

    @app.post("/storage/policies")
    def storage_policy_submit() -> ResponseReturnValue:
        policy_id = request.form.get("policy_id", "").strip()
        name = request.form.get("name", "").strip()
        policy_type = request.form.get("policy_type", "balanced").strip().lower()
        ingest_mode = request.form.get("default_ingest_mode", "cache_drive").strip().lower()
        shard_strategy = request.form.get("shard_strategy", "").strip().lower() or None
        shard_size_bytes_raw = request.form.get("shard_size_bytes", "").strip()
        copies_required_raw = request.form.get("copies_required", "1").strip()
        max_parallelism_raw = request.form.get("max_parallelism", "1").strip()
        allow_sharding = request.form.get("allow_sharding", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        auto_clean_before_archive = request.form.get(
            "auto_clean_before_archive", ""
        ).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        verify_before_archive = request.form.get("verify_before_archive", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        verify_after_archive = request.form.get("verify_after_archive", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if not _POOL_ID_ALLOWED.fullmatch(policy_id):
            flash("Policy ID accepts letters, numbers, dot, underscore, dash, and colon.", "error")
            return redirect(url_for("storage", section="write-path"))
        if not _ASCII_PRINTABLE.fullmatch(name):
            flash("Policy name must be 1-64 printable ASCII characters.", "error")
            return redirect(url_for("storage", section="write-path"))
        if policy_type not in _POLICY_TYPE_OPTIONS:
            flash("Policy type is invalid.", "error")
            return redirect(url_for("storage", section="write-path"))
        if ingest_mode not in _INGEST_MODE_OPTIONS:
            flash("Default ingest mode must be cache_drive or source_stream.", "error")
            return redirect(url_for("storage", section="write-path"))
        if shard_strategy is not None and shard_strategy not in _SHARD_STRATEGY_OPTIONS:
            flash("Shard strategy is invalid.", "error")
            return redirect(url_for("storage", section="write-path"))
        try:
            copies_required = int(copies_required_raw)
            max_parallelism = int(max_parallelism_raw)
        except ValueError:
            flash("Copies required and max parallelism must be numbers.", "error")
            return redirect(url_for("storage", section="write-path"))
        shard_size_bytes: int | None = None
        if shard_size_bytes_raw:
            try:
                shard_size_bytes = int(shard_size_bytes_raw)
            except ValueError:
                flash("Shard size bytes must be a whole number when provided.", "error")
                return redirect(url_for("storage", section="write-path"))
            if shard_size_bytes <= 0:
                flash("Shard size bytes must be greater than zero.", "error")
                return redirect(url_for("storage", section="write-path"))
        if copies_required < 1 or copies_required > 4:
            flash("Copies required must be between 1 and 4.", "error")
            return redirect(url_for("storage", section="write-path"))
        if max_parallelism < 1 or max_parallelism > 16:
            flash("Max parallelism must be between 1 and 16.", "error")
            return redirect(url_for("storage", section="write-path"))
        payload: dict[str, object] = {
            "id": policy_id,
            "name": name,
            "policy_type": policy_type,
            "default_ingest_mode": ingest_mode,
            "copies_required": copies_required,
            "verify_before_archive": verify_before_archive,
            "verify_after_archive": verify_after_archive,
            "allow_spillover": True,
            "allow_sharding": allow_sharding,
            "shard_size_bytes": shard_size_bytes,
            "max_parallelism": max_parallelism,
            "shard_strategy": shard_strategy,
            "auto_clean_before_archive": auto_clean_before_archive,
            "manifest_strategy": "per_tape",
            "cache_retention": "after_verified",
            "allow_source_delete": False,
        }
        try:
            _backend(app).create_or_update_nas_policy(_token(), payload)
        except BackendError as error:
            flash(error.detail, "error")
            return redirect(url_for("storage", section="write-path"))
        flash("Write policy saved.", "success")
        return redirect(url_for("storage", section="write-path"))

    @app.post("/storage/cache-drives")
    def storage_cache_drive_submit() -> ResponseReturnValue:
        drive_id = request.form.get("drive_id", "").strip()
        name = request.form.get("name", "").strip()
        root_path = request.form.get("root_path", "").strip()
        max_bytes_raw = request.form.get("max_bytes", "0").strip()
        min_free_bytes_raw = request.form.get("min_free_bytes", "0").strip()
        retention_days_raw = request.form.get("retention_days", "30").strip()
        stabilization_seconds_raw = request.form.get("stabilization_seconds", "5").strip()
        eviction_policy = request.form.get("eviction_policy", "after_verified").strip().lower()
        enabled = request.form.get("enabled", "").strip().lower() in {"1", "true", "yes", "on"}
        verify_before_archive = request.form.get("verify_before_archive", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        verify_after_archive = request.form.get("verify_after_archive", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if not _POOL_ID_ALLOWED.fullmatch(drive_id):
            flash(
                "Cache drive ID accepts letters, numbers, dot, underscore, dash, and colon.",
                "error",
            )
            return redirect(url_for("storage", section="write-path"))
        if not _ASCII_PRINTABLE.fullmatch(name):
            flash("Cache drive name must be 1-64 printable ASCII characters.", "error")
            return redirect(url_for("storage", section="write-path"))
        if not _is_safe_storage_path(root_path):
            flash("Cache drive root path must be an absolute normalized path.", "error")
            return redirect(url_for("storage", section="write-path"))
        if eviction_policy not in _CACHE_EVICTION_OPTIONS:
            flash("Cache eviction policy is invalid.", "error")
            return redirect(url_for("storage", section="write-path"))
        try:
            max_bytes = int(max_bytes_raw)
            min_free_bytes = int(min_free_bytes_raw)
            retention_days = int(retention_days_raw)
            stabilization_seconds = int(stabilization_seconds_raw)
        except ValueError:
            flash("Cache sizing and timing fields must be numeric.", "error")
            return redirect(url_for("storage", section="write-path"))
        if max_bytes <= 0:
            flash("Cache max bytes must be greater than zero.", "error")
            return redirect(url_for("storage", section="write-path"))
        if min_free_bytes < 0 or retention_days < 0 or stabilization_seconds < 0:
            flash(
                "Cache minimum free bytes, retention days, and stabilization seconds must be non-negative.",
                "error",
            )
            return redirect(url_for("storage", section="write-path"))
        payload: dict[str, object] = {
            "id": drive_id,
            "name": name,
            "root_path": root_path,
            "max_bytes": max_bytes,
            "min_free_bytes": min_free_bytes,
            "eviction_policy": eviction_policy,
            "retention_days": retention_days,
            "verify_before_archive": verify_before_archive,
            "verify_after_archive": verify_after_archive,
            "allow_source_delete_after_verify": False,
            "stabilization_seconds": stabilization_seconds,
            "support_reflink_or_hardlink": False,
            "quarantine_failed_files": True,
            "quarantine_path": f"{root_path.rstrip('/')}/quarantine",
            "enabled": enabled,
        }
        try:
            _backend(app).create_or_update_cache_drive(_token(), payload)
        except BackendError as error:
            flash(error.detail, "error")
            return redirect(url_for("storage", section="write-path"))
        flash("Cache drive saved.", "success")
        return redirect(url_for("storage", section="write-path"))

    @app.post("/storage/source-stream")
    def storage_source_stream_submit() -> ResponseReturnValue:
        checksum_mode = (
            request.form.get("checksum_mode", "precompute_and_post_verify").strip().lower()
        )
        max_retries_raw = request.form.get("max_retries", "3").strip()
        enabled = request.form.get("enabled", "").strip().lower() in {"1", "true", "yes", "on"}
        require_source_online = request.form.get(
            "require_source_online_for_entire_job", ""
        ).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        preflight_read_check = request.form.get("preflight_read_check", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        fail_on_source_change = request.form.get("fail_on_source_change", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        allow_partial = request.form.get("allow_partial_dataset_success", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if checksum_mode not in _SOURCE_CHECKSUM_MODES:
            flash("Source-stream checksum mode is invalid.", "error")
            return redirect(url_for("storage", section="write-path"))
        try:
            max_retries = int(max_retries_raw)
        except ValueError:
            flash("Source-stream max retries must be numeric.", "error")
            return redirect(url_for("storage", section="write-path"))
        if max_retries < 0 or max_retries > 10:
            flash("Source-stream max retries must be between 0 and 10.", "error")
            return redirect(url_for("storage", section="write-path"))
        payload = {
            "enabled": enabled,
            "require_source_online_for_entire_job": require_source_online,
            "preflight_read_check": preflight_read_check,
            "checksum_mode": checksum_mode,
            "retry_policy": "linear",
            "max_retries": max_retries,
            "fail_on_source_change": fail_on_source_change,
            "snapshot_required": False,
            "source_change_detection": "size_mtime_checksum",
            "allow_partial_dataset_success": allow_partial,
        }
        try:
            _backend(app).update_source_stream_config(_token(), payload)
        except BackendError as error:
            flash(error.detail, "error")
            return redirect(url_for("storage", section="write-path"))
        flash("Source-stream configuration saved.", "success")
        return redirect(url_for("storage", section="write-path"))

    @app.post("/storage/shares")
    def storage_share_submit() -> ResponseReturnValue:
        share_path = request.form.get("share_path", "").strip()
        share_name = request.form.get("share_name", "").strip()
        share_type = request.form.get("share_type", "pool").strip().lower()
        default_policy_id = request.form.get("default_policy_id", "").strip()
        pool_ids_raw = request.form.get("pool_ids", "").strip()
        folder_mappings_raw = request.form.get("folder_mappings", "").strip()
        writable = request.form.get("writable", "").strip().lower() in {"1", "true", "yes", "on"}

        if not _is_safe_storage_path(share_path):
            flash("Share path must be an absolute normalized path.", "error")
            return redirect(url_for("storage", section="write-path"))
        if not _SHARE_NAME_ALLOWED.fullmatch(share_name):
            flash("Share name accepts letters, numbers, dot, underscore, and dash only.", "error")
            return redirect(url_for("storage", section="write-path"))
        if share_type not in _SHARE_TYPE_OPTIONS:
            flash("Share type is invalid.", "error")
            return redirect(url_for("storage", section="write-path"))
        if default_policy_id and not _POOL_ID_ALLOWED.fullmatch(default_policy_id):
            flash("Default policy ID is invalid.", "error")
            return redirect(url_for("storage", section="write-path"))

        pool_ids = _parse_csv_tokens(pool_ids_raw)
        for pool_id in pool_ids:
            if not _POOL_ID_ALLOWED.fullmatch(pool_id):
                flash(
                    "Pool IDs must contain only letters, numbers, dot, underscore, dash, and colon.",
                    "error",
                )
                return redirect(url_for("storage", section="write-path"))

        folder_mappings: list[dict[str, str]] = []
        for line_number, line in enumerate(folder_mappings_raw.splitlines(), start=1):
            rule = line.strip()
            if not rule:
                continue
            parts = [part.strip() for part in rule.split("|")]
            if len(parts) != 3:
                flash(
                    f"Folder mapping line {line_number} must use 'folder_path|pool_id|access_mode'.",
                    "error",
                )
                return redirect(url_for("storage", section="write-path"))
            folder_path, pool_id, access_mode = parts
            if not _is_safe_storage_path(folder_path):
                flash(f"Folder mapping line {line_number} has an invalid folder path.", "error")
                return redirect(url_for("storage", section="write-path"))
            if not _POOL_ID_ALLOWED.fullmatch(pool_id):
                flash(f"Folder mapping line {line_number} has an invalid pool ID.", "error")
                return redirect(url_for("storage", section="write-path"))
            if access_mode not in _SHARE_ACCESS_OPTIONS:
                flash(
                    f"Folder mapping line {line_number} access mode must be read_only or read_write.",
                    "error",
                )
                return redirect(url_for("storage", section="write-path"))
            folder_mappings.append(
                {
                    "folder_path": folder_path,
                    "pool_id": pool_id,
                    "access_mode": access_mode,
                }
            )

        if not pool_ids and folder_mappings:
            pool_ids = list(dict.fromkeys(item["pool_id"] for item in folder_mappings))
        known_pool_ids = set(pool_ids)
        for mapping in folder_mappings:
            if mapping["pool_id"] not in known_pool_ids:
                flash("Every folder mapping pool must be listed in pool_ids.", "error")
                return redirect(url_for("storage", section="write-path"))

        payload: dict[str, object] = {
            "path": share_path,
            "name": share_name,
            "share_type": share_type,
            "default_policy_id": default_policy_id or None,
            "pool_ids": pool_ids,
            "folder_mappings": folder_mappings,
            "writable": writable,
            "description": f"Share mapping for {share_name}",
        }
        try:
            _backend(app).create_nas_share(_token(), payload)
        except BackendError as error:
            flash(error.detail, "error")
            return redirect(url_for("storage", section="write-path"))
        flash("Share mapping saved.", "success")
        return redirect(url_for("storage", section="write-path"))

    @app.get("/jobs")
    def jobs() -> ResponseReturnValue:
        selected_id = _parse_int(request.args.get("library_id"))
        state = request.args.get("state", "").strip().lower() or None
        job_type = request.args.get("job_type", "").strip().lower() or None
        if state and state not in _JOB_STATES:
            state = None
        if job_type and job_type not in _JOB_TYPES:
            job_type = None
        try:
            devices = _backend(app).list_devices(_token())
            jobs_list = _backend(app).list_jobs(
                _token(), library_id=selected_id, state=state, job_type=job_type
            )
        except BackendError as error:
            return _handle_backend_error(error)
        has_active = any(
            str(item.get("state", "")).lower() in {"pending", "running"} for item in jobs_list
        )
        jobs_summary = {
            "total": len(jobs_list),
            "running": sum(
                1 for job in jobs_list if str(job.get("state", "")).lower() == "running"
            ),
            "pending": sum(
                1 for job in jobs_list if str(job.get("state", "")).lower() == "pending"
            ),
            "failed": sum(
                1 for job in jobs_list if str(job.get("state", "")).lower().startswith("failed")
            ),
        }
        return Response(
            render_template(
                "jobs/index.html",
                jobs=jobs_list,
                jobs_summary=jobs_summary,
                devices=devices,
                selected_library_id=selected_id,
                selected_state=state or "all",
                selected_job_type=job_type or "all",
                has_active=has_active,
                page_id="nas-jobs",
            ),
            status=200,
        )

    @app.get("/reports")
    def reports() -> ResponseReturnValue:
        selected_id = _parse_int(request.args.get("library_id"))
        severity = request.args.get("severity", "all").strip().lower() or "all"
        if severity not in {"all", "critical", "warning", "info"}:
            severity = "all"
        try:
            devices = _backend(app).list_devices(_token())
            active_device = _pick_device(devices, selected_id)
            events: list[dict[str, object]] = []
            event_summary: dict[str, object] = {}
            alert_summary: dict[str, object] = {}
            if active_device is not None:
                event_summary = _backend(app).get_event_summary(
                    _token(), library_id=active_device.id
                )
                alert_summary = _backend(app).get_alert_summary(
                    _token(), library_id=active_device.id
                )
                events = _backend(app).list_events(
                    _token(),
                    library_id=active_device.id,
                    severity=severity,
                    limit=50,
                )
        except BackendError as error:
            return _handle_backend_error(error)
        return Response(
            render_template(
                "reports/index.html",
                devices=devices,
                active_device=active_device,
                events=events,
                event_summary=event_summary,
                alert_summary=alert_summary,
                severity=severity,
                page_id="nas-reports",
            ),
            status=200,
        )

    @app.get("/system")
    def system() -> ResponseReturnValue:
        try:
            health = _backend(app).get_health(_token())
            config = _backend(app).get_system_config(_token())
            catalog_status = _backend(app).get_catalog_status(_token())
            system_status = _backend(app).get_system_status(_token())
            users = _backend(app).list_aml_users(_token())
        except BackendError as error:
            return _handle_backend_error(error)
        return Response(
            render_template(
                "system/index.html",
                health=health,
                config=config,
                catalog_status=catalog_status,
                system_status=system_status,
                users=users,
                page_id="nas-system",
            ),
            status=200,
        )

    @app.post("/system/users")
    def system_create_user() -> ResponseReturnValue:
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role_raw = request.form.get("role", "").strip()
        if not _DEVICE_USERNAME_ALLOWED.fullmatch(username):
            flash("User name accepts letters, numbers, dot, underscore, and dash.", "error")
            return redirect(url_for("system"))
        if len(password) < 8 or len(password) > 64:
            flash("Password must be between 8 and 64 characters.", "error")
            return redirect(url_for("system"))
        try:
            role = int(role_raw)
        except ValueError:
            flash("User role is invalid.", "error")
            return redirect(url_for("system"))
        if role not in {0, 1, 2}:
            flash("User role is invalid.", "error")
            return redirect(url_for("system"))
        try:
            _backend(app).create_aml_user(
                _token(),
                {
                    "name": username,
                    "password": password,
                    "role": role,
                },
            )
        except BackendError as error:
            flash(error.detail, "error")
            return redirect(url_for("system"))
        flash(f"User {username} created.", "success")
        return redirect(url_for("system"))

    @app.get("/events/devices")
    def devices_events() -> ResponseReturnValue:
        token = _token()
        once = request.args.get("once", "").lower() in {"1", "true", "yes"}

        def _stream() -> Iterator[str]:
            last_payload = ""
            while True:
                try:
                    devices = _backend(app).list_devices(token)
                    payload = json.dumps(
                        {
                            "summary": _summary(devices),
                            "devices": [asdict(item) for item in devices],
                            "updated_at": int(time.time()),
                        }
                    )
                    if payload != last_payload:
                        yield f"event: devices.snapshot\ndata: {payload}\n\n"
                        last_payload = payload
                    else:
                        yield ": keepalive\n\n"
                except BackendError as error:
                    yield f"event: devices.error\ndata: {json.dumps({'detail': error.detail})}\n\n"
                if once:
                    break
                time.sleep(10)

        headers = {
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
        return Response(
            stream_with_context(_stream()), mimetype="text/event-stream", headers=headers
        )

    @app.get("/events/jobs")
    def jobs_events() -> ResponseReturnValue:
        token = _token()
        once = request.args.get("once", "").lower() in {"1", "true", "yes"}

        def _stream() -> Iterator[str]:
            last_payload = ""
            while True:
                try:
                    jobs_list = _backend(app).list_jobs(token)
                    running = sum(
                        1 for job in jobs_list if str(job.get("state", "")).lower() == "running"
                    )
                    pending = sum(
                        1 for job in jobs_list if str(job.get("state", "")).lower() == "pending"
                    )
                    failed = sum(
                        1
                        for job in jobs_list
                        if str(job.get("state", "")).lower().startswith("failed")
                    )
                    payload = json.dumps(
                        {
                            "total": len(jobs_list),
                            "running": running,
                            "pending": pending,
                            "failed": failed,
                            "updated_at": int(time.time()),
                        }
                    )
                    if payload != last_payload:
                        yield f"event: jobs.snapshot\ndata: {payload}\n\n"
                        last_payload = payload
                    else:
                        yield ": keepalive\n\n"
                except BackendError as error:
                    yield f"event: jobs.error\ndata: {json.dumps({'detail': error.detail})}\n\n"
                if once:
                    break
                time.sleep(8)

        headers = {
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
        return Response(
            stream_with_context(_stream()), mimetype="text/event-stream", headers=headers
        )

    @app.post("/devices/probe")
    def devices_probe() -> ResponseReturnValue:
        connection_url = request.form.get("connection_url", "").strip()
        device_username = request.form.get("device_username", "").strip()
        device_password = request.form.get("device_password", "")
        connection_error = _validate_device_url(connection_url)
        if connection_error:
            response = jsonify({"ok": False, "detail": connection_error})
            response.status_code = 400
            return response
        if device_username and not _DEVICE_USERNAME_ALLOWED.fullmatch(device_username):
            response = jsonify({"ok": False, "detail": "Invalid device username format"})
            response.status_code = 400
            return response
        if device_password and not device_username:
            response = jsonify(
                {"ok": False, "detail": "Username is required when password is provided"}
            )
            response.status_code = 400
            return response
        try:
            probe = _backend(app).probe_device_endpoint(
                connection_url=connection_url,
                username=device_username or None,
                password=device_password or None,
            )
        except BackendError as error:
            response = jsonify({"ok": False, "detail": error.detail})
            response.status_code = 400
            return response
        response = jsonify(
            {"ok": True, "detail": "Connection and auth probe passed", "probe": probe}
        )
        response.status_code = 200
        return response

    return app


app = create_app()
