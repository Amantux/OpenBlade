"""iBlade compatibility routes."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Coroutine
from csv import DictWriter
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from io import StringIO
from ipaddress import AddressValueError, IPv4Address
from typing import Any
from uuid import uuid4
from xml.etree import ElementTree

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response, status
from fastapi.routing import APIRoute
from pydantic import ValidationError

from openblade.api import aml_state
from openblade.api.routes_aml_auth import _ensure_state, require_auth
from openblade.bootstrap import AppContext, get_context
from openblade.catalog.models import AmlUser
from openblade.config import IBladeCompatibilityMode
from openblade.nas.iblade_types import (
    CodeDescription,
    IBladeAssignmentOperationRequest,
    IBladeHost,
    IBladeHostUpdate,
    IBladeIoStatus,
    IBladeJob,
    IBladeJobResponse,
    IBladeJobStateUpdate,
    IBladeJobsUpdateRequest,
    IBladeMergeOperationRequest,
    IBladeMessage,
    IBladeMessageCloseRequest,
    IBladeMessagesCloseRequest,
    IBladeNasDrive,
    IBladeNetworkConfig,
    IBladeNetworkUpdate,
    IBladePrepareExportOperationRequest,
    IBladeProductElement,
    IBladeProductInfo,
    IBladeRepairOperationRequest,
    IBladeReplicateOperationRequest,
    IBladeReport,
    IBladeSafeRepairOperationRequest,
    IBladeSetting,
    IBladeVolumeGroup,
)

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH"})
_ROUTE_MATCH_METHODS = frozenset({"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"})
_XML_TAG_INVALID_CHARACTERS = re.compile(r"[^A-Za-z0-9_.-]+")
_XML_TAG_INVALID_PREFIX = re.compile(r"^[^A-Za-z_]+")
_EXTENDED_ONLY_PATHS = frozenset({"/system/extended-snapshot"})
_STRICT_SYSTEM_SETTING_KEYS = frozenset(aml_state.get_iblade_system_settings().keys())
_VALID_VOLUME_GROUP_POLICIES = frozenset(
    {"balanced", "standard", "archive", "replica", "protected"}
)
_PROTECTED_VOLUME_GROUP_INDEXES = frozenset({1})
_MEDIA_STATE_TRANSITIONS: dict[str, frozenset[str]] = {
    "home": frozenset({"home", "stored", "loaded", "scratch", "sequestered"}),
    "stored": frozenset({"stored", "home", "loaded", "sequestered"}),
    "loaded": frozenset({"loaded", "home", "stored"}),
    "scratch": frozenset({"scratch", "home", "sequestered"}),
    "sequestered": frozenset({"sequestered", "formatted", "home"}),
    "formatted": frozenset({"formatted", "exported", "home"}),
    "exported": frozenset({"exported", "home"}),
}
_NETWORK_PORT_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


def _request_has_body(request: Request) -> bool:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            return int(content_length) > 0
        except ValueError:
            return True
    transfer_encoding = request.headers.get("transfer-encoding", "")
    return "chunked" in transfer_encoding.lower()


def _is_json_content_type(content_type: str) -> bool:
    media_type = content_type.split(";", 1)[0].strip().lower()
    return media_type == "application/json" or media_type.endswith("+json")


def _enforce_mutating_content_type(request: Request) -> None:
    if request.method.upper() not in _MUTATING_METHODS or not _request_has_body(request):
        return
    if _is_json_content_type(request.headers.get("content-type", "")):
        return
    raise HTTPException(
        status_code=415, detail="Content-Type must be application/json for request bodies"
    )


def _parse_accept_header(value: str) -> list[tuple[str, float]]:
    accepts: list[tuple[str, float]] = []
    for token in value.split(","):
        chunk = token.strip()
        if not chunk:
            continue
        segments = [segment.strip() for segment in chunk.split(";") if segment.strip()]
        if not segments:
            continue
        media_type = segments[0].lower()
        quality = 1.0
        for segment in segments[1:]:
            if not segment.lower().startswith("q="):
                continue
            try:
                quality = float(segment.split("=", 1)[1])
            except ValueError:
                quality = 0.0
            break
        accepts.append((media_type, quality))
    return accepts


def _matches_xml_media_type(media_type: str) -> bool:
    return media_type in {
        "application/xml",
        "text/xml",
        "application/*",
        "text/*",
        "*/*",
    } or media_type.endswith("+xml")


def _matches_json_media_type(media_type: str) -> bool:
    return media_type in {"application/json", "application/*", "*/*"} or media_type.endswith(
        "+json"
    )


def _matches_csv_media_type(media_type: str) -> bool:
    return media_type in {"text/csv", "application/csv", "text/*"} or media_type.endswith("+csv")


def _compat_mode(context: AppContext) -> IBladeCompatibilityMode:
    mode = getattr(context.config, "iblade_compat_mode", IBladeCompatibilityMode.EXTENDED)
    if isinstance(mode, IBladeCompatibilityMode):
        return mode
    normalized = str(getattr(mode, "value", mode)).strip().lower()
    if normalized == IBladeCompatibilityMode.STRICT.value:
        return IBladeCompatibilityMode.STRICT
    return IBladeCompatibilityMode.EXTENDED


def _is_strict_interface(context: AppContext) -> bool:
    return _compat_mode(context) is IBladeCompatibilityMode.STRICT


def _strict_interface_path_allowed(path_suffix: str) -> bool:
    normalized_suffix = _normalize_gateway_suffix(path_suffix)
    return normalized_suffix not in _EXTENDED_ONLY_PATHS


def _wants_xml_response(request: Request) -> bool:
    accept_header = request.headers.get("accept", "")
    if not accept_header:
        return False
    accepts = _parse_accept_header(accept_header)
    if not accepts:
        return False
    xml_quality = max(
        (quality for media_type, quality in accepts if _matches_xml_media_type(media_type)),
        default=0.0,
    )
    json_quality = max(
        (quality for media_type, quality in accepts if _matches_json_media_type(media_type)),
        default=0.0,
    )
    return xml_quality > 0 and xml_quality > json_quality


def _wants_csv_response(request: Request) -> bool:
    accept_header = request.headers.get("accept", "")
    if not accept_header:
        return False
    accepts = _parse_accept_header(accept_header)
    if not accepts:
        return False
    csv_quality = max(
        (quality for media_type, quality in accepts if _matches_csv_media_type(media_type)),
        default=0.0,
    )
    json_quality = max(
        (quality for media_type, quality in accepts if _matches_json_media_type(media_type)),
        default=0.0,
    )
    xml_quality = max(
        (quality for media_type, quality in accepts if _matches_xml_media_type(media_type)),
        default=0.0,
    )
    return csv_quality > 0 and csv_quality > max(json_quality, xml_quality)


def _normalize_xml_tag(value: str) -> str:
    normalized = _XML_TAG_INVALID_CHARACTERS.sub("_", value.strip())
    normalized = _XML_TAG_INVALID_PREFIX.sub("", normalized)
    return normalized or "field"


def _append_xml_value(parent: ElementTree.Element, value: object) -> None:
    if isinstance(value, dict):
        for key, nested_value in value.items():
            child = ElementTree.SubElement(parent, _normalize_xml_tag(str(key)))
            _append_xml_value(child, nested_value)
        return
    if isinstance(value, list):
        for nested_value in value:
            child = ElementTree.SubElement(parent, "item")
            _append_xml_value(child, nested_value)
        return
    if value is None:
        return
    if isinstance(value, bool):
        parent.text = "true" if value else "false"
        return
    parent.text = str(value)


def _json_payload_to_xml(payload: object) -> bytes:
    root = ElementTree.Element("response")
    _append_xml_value(root, payload)
    return bytes(ElementTree.tostring(root, encoding="utf-8", xml_declaration=True))


def _report_payload_to_csv(payload: object) -> str:
    if not isinstance(payload, dict):
        return "value\n"
    items = payload.get("items")
    if isinstance(items, list) and items:
        rows: list[dict[str, str]] = []
        fieldnames: list[str] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            row: dict[str, str] = {}
            for key, value in item.items():
                normalized_key = str(key)
                if normalized_key not in seen:
                    seen.add(normalized_key)
                    fieldnames.append(normalized_key)
                row[normalized_key] = (
                    json.dumps(value, separators=(",", ":"), sort_keys=True)
                    if isinstance(value, (dict, list))
                    else str(value)
                )
            rows.append(row)
        if rows and fieldnames:
            buffer = StringIO()
            writer = DictWriter(buffer, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
            return buffer.getvalue()

    summary = payload.get("summary")
    if isinstance(summary, dict) and summary:
        buffer = StringIO()
        writer = DictWriter(buffer, fieldnames=["key", "value"])
        writer.writeheader()
        for key, value in summary.items():
            writer.writerow(
                {
                    "key": str(key),
                    "value": json.dumps(value, separators=(",", ":"), sort_keys=True)
                    if isinstance(value, (dict, list))
                    else str(value),
                }
            )
        return buffer.getvalue()
    return "key,value\n"


def _negotiate_iblade_response(request: Request, response: Response) -> Response:
    if _wants_csv_response(request):
        if request.method.upper() != "GET":
            response.headers["X-OpenBlade-Content-Negotiation"] = (
                "json-fallback; reason=csv-write-not-supported"
            )
            return response
        if not request.url.path.endswith(
            (
                "/reports/configuration",
                "/reports/media",
                "/reports/media-count",
                "/reports/volume-groups",
                "/system/extended-snapshot",
            )
        ):
            response.headers["X-OpenBlade-Content-Negotiation"] = (
                "json-fallback; reason=csv-not-supported"
            )
            return response
        if "application/json" not in response.headers.get("content-type", ""):
            response.headers["X-OpenBlade-Content-Negotiation"] = (
                "json-fallback; reason=unsupported-response-type"
            )
            return response
        try:
            payload = json.loads(bytes(response.body))
            csv_payload = _report_payload_to_csv(payload)
        except Exception:
            response.headers["X-OpenBlade-Content-Negotiation"] = (
                "json-fallback; reason=csv-serialization-failed"
            )
            return response
        headers = dict(response.headers)
        headers.pop("content-length", None)
        headers.pop("content-type", None)
        return Response(
            content=csv_payload.encode("utf-8"),
            status_code=response.status_code,
            headers=headers,
            media_type="text/csv",
        )
    if not _wants_xml_response(request):
        return response
    if request.method.upper() != "GET":
        response.headers["X-OpenBlade-Content-Negotiation"] = (
            "json-fallback; reason=xml-write-not-supported"
        )
        return response
    if "application/json" not in response.headers.get("content-type", ""):
        response.headers["X-OpenBlade-Content-Negotiation"] = (
            "json-fallback; reason=unsupported-response-type"
        )
        return response
    try:
        payload = json.loads(bytes(response.body))
        xml_payload = _json_payload_to_xml(payload)
    except Exception:
        response.headers["X-OpenBlade-Content-Negotiation"] = (
            "json-fallback; reason=xml-serialization-failed"
        )
        return response
    headers = dict(response.headers)
    headers.pop("content-length", None)
    headers.pop("content-type", None)
    return Response(
        content=xml_payload,
        status_code=response.status_code,
        headers=headers,
        media_type="application/xml",
    )


class IBladeNegotiatedRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        route_handler = super().get_route_handler()

        async def negotiated_route_handler(request: Request) -> Response:
            _enforce_mutating_content_type(request)
            response = await route_handler(request)
            return _negotiate_iblade_response(request, response)

        return negotiated_route_handler


router = APIRouter(route_class=IBladeNegotiatedRoute)
_STRICT_BLADE_URI_PATTERN = re.compile(
    r"^/aml/devices/blade/(?P<blade_type>ltfs|windows)/(?P<section_number>[^/]+)(?P<suffix>/.+)$"
)

_STATE_CODES = [
    {"code": "READY", "description": "Element is ready for normal library operations."},
    {"code": "IN_USE", "description": "Element is actively participating in an operation."},
    {"code": "LOADED", "description": "Tape is loaded in a drive."},
    {"code": "UNLOADED", "description": "Tape is not mounted in a drive."},
    {"code": "OFFLINE", "description": "Element is administratively offline."},
    {"code": "ERROR", "description": "Element is faulted and requires attention."},
]
_VOLUME_STATES = [
    {"code": "SCRATCH", "description": "Volume is available for assignment."},
    {"code": "ASSIGNED", "description": "Volume is assigned to a volume group."},
    {"code": "EXPORTED", "description": "Volume is staged for export or already exported."},
    {"code": "FULL", "description": "Volume has no remaining usable capacity."},
]
_VG_STATES = [
    {"code": "READY", "description": "Volume group is online and consistent."},
    {"code": "DEGRADED", "description": "Volume group is accessible with warnings."},
    {"code": "REPAIRING", "description": "Volume group is being repaired."},
    {"code": "OFFLINE", "description": "Volume group is unavailable."},
]
_JOB_STATES = [
    {"code": "queued", "description": "Job is queued and waiting to run."},
    {"code": "active", "description": "Job is currently running."},
    {"code": "running", "description": "Job is actively transferring data."},
    {"code": "paused", "description": "Job is paused and can be resumed."},
    {"code": "cancelled", "description": "Job was cancelled by an operator request."},
    {"code": "completed", "description": "Job completed successfully."},
    {"code": "failed", "description": "Job failed and requires review."},
]
_REASON_CODES = [
    {"code": "NONE", "description": "No exceptional reason is currently recorded."},
    {"code": "OPERATOR_REQUEST", "description": "State change was requested by an operator."},
    {"code": "MAINTENANCE", "description": "State change is due to maintenance activity."},
    {"code": "HARDWARE_EVENT", "description": "A hardware condition triggered the state change."},
]
_VG_REASON_CODES = [
    {"code": "NONE", "description": "Volume group is healthy."},
    {"code": "INCOMPLETE_ASSIGNMENT", "description": "Expected media are missing from the group."},
    {"code": "REPLICATION_PENDING", "description": "Replication is scheduled or in progress."},
    {"code": "REPAIR_REQUIRED", "description": "Metadata needs repair before the group is usable."},
]


@lru_cache(maxsize=1)
def _iblade_route_patterns_by_method() -> dict[str, tuple[re.Pattern[str], ...]]:
    patterns: dict[str, list[re.Pattern[str]]] = {}
    for route in router.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods or set():
            if method not in {"GET", "POST", "PUT", "DELETE", "PATCH"}:
                continue
            patterns.setdefault(method, []).append(route.path_regex)
    return {method: tuple(method_patterns) for method, method_patterns in patterns.items()}


def _normalize_gateway_suffix(path_suffix: str) -> str:
    if path_suffix != "/" and path_suffix.endswith("/"):
        return path_suffix.rstrip("/")
    return path_suffix


def _matches_iblade_route(method: str, path_suffix: str) -> bool:
    normalized_suffix = _normalize_gateway_suffix(path_suffix)
    for pattern in _iblade_route_patterns_by_method().get(method.upper(), ()):
        if pattern.match(normalized_suffix):
            return True
    return False


@lru_cache(maxsize=1)
def _iblade_route_patterns() -> tuple[re.Pattern[str], ...]:
    patterns: list[re.Pattern[str]] = []
    for route in router.routes:
        if isinstance(route, APIRoute):
            patterns.append(route.path_regex)
    return tuple(patterns)


def _matches_iblade_route_path(path_suffix: str) -> bool:
    normalized_suffix = _normalize_gateway_suffix(path_suffix)
    return any(pattern.match(normalized_suffix) for pattern in _iblade_route_patterns())


def resolve_strict_blade_uri_alias(
    method: str, path: str, *, strict_interface: bool = False
) -> tuple[str, int, str] | None:
    match = _STRICT_BLADE_URI_PATTERN.match(path)
    if match is None:
        return None

    suffix = _normalize_gateway_suffix(match.group("suffix"))
    if strict_interface and not _strict_interface_path_allowed(suffix):
        return None
    normalized_method = method.upper()
    if not _matches_iblade_route(normalized_method, suffix) and (
        normalized_method not in _ROUTE_MATCH_METHODS or not _matches_iblade_route_path(suffix)
    ):
        return None

    section_number_raw = match.group("section_number").strip()
    try:
        section_number = int(section_number_raw)
    except ValueError as error:
        raise HTTPException(
            status_code=422, detail="sectionNumber must be a positive integer"
        ) from error
    if section_number <= 0:
        raise HTTPException(status_code=422, detail="sectionNumber must be a positive integer")
    blade_type = match.group("blade_type")
    if blade_type == "windows":
        if aml_state.get_aml_windows_section(section_number) is None:
            raise HTTPException(status_code=404, detail=f"Section {section_number} not found")
    elif aml_state.get_aml_ltfs_section(section_number) is None:
        raise HTTPException(status_code=404, detail=f"Section {section_number} not found")
    return blade_type, section_number, f"/iblade{suffix}"


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_identifier(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail=f"{field_name} is required")
    return normalized


def _parse_int_field(data: dict[str, Any], *, field_name: str, default: int) -> int:
    value = data.get(field_name, default)
    if value is None:
        return default
    if isinstance(value, bool):
        raise HTTPException(status_code=400, detail=f"{field_name} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if normalized and (
            normalized.isdigit() or (normalized.startswith("-") and normalized[1:].isdigit())
        ):
            return int(normalized)
    raise HTTPException(status_code=400, detail=f"{field_name} must be an integer")


def _model_bad_request(model_name: str, error: ValidationError) -> HTTPException:
    details = "; ".join(err.get("msg", "Invalid value") for err in error.errors()) or "Invalid value"
    details = details.replace("Input should be a valid integer", "must be an integer")
    return HTTPException(status_code=400, detail=f"{model_name}: {details}")


def _parse_model_payload(model: type[Any], payload: dict[str, Any] | None, *, model_name: str) -> Any:
    try:
        return model.model_validate(payload or {})
    except ValidationError as error:
        raise _model_bad_request(model_name, error) from error


def _normalize_media_state(value: object) -> str:
    normalized = str(value).strip().lower()
    if not normalized:
        raise HTTPException(status_code=400, detail="state cannot be empty")
    if normalized not in _MEDIA_STATE_TRANSITIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported media state {normalized}")
    return normalized


def _validate_media_state_transition(barcode: str, current_state: object, target_state: object) -> None:
    normalized_current = _normalize_media_state(current_state)
    normalized_target = _normalize_media_state(target_state)
    allowed = _MEDIA_STATE_TRANSITIONS.get(normalized_current, frozenset())
    if normalized_target not in allowed:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Media {barcode} transition from {normalized_current} "
                f"to {normalized_target} is not allowed"
            ),
        )


def _validate_media_update_item(
    barcode: str, existing: dict[str, Any], updates: dict[str, Any]
) -> dict[str, Any]:
    normalized_updates = {key: value for key, value in updates.items() if key != "barcode"}
    if "state" in normalized_updates and normalized_updates["state"] is not None:
        _validate_media_state_transition(barcode, existing.get("state", "home"), normalized_updates["state"])
        normalized_updates["state"] = _normalize_media_state(normalized_updates["state"])
    return normalized_updates


def _validated_media_batch_updates(
    payload_items: list[dict[str, Any]],
) -> list[tuple[str, dict[str, Any]]]:
    if not payload_items:
        return []
    validated: list[tuple[str, dict[str, Any]]] = []
    seen_barcodes: set[str] = set()
    for item in payload_items:
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail="lto-media entries must be objects")
        barcode = str(item.get("barcode", "")).strip().upper()
        if not barcode:
            raise HTTPException(status_code=400, detail="barcode is required")
        if barcode in seen_barcodes:
            raise HTTPException(status_code=400, detail=f"Duplicate media barcode {barcode}")
        seen_barcodes.add(barcode)
        existing = aml_state.get_aml_media(barcode)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"Media {barcode} not found")
        validated.append((barcode, _validate_media_update_item(barcode, existing, item)))
    return validated


def _serialize_nas_drives() -> list[IBladeNasDrive]:
    drives: list[IBladeNasDrive] = []
    for drive in aml_state.list_aml_drives():
        drives.append(
            IBladeNasDrive(
                serialNumber=str(drive.get("serialNumber", "")),
                model=str(drive.get("model", "")),
                status=str(drive.get("status", "unknown")),
                state=str(drive.get("state", "unknown")),
            )
        )
    return drives


def _normalized_message_close_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    if "closed_by" in payload:
        normalized["closed_by"] = payload.get("closed_by")
    elif "closedBy" in payload:
        normalized["closed_by"] = payload.get("closedBy")
    if "close_all" in payload:
        normalized["close_all"] = payload.get("close_all")
    elif "closeAll" in payload:
        normalized["close_all"] = payload.get("closeAll")
    if "ids" in payload:
        normalized["ids"] = payload.get("ids")
    elif "messages" in payload and isinstance(payload["messages"], list):
        normalized["ids"] = [item.get("id") for item in payload["messages"] if isinstance(item, dict)]
    return normalized


def _close_message(message_id: str, *, closed_by: str) -> IBladeMessage:
    message = _message_or_404(_validate_identifier(message_id, field_name="message id"))
    updated = aml_state.update_iblade_message(
        str(message["id"]),
        {"acknowledged": True, "closed_by": closed_by, "closed_at": _timestamp()},
    ) or {**message, "acknowledged": True, "closed_by": closed_by, "closed_at": _timestamp()}
    return IBladeMessage.model_validate(updated)


def _extract_message_ids(payload: dict[str, Any]) -> list[str]:
    close_all = bool(payload.get("close_all") or payload.get("closeAll"))
    if close_all:
        return [str(item.id) for item in _sorted_open_messages()]
    if "ids" in payload and isinstance(payload["ids"], list):
        source_ids = payload["ids"]
    elif "messages" in payload and isinstance(payload["messages"], list):
        source_ids = [item.get("id") for item in payload["messages"] if isinstance(item, dict)]
    else:
        source_ids = []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in source_ids:
        message_id = str(item).strip()
        if not message_id or message_id in seen:
            continue
        seen.add(message_id)
        normalized.append(message_id)
    if not normalized and not close_all:
        raise HTTPException(status_code=400, detail="ids or close_all is required")
    return normalized


def _validate_ipv4(value: object, *, field_name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise HTTPException(status_code=400, detail=f"{field_name} is required")
    try:
        IPv4Address(normalized)
    except AddressValueError as error:
        raise HTTPException(status_code=400, detail=f"{field_name} must be a valid IPv4 address") from error
    return normalized


def _validated_network_updates(payload: dict[str, Any] | None) -> dict[str, Any]:
    model = _parse_model_payload(IBladeNetworkUpdate, payload or {}, model_name="network payload")
    updates: dict[str, Any] = dict(model.model_dump(exclude_none=True))
    for field_name in ("management_ip", "subnet_mask", "gateway"):
        if field_name in updates:
            updates[field_name] = _validate_ipv4(updates[field_name], field_name=field_name)
    dns_value = updates.get("dns")
    if dns_value is not None:
        if len(dns_value) > 4:
            raise HTTPException(status_code=400, detail="dns must contain at most 4 entries")
        updates["dns"] = [_validate_ipv4(entry, field_name="dns entry") for entry in dns_value]
    return updates


def _validate_network_path_fields(port: str, version: str) -> tuple[str, int]:
    normalized_port = port.strip()
    if not normalized_port or _NETWORK_PORT_PATTERN.fullmatch(normalized_port) is None:
        raise HTTPException(status_code=400, detail="port must be alphanumeric")
    try:
        normalized_version = int(version)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="version must be a positive integer") from error
    if normalized_version <= 0:
        raise HTTPException(status_code=400, detail="version must be a positive integer")
    return normalized_port, normalized_version


def _apply_network_update(payload: dict[str, Any] | None, *, port: str | None = None, version: int | None = None) -> IBladeNetworkConfig:
    updates = _validated_network_updates(payload)
    if port is not None and version is not None:
        updates["configurationPort"] = port
        updates["configurationVersion"] = version
        updates["configurationUpdatedAt"] = _timestamp()
    return IBladeNetworkConfig.model_validate(aml_state.set_iblade_network_config(updates))


def _formatted_volume_group_tapes(group: dict[str, Any]) -> list[str]:
    formatted: list[str] = []
    for tape in group.get("tapes", []):
        barcode = str(tape).strip().upper()
        if not barcode:
            continue
        media = aml_state.get_aml_media(barcode)
        if media is None:
            raise HTTPException(status_code=404, detail=f"Media {barcode} not found")
        if str(media.get("state", "")).lower() != "formatted":
            raise HTTPException(
                status_code=409,
                detail=f"Media {barcode} must be in formatted state for prepare-export",
            )
        formatted.append(barcode)
    if not formatted:
        raise HTTPException(status_code=409, detail="Volume group has no media to export")
    return formatted

def _queue_job(
    job_type: str, message: str, metadata: dict[str, Any] | None = None
) -> IBladeJobResponse:
    job_id = str(uuid4())
    aml_state.set_aml_job(
        job_id,
        {
            "type": job_type,
            "status": "queued",
            "progress": 0,
            "requestedAt": _timestamp(),
            "result": message,
            "metadata": metadata or {},
        },
    )
    return IBladeJobResponse(job_id=job_id, status="queued", message=message)


def _product_info() -> IBladeProductInfo:
    firmware = aml_state.get_system_firmware_info().get("currentVersion", "6.0.1")
    return IBladeProductInfo(
        product="OpenBlade iBlade",
        model="Scalar i3",
        serial="MOCK-I3-001",
        firmware=str(firmware),
        software="0.1.0",
        vendor="Quantum",
        build="20240115.1",
    )


def _message_or_404(message_id: str) -> dict[str, Any]:
    message = aml_state.get_iblade_message(message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return message


def _volume_group_or_404(index: int) -> dict[str, Any]:
    group = aml_state.get_iblade_volume_group(index)
    if group is None:
        raise HTTPException(status_code=404, detail="Volume group not found")
    return group


def _sorted_open_messages() -> list[IBladeMessage]:
    messages = [
        IBladeMessage.model_validate(item)
        for item in aml_state.list_iblade_messages()
        if not bool(item.get("acknowledged", False))
    ]
    return sorted(messages, key=lambda item: item.created_at, reverse=True)


def _code_description_or_404(
    items: list[dict[str, str]], code: str, *, field_name: str = "code"
) -> CodeDescription:
    normalized = _validate_identifier(code, field_name=field_name).upper()
    for item in items:
        if str(item.get("code", "")).upper() == normalized:
            return CodeDescription.model_validate(item)
    raise HTTPException(status_code=404, detail=f"{field_name} {code} not found")


def _serialize_hosts() -> list[IBladeHost]:
    return [IBladeHost.model_validate(item) for item in aml_state.list_iblade_hosts()]


def _host_by_ip_or_404(ip: str) -> dict[str, Any]:
    normalized = _validate_identifier(ip, field_name="ip")
    for host in aml_state.list_iblade_hosts():
        if str(host.get("ip", "")).strip() == normalized:
            return host
    raise HTTPException(status_code=404, detail="Host not found")


def _next_host_id() -> str:
    existing_hosts = aml_state.list_iblade_hosts()
    existing_ids = {str(item.get("id", "")) for item in existing_hosts}
    max_suffix = 0
    for host_id in existing_ids:
        normalized = host_id.strip().upper()
        if not normalized.startswith("HOST-"):
            continue
        suffix = normalized.removeprefix("HOST-")
        if suffix.isdigit():
            max_suffix = max(max_suffix, int(suffix))
    candidate_suffix = max_suffix + 1
    candidate = f"HOST-{candidate_suffix:03d}"
    while candidate in existing_ids:
        candidate_suffix += 1
        candidate = f"HOST-{candidate_suffix:03d}"
    return candidate


def _serialize_job(job: dict[str, Any]) -> IBladeJob:
    opened = str(job.get("requestedAt") or job.get("opened") or _timestamp())
    closed = job.get("closedAt") or job.get("closed")
    progress_value = job.get("progress", 0)
    try:
        progress = int(progress_value) if isinstance(progress_value, (int, float, str)) else 0
    except (TypeError, ValueError):
        progress = 0
    return IBladeJob(
        id=str(job.get("id", "")),
        type=str(job.get("type", "generic")),
        status=str(job.get("status", "queued")),
        opened=opened,
        closed=(str(closed) if closed is not None else None),
        description=str(job.get("result") or job.get("description") or ""),
        progress=progress,
        metadata=dict(job.get("metadata", {})) if isinstance(job.get("metadata"), dict) else {},
    )


def _normalize_job_state(value: str | int) -> str:
    if isinstance(value, int):
        numeric_states = {
            0: "queued",
            1: "active",
            2: "completed",
            3: "cancelled",
            4: "failed",
        }
        target = numeric_states.get(value)
        if target is None:
            raise HTTPException(status_code=400, detail="Unsupported numeric job_state value")
        return target
    normalized = str(value).strip().lower()
    allowed = {"queued", "active", "running", "paused", "cancelled", "completed", "failed"}
    if normalized not in allowed:
        raise HTTPException(status_code=400, detail="Unsupported job_state value")
    return normalized


def _validate_job_transition(job: dict[str, Any], target_status: str) -> None:
    current_status = str(job.get("status", "queued")).lower()
    transitions: dict[str, set[str]] = {
        "queued": {"queued", "active", "running", "paused", "cancelled", "completed", "failed"},
        "active": {"active", "running", "paused", "cancelled", "completed", "failed"},
        "running": {"running", "active", "paused", "cancelled", "completed", "failed"},
        "paused": {"paused", "active", "running", "cancelled", "failed"},
        "cancelled": {"cancelled"},
        "completed": {"completed"},
        "failed": {"failed"},
    }
    allowed = transitions.get(current_status)
    if allowed is None:
        raise HTTPException(status_code=400, detail="Unsupported current job status")
    if target_status not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Job transition from {current_status} to {target_status} is not allowed",
        )


def _job_update_payload(job: dict[str, Any], target_status: str) -> dict[str, Any]:
    payload: dict[str, Any] = {"status": target_status}
    terminal_states = {"cancelled", "completed", "failed"}
    current_status = str(job.get("status", "queued")).lower()
    if target_status in terminal_states and current_status not in terminal_states:
        payload["closedAt"] = _timestamp()
    return payload


def _filter_jobs_for_completed_window(
    jobs: list[IBladeJob], completed_days: int | None
) -> list[IBladeJob]:
    if completed_days is None:
        return jobs
    active_states = {"queued", "active", "running", "paused"}
    terminal_states = {"cancelled", "completed", "failed"}
    cutoff = datetime.now(timezone.utc) - timedelta(days=completed_days)
    filtered: list[IBladeJob] = []
    for job in jobs:
        status_value = job.status.lower()
        if status_value in active_states:
            filtered.append(job)
            continue
        if status_value not in terminal_states or not job.closed:
            continue
        try:
            closed_dt = datetime.fromisoformat(job.closed.replace("Z", "+00:00"))
        except ValueError:
            continue
        if closed_dt >= cutoff:
            filtered.append(job)
    return filtered


def _serialize_volume_groups() -> list[IBladeVolumeGroup]:
    return [
        IBladeVolumeGroup.model_validate(item) for item in aml_state.list_iblade_volume_groups()
    ]


def _normalize_setting_key(settings: dict[str, Any], settingname: str) -> str:
    key = _validate_identifier(settingname, field_name="settingname")
    if key in settings:
        return key
    lowered = {name.lower(): name for name in settings}
    return lowered.get(key.lower(), key)


def _validate_strict_system_settings_payload(
    payload: dict[str, Any], settings: dict[str, Any]
) -> None:
    for key in payload:
        normalized_key = _normalize_setting_key(settings, str(key))
        if normalized_key not in settings or normalized_key not in _STRICT_SYSTEM_SETTING_KEYS:
            raise HTTPException(status_code=400, detail=f"Unsupported system setting {key}")


def _strict_system_settings(settings: dict[str, Any]) -> dict[str, Any]:
    return {key: settings[key] for key in settings if key in _STRICT_SYSTEM_SETTING_KEYS}


def _parse_report_criteria(raw: str | dict[str, Any] | None) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    normalized = raw.strip()
    if not normalized:
        return {}
    try:
        payload = json.loads(normalized)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=400, detail="reportCriteria must be valid JSON") from error
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="reportCriteria must be a JSON object")
    return payload


def _normalize_volume_group_name(value: object, *, fallback: str) -> str:
    candidate = str(value).strip() if value is not None else fallback
    if not candidate:
        candidate = fallback
    if len(candidate) > 64:
        raise HTTPException(
            status_code=400, detail="Volume-group name must be 64 characters or fewer"
        )
    return candidate


def _normalize_volume_group_policy(value: object, *, fallback: str) -> str:
    policy = str(value).strip().lower() if value is not None else fallback
    if policy not in _VALID_VOLUME_GROUP_POLICIES:
        raise HTTPException(status_code=400, detail=f"Unsupported volume-group policy {policy}")
    return policy


def _known_media_barcodes() -> set[str]:
    return {str(item.get("barcode", "")).strip().upper() for item in aml_state.list_aml_media()}


def _normalize_volume_group_tapes(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise HTTPException(status_code=400, detail="tapes must be a list")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        barcode = str(item).strip().upper()
        if not barcode:
            continue
        if barcode in seen:
            continue
        seen.add(barcode)
        normalized.append(barcode)
    return normalized


def _validate_volume_group_tapes_exist(tapes: list[str]) -> None:
    known = _known_media_barcodes()
    for barcode in tapes:
        if barcode not in known:
            raise HTTPException(status_code=400, detail=f"Unknown tape barcode {barcode}")


def _validate_unique_tape_assignments(groups: list[dict[str, Any]]) -> None:
    seen: dict[str, int] = {}
    for group in groups:
        index = int(group["index"])
        for tape in group.get("tapes", []):
            current = str(tape)
            if current in seen:
                raise HTTPException(
                    status_code=400,
                    detail=f"Tape {current} already assigned to volume group {seen[current]}",
                )
            seen[current] = index


def _is_protected_volume_group(index: int) -> bool:
    return index in _PROTECTED_VOLUME_GROUP_INDEXES


def _configuration_report(criteria: dict[str, Any] | None = None) -> IBladeReport:
    report_criteria = criteria or {}
    product = _product_info().model_dump()
    network = aml_state.get_iblade_network_config()
    partitions = aml_state.list_aml_partitions()
    drives = aml_state.list_aml_drives()
    items: list[dict[str, Any]] = [
        {"section": "product", "data": product},
        {"section": "network", "data": network},
        {"section": "partitions", "data": partitions},
        {"section": "drives", "data": drives},
    ]
    sections = report_criteria.get("sections")
    if isinstance(sections, list):
        requested = {str(item).strip().lower() for item in sections if str(item).strip()}
        if requested:
            items = [item for item in items if str(item.get("section", "")).lower() in requested]
    return IBladeReport(
        generated_at=_timestamp(),
        items=items,
        summary={
            "partitionCount": len(partitions),
            "driveCount": len(drives),
            "hostname": network.get("hostname"),
            "reportCriteria": report_criteria,
        },
    )


def _media_report(criteria: dict[str, Any] | None = None) -> IBladeReport:
    report_criteria = criteria or {}
    media = aml_state.list_aml_media()
    media_type = str(report_criteria.get("type", "")).strip().upper()
    if media_type:
        media = [item for item in media if str(item.get("type", "")).upper() == media_type]
    state = str(report_criteria.get("state", "")).strip().upper()
    if state:
        media = [item for item in media if str(item.get("state", "")).upper() == state]
    return IBladeReport(
        generated_at=_timestamp(),
        items=media,
        summary={
            "total": len(media),
            "data": sum(1 for item in media if str(item.get("type")) == "LTO-9"),
            "cleaning": sum(1 for item in media if "CLN" in str(item.get("barcode", ""))),
            "reportCriteria": report_criteria,
        },
    )


def _media_count_report(criteria: dict[str, Any] | None = None) -> IBladeReport:
    report_criteria = criteria or {}
    media = _media_report(report_criteria).items
    by_state: dict[str, int] = {}
    for item in media:
        state = str(item.get("state", "unknown"))
        by_state[state] = by_state.get(state, 0) + 1
    return IBladeReport(
        generated_at=_timestamp(),
        items=[],
        summary={"total": len(media), "byState": by_state, "reportCriteria": report_criteria},
    )


def _volume_group_report(criteria: dict[str, Any] | None = None) -> IBladeReport:
    report_criteria = criteria or {}
    groups = [item.model_dump() for item in _serialize_volume_groups()]
    state = str(report_criteria.get("state", "")).strip().upper()
    if state:
        groups = [item for item in groups if str(item.get("state", "")).upper() == state]
    policy = str(report_criteria.get("policy", "")).strip().lower()
    if policy:
        groups = [item for item in groups if str(item.get("policy", "")).lower() == policy]
    return IBladeReport(
        generated_at=_timestamp(),
        items=groups,
        summary={
            "total": len(groups),
            "mediaCount": sum(int(item.get("mediaCount", 0)) for item in groups),
            "reportCriteria": report_criteria,
        },
    )


def _io_status() -> IBladeIoStatus:
    jobs = aml_state.list_aml_jobs()
    drives = aml_state.list_aml_drives()
    queue_jobs = [
        job for job in jobs if str(job.get("status", "")).lower() in {"queued", "active", "running"}
    ]
    active_transfer_jobs = [
        job
        for job in jobs
        if str(job.get("status", "")).lower() in {"active", "running"}
        and any(
            keyword in str(job.get("type", "")).lower()
            for keyword in (
                "archive",
                "restore",
                "assignment",
                "merge",
                "export",
                "repair",
                "replicate",
            )
        )
    ]
    active_drives = [str(drive.get("serialNumber")) for drive in drives if drive.get("loadedMedia")]
    active_transfers = max(len(active_drives), len(active_transfer_jobs))
    return IBladeIoStatus(
        activeTransfers=active_transfers,
        queueDepth=len(queue_jobs),
        throughputMBps=max(active_transfers * 400, 0),
        activeDrives=active_drives,
    )


@router.get("/states", response_model=list[CodeDescription])
async def get_states() -> list[CodeDescription]:
    return [CodeDescription.model_validate(item) for item in _STATE_CODES]


@router.get("/states/{code}", response_model=CodeDescription)
async def get_state_by_code(code: str) -> CodeDescription:
    return _code_description_or_404(_STATE_CODES, code)


@router.get("/volstates", response_model=list[CodeDescription])
async def get_volume_states() -> list[CodeDescription]:
    return [CodeDescription.model_validate(item) for item in _VOLUME_STATES]


@router.get("/volstates/{code}", response_model=CodeDescription)
async def get_volume_state_by_code(code: str) -> CodeDescription:
    return _code_description_or_404(_VOLUME_STATES, code)


@router.get("/vgstates", response_model=list[CodeDescription])
async def get_volume_group_states() -> list[CodeDescription]:
    return [CodeDescription.model_validate(item) for item in _VG_STATES]


@router.get("/vgstates/{code}", response_model=CodeDescription)
async def get_volume_group_state_by_code(code: str) -> CodeDescription:
    return _code_description_or_404(_VG_STATES, code)


@router.get("/jobstates", response_model=list[CodeDescription])
async def get_job_states() -> list[CodeDescription]:
    return [CodeDescription.model_validate(item) for item in _JOB_STATES]


@router.get("/jobstates/{code}", response_model=CodeDescription)
async def get_job_state_by_code(code: str) -> CodeDescription:
    return _code_description_or_404(_JOB_STATES, code)


@router.get("/opstates", response_model=list[CodeDescription])
async def get_operation_states() -> list[CodeDescription]:
    return [CodeDescription.model_validate(item) for item in _JOB_STATES]


@router.get("/opstates/{code}", response_model=CodeDescription)
async def get_operation_state_by_code(code: str) -> CodeDescription:
    return _code_description_or_404(_JOB_STATES, code)


@router.get("/reasons", response_model=list[CodeDescription])
async def get_reasons() -> list[CodeDescription]:
    return [CodeDescription.model_validate(item) for item in _REASON_CODES]


@router.get("/reasons/{code}", response_model=CodeDescription)
async def get_reason_by_code(code: str) -> CodeDescription:
    return _code_description_or_404(_REASON_CODES, code)


@router.get("/vgreasons", response_model=list[CodeDescription])
async def get_volume_group_reasons() -> list[CodeDescription]:
    return [CodeDescription.model_validate(item) for item in _VG_REASON_CODES]


@router.get("/vgreasons/{code}", response_model=CodeDescription)
async def get_volume_group_reason_by_code(code: str) -> CodeDescription:
    return _code_description_or_404(_VG_REASON_CODES, code)


@router.get("/messages", response_model=list[IBladeMessage])
async def list_messages(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> list[IBladeMessage]:
    _ensure_state(context)
    return _sorted_open_messages()


@router.put("/messages", response_model=list[IBladeMessage])
async def put_messages(
    payload: dict[str, Any] = Body(default_factory=dict),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> list[IBladeMessage]:
    _ensure_state(context)
    normalized_payload = _normalized_message_close_payload(payload)
    request = _parse_model_payload(
        IBladeMessagesCloseRequest,
        normalized_payload,
        model_name="messages payload",
    )
    message_ids = _extract_message_ids(normalized_payload)
    if not message_ids and request.close_all:
        return []
    normalized_ids: list[str] = []
    seen: set[str] = set()
    for message_id in message_ids:
        normalized = _validate_identifier(message_id, field_name="message id")
        if normalized in seen:
            raise HTTPException(status_code=400, detail=f"Duplicate message id {normalized}")
        _message_or_404(normalized)
        seen.add(normalized)
        normalized_ids.append(normalized)
    return [_close_message(message_id, closed_by=request.closed_by) for message_id in normalized_ids]


@router.get("/messages/{message_id}", response_model=IBladeMessage)
async def get_message(
    message_id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeMessage:
    _ensure_state(context)
    return IBladeMessage.model_validate(
        _message_or_404(_validate_identifier(message_id, field_name="message id"))
    )


@router.put("/messages/{message_id}", response_model=IBladeMessage)
async def put_message(
    message_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeMessage:
    _ensure_state(context)
    normalized_payload = _normalized_message_close_payload(payload)
    request = _parse_model_payload(
        IBladeMessageCloseRequest,
        normalized_payload,
        model_name="message payload",
    )
    return _close_message(message_id, closed_by=request.closed_by)


@router.delete("/messages/{message_id}", response_model=IBladeMessage)
async def delete_message(
    message_id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeMessage:
    _ensure_state(context)
    return _close_message(message_id, closed_by="legacy-delete")


@router.get("/nas-drives", response_model=list[IBladeNasDrive])
async def list_nas_drives(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> list[IBladeNasDrive]:
    _ensure_state(context)
    return _serialize_nas_drives()


@router.get("/lto-media", response_model=list[dict[str, Any]])
async def list_lto_media(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> list[dict[str, Any]]:
    _ensure_state(context)
    return aml_state.list_aml_media()


@router.get("/lto_media/{barcode}", response_model=dict[str, Any])
async def get_lto_medium(
    barcode: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    _ensure_state(context)
    medium = aml_state.get_aml_media(_validate_identifier(barcode, field_name="barcode"))
    if medium is None:
        raise HTTPException(status_code=404, detail="Media not found")
    return medium


@router.put("/lto-media", response_model=list[dict[str, Any]])
async def update_lto_media(
    payload: list[dict[str, Any]] | dict[str, Any] = Body(default_factory=list),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> list[dict[str, Any]]:
    _ensure_state(context)
    items = payload if isinstance(payload, list) else list(payload.get("lto_media", []))
    validated = _validated_media_batch_updates(items)
    updated: list[dict[str, Any]] = []
    for barcode, updates in validated:
        candidate = aml_state.update_aml_media(barcode, updates)
        if candidate is None:
            raise HTTPException(status_code=404, detail=f"Media {barcode} not found")
        updated.append(candidate)
    return updated


@router.put("/lto-media/{barcode}", response_model=dict[str, Any])
async def update_lto_medium(
    barcode: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    _ensure_state(context)
    normalized_barcode = _validate_identifier(barcode, field_name="barcode").upper()
    existing = aml_state.get_aml_media(normalized_barcode)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Media {barcode} not found")
    updates = _validate_media_update_item(normalized_barcode, existing, payload)
    updated = aml_state.update_aml_media(normalized_barcode, updates)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Media {barcode} not found")
    return updated


@router.get("/hosts", response_model=list[IBladeHost])
async def list_hosts(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> list[IBladeHost]:
    _ensure_state(context)
    return _serialize_hosts()


@router.put("/hosts", response_model=list[IBladeHost])
async def put_hosts(
    payload: dict[str, Any] = Body(default_factory=dict),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> list[IBladeHost]:
    _ensure_state(context)
    hosts_value = payload.get("hosts")
    if hosts_value is not None and not isinstance(hosts_value, list):
        raise HTTPException(status_code=400, detail="hosts must be a list")
    items: list[object] = (
        hosts_value if isinstance(hosts_value, list) else [payload.get("host") or payload]
    )
    seen_ips: set[str] = set()
    existing_by_ip = {
        str(host.get("ip", "")).strip(): str(host.get("id", ""))
        for host in aml_state.list_iblade_hosts()
    }
    host_payloads: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail="hosts entries must be objects")
        host = IBladeHost.model_validate(item)
        normalized_ip = _validate_identifier(host.ip, field_name="ip")
        if normalized_ip in seen_ips:
            raise HTTPException(status_code=400, detail=f"Duplicate host ip {normalized_ip}")
        seen_ips.add(normalized_ip)
        host_payload = host.model_dump()
        # reboot_required is a response-only signal; never persist a client-supplied
        # (or defaulted) value into stored host state.
        host_payload.pop("reboot_required", None)
        host_payload["ip"] = normalized_ip
        existing_id = existing_by_ip.get(normalized_ip)
        if existing_id:
            host_payload["id"] = existing_id
        host_payloads.append(host_payload)
    for host_payload in host_payloads:
        aml_state.upsert_iblade_host(host_payload)
    # PUT .../hosts overwrites the allowed-host list; per iBlade WS Rev A the
    # change requires a reboot before taking effect. Signal it on the response.
    return [host.model_copy(update={"reboot_required": True}) for host in _serialize_hosts()]


@router.get("/hosts/{ip}", response_model=IBladeHost)
async def get_host_by_ip(
    ip: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeHost:
    _ensure_state(context)
    return IBladeHost.model_validate(_host_by_ip_or_404(ip))


@router.post("/hosts/{ip}", response_model=IBladeHost)
async def post_host_by_ip(
    ip: str,
    payload: IBladeHostUpdate = Body(default_factory=IBladeHostUpdate),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeHost:
    _ensure_state(context)
    normalized_ip = _validate_identifier(ip, field_name="ip")
    host_data = payload.model_dump(exclude_none=True)
    # reboot_required is a response-only signal; never persist a client-injected value.
    host_data.pop("reboot_required", None)
    existing: dict[str, Any] | None
    try:
        existing = _host_by_ip_or_404(normalized_ip)
    except HTTPException as error:
        if error.status_code != 404:
            raise
        existing = None
    if existing is None:
        host_id = str(host_data.get("id") or _next_host_id())
        host_suffix = host_id.split("-", 1)[-1].lower()
        host_record = {
            "id": host_id,
            "hostname": str(host_data.get("hostname") or f"host-{host_suffix}"),
            "ip": normalized_ip,
            "wwn": str(host_data.get("wwn") or ""),
            "connection_type": str(host_data.get("connection_type") or "ethernet"),
            "state": str(host_data.get("state") or "connected"),
        }
    else:
        host_record = {
            **existing,
            **host_data,
            "id": str(existing.get("id")),
            "ip": normalized_ip,
        }
    updated = aml_state.upsert_iblade_host(host_record)
    # POST .../hosts/{ip} adds/updates a host; per iBlade WS Rev A the change
    # requires a reboot before taking effect. Signal it on the response.
    return IBladeHost.model_validate(updated).model_copy(update={"reboot_required": True})


@router.delete("/hosts/{ip}", response_model=IBladeHost)
async def delete_host_by_ip(
    ip: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeHost:
    _ensure_state(context)
    host = _host_by_ip_or_404(ip)
    host_id = str(host["id"])
    removed = aml_state.delete_iblade_host(host_id)
    if removed is None:
        raise HTTPException(status_code=404, detail="Host not found")
    return IBladeHost.model_validate(removed)


@router.get("/jobs", response_model=list[IBladeJob])
async def list_jobs(
    completed: int | None = Query(default=None, ge=0),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> list[IBladeJob]:
    _ensure_state(context)
    jobs = sorted(
        (_serialize_job(item) for item in aml_state.list_aml_jobs()),
        key=lambda item: (item.opened, item.id),
        reverse=True,
    )
    return _filter_jobs_for_completed_window(jobs, completed)


@router.put("/jobs", response_model=list[IBladeJob])
async def update_jobs(
    payload: IBladeJobsUpdateRequest,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> list[IBladeJob]:
    _ensure_state(context)
    updates = payload.jobs
    validated: list[tuple[str, dict[str, Any], str]] = []
    seen_job_ids: set[str] = set()
    for item in updates:
        job_id = _validate_identifier(item.id, field_name="job id")
        if job_id in seen_job_ids:
            raise HTTPException(status_code=400, detail=f"Duplicate job id {job_id}")
        seen_job_ids.add(job_id)
        job = aml_state.get_aml_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        target_status = _normalize_job_state(item.job_state)
        _validate_job_transition(job, target_status)
        validated.append((job_id, job, target_status))
    updated_jobs: list[IBladeJob] = []
    for job_id, job, target_status in validated:
        update_payload = _job_update_payload(job, target_status)
        updated = aml_state.update_aml_job(job_id, update_payload)
        if updated is None:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        updated_jobs.append(_serialize_job(updated))
    return updated_jobs


@router.put("/jobs/{job_id}", response_model=IBladeJob)
async def update_job(
    job_id: str,
    payload: IBladeJobStateUpdate,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeJob:
    _ensure_state(context)
    job = aml_state.get_aml_job(_validate_identifier(job_id, field_name="job id"))
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    target_status = _normalize_job_state(payload.job_state)
    _validate_job_transition(job, target_status)
    update_payload = _job_update_payload(job, target_status)
    updated = aml_state.update_aml_job(str(job["id"]), update_payload)
    if updated is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _serialize_job(updated)


@router.get("/network", response_model=IBladeNetworkConfig)
async def get_network(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeNetworkConfig:
    _ensure_state(context)
    return IBladeNetworkConfig.model_validate(aml_state.get_iblade_network_config())


@router.put("/network", response_model=IBladeNetworkConfig)
async def put_network(
    payload: dict[str, Any] = Body(default_factory=dict),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeNetworkConfig:
    _ensure_state(context)
    return _apply_network_update(payload)


@router.put("/network/configuration/{port}/{version}", response_model=IBladeNetworkConfig)
async def put_network_configuration(
    port: str,
    version: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeNetworkConfig:
    _ensure_state(context)
    normalized_port, normalized_version = _validate_network_path_fields(port, version)
    return _apply_network_update(payload, port=normalized_port, version=normalized_version)


@router.get("/product", response_model=IBladeProductInfo)
async def get_product() -> IBladeProductInfo:
    return _product_info()


@router.get("/product/{element}", response_model=IBladeProductElement)
async def get_product_element(element: str) -> IBladeProductElement:
    product = _product_info().model_dump()
    key = _validate_identifier(element, field_name="element")
    if key not in product:
        raise HTTPException(status_code=404, detail="Product element not found")
    return IBladeProductElement(element=key, value=str(product[key]))


@router.get("/reports/configuration", response_model=IBladeReport)
async def get_configuration_report(
    reportCriteria: str | None = Query(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeReport:
    _ensure_state(context)
    return _configuration_report(_parse_report_criteria(reportCriteria))


@router.post(
    "/reports/configuration/email",
    response_model=IBladeJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def email_configuration_report(
    payload: dict[str, Any] | None = Body(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeJobResponse:
    _ensure_state(context)
    metadata = payload or {}
    if "reportCriteria" in metadata:
        metadata["reportCriteria"] = _parse_report_criteria(metadata.get("reportCriteria"))
    return _queue_job(
        "iblade-report-configuration-email", "Configuration report email queued", metadata
    )


@router.get("/reports/media", response_model=IBladeReport)
async def get_media_report(
    reportCriteria: str | None = Query(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeReport:
    _ensure_state(context)
    return _media_report(_parse_report_criteria(reportCriteria))


@router.post(
    "/reports/media/email", response_model=IBladeJobResponse, status_code=status.HTTP_202_ACCEPTED
)
async def email_media_report(
    payload: dict[str, Any] | None = Body(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeJobResponse:
    _ensure_state(context)
    metadata = payload or {}
    if "reportCriteria" in metadata:
        metadata["reportCriteria"] = _parse_report_criteria(metadata.get("reportCriteria"))
    return _queue_job("iblade-report-media-email", "Media report email queued", metadata)


@router.get("/reports/media-count", response_model=IBladeReport)
async def get_media_count_report(
    reportCriteria: str | None = Query(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeReport:
    _ensure_state(context)
    return _media_count_report(_parse_report_criteria(reportCriteria))


@router.post(
    "/reports/media-count/email",
    response_model=IBladeJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def email_media_count_report(
    payload: dict[str, Any] | None = Body(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeJobResponse:
    _ensure_state(context)
    metadata = payload or {}
    if "reportCriteria" in metadata:
        metadata["reportCriteria"] = _parse_report_criteria(metadata.get("reportCriteria"))
    return _queue_job(
        "iblade-report-media-count-email", "Media-count report email queued", metadata
    )


@router.get("/reports/volume-groups", response_model=IBladeReport)
async def get_volume_groups_report(
    reportCriteria: str | None = Query(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeReport:
    _ensure_state(context)
    return _volume_group_report(_parse_report_criteria(reportCriteria))


@router.post(
    "/reports/volume-groups/email",
    response_model=IBladeJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def email_volume_groups_report(
    payload: dict[str, Any] | None = Body(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeJobResponse:
    _ensure_state(context)
    metadata = payload or {}
    if "reportCriteria" in metadata:
        metadata["reportCriteria"] = _parse_report_criteria(metadata.get("reportCriteria"))
    return _queue_job(
        "iblade-report-volume-groups-email", "Volume-groups report email queued", metadata
    )


@router.get("/status/io", response_model=IBladeIoStatus)
async def get_io_status(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeIoStatus:
    _ensure_state(context)
    return _io_status()


@router.get("/status/open-messages", response_model=list[IBladeMessage])
async def get_open_messages(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> list[IBladeMessage]:
    _ensure_state(context)
    return _sorted_open_messages()


@router.get("/status/system/open-messages", response_model=list[IBladeMessage])
async def get_system_open_messages(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> list[IBladeMessage]:
    _ensure_state(context)
    return _sorted_open_messages()


@router.get("/system/settings", response_model=dict[str, Any])
async def get_system_settings(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    _ensure_state(context)
    settings = aml_state.get_iblade_system_settings()
    if _is_strict_interface(context):
        return _strict_system_settings(settings)
    return settings


@router.put("/system/settings", response_model=dict[str, Any])
async def put_system_settings(
    payload: dict[str, Any] = Body(default_factory=dict),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    _ensure_state(context)
    settings = aml_state.get_iblade_system_settings()
    if _is_strict_interface(context):
        _validate_strict_system_settings_payload(payload, settings)
    normalized_updates: dict[str, Any] = {}
    for key, value in payload.items():
        normalized_updates[_normalize_setting_key(settings, str(key))] = value
    return aml_state.set_iblade_system_settings(normalized_updates)


@router.get("/system/settings/{settingname}", response_model=IBladeSetting)
async def get_system_setting(
    settingname: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeSetting:
    _ensure_state(context)
    settings = aml_state.get_iblade_system_settings()
    if _is_strict_interface(context):
        settings = _strict_system_settings(settings)
    key = _normalize_setting_key(settings, settingname)
    if key not in settings:
        raise HTTPException(status_code=404, detail="Setting not found")
    return IBladeSetting(name=key, value=settings[key])


@router.put("/system/settings/{settingname}", response_model=IBladeSetting)
async def put_system_setting(
    settingname: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeSetting:
    _ensure_state(context)
    settings = aml_state.get_iblade_system_settings()
    strict_settings = _strict_system_settings(settings)
    lookup_settings = strict_settings if _is_strict_interface(context) else settings
    key = _normalize_setting_key(lookup_settings, settingname)
    if _is_strict_interface(context) and key not in strict_settings:
        raise HTTPException(status_code=404, detail="Setting not found")
    value = payload.get("value") if isinstance(payload, dict) and "value" in payload else payload
    settings = aml_state.set_iblade_system_settings({key: value})
    return IBladeSetting(name=key, value=settings[key])


@router.post(
    "/system/clear-to-ship", response_model=IBladeJobResponse, status_code=status.HTTP_202_ACCEPTED
)
async def clear_to_ship(
    payload: dict[str, Any] | None = Body(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeJobResponse:
    _ensure_state(context)
    return _queue_job("iblade-clear-to-ship", "Clear-to-ship workflow queued", payload or {})


@router.get("/system/extended-snapshot", response_model=IBladeReport)
async def get_extended_snapshot(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeReport:
    _ensure_state(context)
    if _is_strict_interface(context):
        raise HTTPException(status_code=404, detail="Endpoint not available in strict iBlade mode")
    payload = _configuration_report().model_dump(mode="json")
    open_messages = [item.model_dump(mode="json") for item in _sorted_open_messages()]
    payload["items"].append({"section": "open-messages", "data": open_messages})
    payload["summary"]["openMessages"] = len(open_messages)
    return IBladeReport.model_validate(payload)


@router.post(
    "/system/factory-defaults",
    response_model=IBladeJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def factory_defaults(
    payload: dict[str, Any] | None = Body(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeJobResponse:
    _ensure_state(context)
    return _queue_job("iblade-factory-defaults", "Factory defaults workflow queued", payload or {})


@router.post(
    "/system/snapshot", response_model=IBladeJobResponse, status_code=status.HTTP_202_ACCEPTED
)
async def create_snapshot(
    payload: dict[str, Any] | None = Body(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeJobResponse:
    _ensure_state(context)
    return _queue_job("iblade-snapshot", "System snapshot queued", payload or {})


@router.post(
    "/system/save-configuration",
    response_model=IBladeJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def save_configuration(
    payload: dict[str, Any] | None = Body(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeJobResponse:
    _ensure_state(context)
    return _queue_job("iblade-save-config", "Configuration save queued", payload or {})


@router.post(
    "/system/restore-configuration",
    response_model=IBladeJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def restore_configuration(
    payload: dict[str, Any] | None = Body(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeJobResponse:
    _ensure_state(context)
    return _queue_job("iblade-restore-config", "Configuration restore queued", payload or {})


@router.post(
    "/system/fwupgrade", response_model=IBladeJobResponse, status_code=status.HTTP_202_ACCEPTED
)
async def start_firmware_upgrade(
    payload: dict[str, Any] | None = Body(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeJobResponse:
    _ensure_state(context)
    return _queue_job("iblade-fwupgrade", "Firmware upgrade queued", payload or {})


@router.post(
    "/system/reboot", response_model=IBladeJobResponse, status_code=status.HTTP_202_ACCEPTED
)
async def reboot_system(
    payload: dict[str, Any] | None = Body(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeJobResponse:
    _ensure_state(context)
    return _queue_job("iblade-reboot", "System reboot queued", payload or {})


@router.post(
    "/operations/assignment", response_model=IBladeJobResponse, status_code=status.HTTP_202_ACCEPTED
)
async def assignment_operation(
    payload: dict[str, Any] | None = Body(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeJobResponse:
    _ensure_state(context)
    data = payload or {}
    request = _parse_model_payload(
        IBladeAssignmentOperationRequest,
        data,
        model_name="assignment payload",
    )
    index = int(request.index)
    group = _volume_group_or_404(index)
    tapes = _normalize_volume_group_tapes([*request.tapes, *request.barcodes])
    _validate_volume_group_tapes_exist(tapes)
    if tapes:
        merged = list(dict.fromkeys([*list(group.get("tapes", [])), *tapes]))
        others = [
            item.model_dump(mode="json")
            for item in _serialize_volume_groups()
            if int(item.index) != index
        ]
        _validate_unique_tape_assignments(
            [
                *others,
                {
                    "index": index,
                    "name": group.get("name", f"Volume Group {index}"),
                    "state": group.get("state", "READY"),
                    "reason": group.get("reason", "NONE"),
                    "policy": group.get("policy", "balanced"),
                    "tapes": merged,
                    "mediaCount": len(merged),
                },
            ]
        )
        aml_state.update_iblade_volume_group(index, {"tapes": merged})
    return _queue_job(
        "iblade-assignment",
        f"Tape assignment queued for volume group {index}",
        {**request.model_dump(mode="json"), "tapes": tapes},
    )


@router.post(
    "/operations/volume-groups/assign",
    response_model=IBladeJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def assignment_operation_compat(
    payload: dict[str, Any] | None = Body(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeJobResponse:
    return await assignment_operation(payload=payload, _=_, context=context)


@router.post(
    "/operations/merge", response_model=IBladeJobResponse, status_code=status.HTTP_202_ACCEPTED
)
async def merge_operation(
    payload: dict[str, Any] | None = Body(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeJobResponse:
    _ensure_state(context)
    request = _parse_model_payload(IBladeMergeOperationRequest, payload or {}, model_name="merge payload")
    source_index = int(request.source)
    destination_index = int(request.destination)
    if source_index == destination_index:
        raise HTTPException(status_code=400, detail="source and destination must differ")
    source = _volume_group_or_404(source_index)
    destination = _volume_group_or_404(destination_index)
    source_tapes = list(source.get("tapes", []))
    if not source_tapes:
        raise HTTPException(status_code=409, detail=f"Volume group {source_index} has no media")
    merged = list(dict.fromkeys([*destination.get("tapes", []), *source_tapes]))
    groups = [item.model_dump() for item in _serialize_volume_groups() if item.index != source_index]
    for item in groups:
        if int(item["index"]) == destination_index:
            item["tapes"] = merged
            item["mediaCount"] = len(merged)
    _validate_unique_tape_assignments(groups)
    aml_state.replace_iblade_volume_groups(groups)
    return _queue_job("iblade-merge", "Volume group merge queued", request.model_dump(mode="json"))


@router.post(
    "/operations/volume-groups/merge",
    response_model=IBladeJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def merge_operation_compat(
    payload: dict[str, Any] | None = Body(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeJobResponse:
    return await merge_operation(payload=payload, _=_, context=context)


@router.post(
    "/operations/prepare-export",
    response_model=IBladeJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def prepare_export_operation(
    payload: dict[str, Any] | None = Body(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeJobResponse:
    _ensure_state(context)
    request = _parse_model_payload(
        IBladePrepareExportOperationRequest,
        payload or {},
        model_name="prepare-export payload",
    )
    index = int(request.index)
    group = _volume_group_or_404(index)
    tapes = _formatted_volume_group_tapes(group)
    for barcode in tapes:
        updated = aml_state.update_aml_media(barcode, {"state": "exported"})
        if updated is None:
            raise HTTPException(status_code=404, detail=f"Media {barcode} not found")
    return _queue_job(
        "iblade-prepare-export",
        f"Prepare export job queued for volume group {index}",
        {**request.model_dump(mode="json"), "tapes": tapes},
    )


@router.post(
    "/operations/volume-groups/prepare-export",
    response_model=IBladeJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def prepare_export_operation_compat(
    payload: dict[str, Any] | None = Body(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeJobResponse:
    return await prepare_export_operation(payload=payload, _=_, context=context)


@router.post(
    "/operations/repair",
    response_model=IBladeJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def repair_operation(
    payload: dict[str, Any] | None = Body(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeJobResponse:
    return await repair_volume_group_operation(payload=payload, _=_, context=context)


@router.post(
    "/operations/volume-groups/repair",
    response_model=IBladeJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def repair_volume_group_operation(
    payload: dict[str, Any] | None = Body(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeJobResponse:
    _ensure_state(context)
    request = _parse_model_payload(IBladeRepairOperationRequest, payload or {}, model_name="repair payload")
    index = int(request.index)
    group = _volume_group_or_404(index)
    if str(group.get("state", "")).upper() == "READY" and str(group.get("reason", "")).upper() == "NONE":
        raise HTTPException(status_code=409, detail=f"Volume group {index} is already healthy")
    aml_state.update_iblade_volume_group(index, {"state": "READY", "reason": "NONE"})
    return _queue_job(
        "iblade-vg-repair",
        f"Repair queued for volume group {index}",
        request.model_dump(mode="json"),
    )


@router.post(
    "/operations/replicate", response_model=IBladeJobResponse, status_code=status.HTTP_202_ACCEPTED
)
async def replicate_operation(
    payload: dict[str, Any] | None = Body(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeJobResponse:
    _ensure_state(context)
    request = _parse_model_payload(
        IBladeReplicateOperationRequest,
        payload or {},
        model_name="replicate payload",
    )
    source_index = int(request.source)
    destination_index = int(request.destination)
    if source_index == destination_index:
        raise HTTPException(status_code=400, detail="source and destination must differ")
    source = _volume_group_or_404(source_index)
    _volume_group_or_404(destination_index)
    if not list(source.get("tapes", [])):
        raise HTTPException(status_code=409, detail=f"Volume group {source_index} has no media")
    return _queue_job("iblade-replicate", "Replication queued", request.model_dump(mode="json"))


@router.post(
    "/operations/volume-groups/replicate",
    response_model=IBladeJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def replicate_operation_compat(
    payload: dict[str, Any] | None = Body(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeJobResponse:
    return await replicate_operation(payload=payload, _=_, context=context)


@router.post(
    "/operations/safe-repair",
    response_model=IBladeJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def safe_repair_operation(
    payload: dict[str, Any] | None = Body(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeJobResponse:
    _ensure_state(context)
    request = _parse_model_payload(
        IBladeSafeRepairOperationRequest,
        payload or {},
        model_name="safe-repair payload",
    )
    index = int(request.index)
    if not _is_protected_volume_group(index):
        raise HTTPException(
            status_code=409,
            detail=f"Volume group {index} is not eligible for safe-repair",
        )
    group = _volume_group_or_404(index)
    if str(group.get("state", "")).upper() == "READY" and str(group.get("reason", "")).upper() == "NONE":
        raise HTTPException(status_code=409, detail=f"Volume group {index} is already healthy")
    aml_state.update_iblade_volume_group(index, {"state": "READY", "reason": "NONE"})
    return _queue_job(
        "iblade-safe-repair",
        f"Safe repair queued for volume group {index}",
        request.model_dump(mode="json"),
    )


@router.post(
    "/operations/volume-groups/safe-repair",
    response_model=IBladeJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def safe_repair_operation_compat(
    payload: dict[str, Any] | None = Body(default=None),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeJobResponse:
    return await safe_repair_operation(payload=payload, _=_, context=context)


@router.get("/volume-groups", response_model=list[IBladeVolumeGroup])
async def list_volume_groups(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> list[IBladeVolumeGroup]:
    _ensure_state(context)
    return _serialize_volume_groups()


@router.post(
    "/volume_groups", response_model=IBladeVolumeGroup, status_code=status.HTTP_201_CREATED
)
async def create_volume_group(
    payload: dict[str, Any] = Body(default_factory=dict),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeVolumeGroup:
    _ensure_state(context)
    if "index" in payload:
        raise HTTPException(status_code=400, detail="index is assigned automatically")
    groups = [item.model_dump(mode="json") for item in _serialize_volume_groups()]
    next_index = max((int(item["index"]) for item in groups), default=0) + 1
    name = _normalize_volume_group_name(payload.get("name"), fallback=f"Volume Group {next_index}")
    existing_names = {str(item.get("name", "")).strip().lower() for item in groups}
    if name.lower() in existing_names:
        raise HTTPException(status_code=400, detail=f"Volume-group name {name} already exists")
    tapes = _normalize_volume_group_tapes(payload.get("tapes"))
    _validate_volume_group_tapes_exist(tapes)
    candidate = {
        "index": next_index,
        "name": name,
        "state": str(payload.get("state", "READY")).strip().upper() or "READY",
        "reason": str(payload.get("reason", "NONE")).strip().upper() or "NONE",
        "policy": _normalize_volume_group_policy(payload.get("policy"), fallback="balanced"),
        "tapes": tapes,
        "mediaCount": len(tapes),
    }
    _validate_unique_tape_assignments([*groups, candidate])
    groups.append(candidate)
    aml_state.replace_iblade_volume_groups(groups)
    return IBladeVolumeGroup.model_validate(_volume_group_or_404(next_index))


@router.put("/volume-groups", response_model=list[IBladeVolumeGroup])
async def put_volume_groups(
    payload: list[dict[str, Any]] | dict[str, Any] = Body(default_factory=list),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> list[IBladeVolumeGroup]:
    _ensure_state(context)
    items = (
        payload
        if isinstance(payload, list)
        else list(payload.get("volume_groups", payload.get("volumeGroups", [])))
    )
    existing_groups = [item.model_dump(mode="json") for item in _serialize_volume_groups()]
    existing_by_index = {int(item["index"]): item for item in existing_groups}
    normalized: list[dict[str, Any]] = []
    seen_indexes: set[int] = set()
    seen_names: set[str] = set()
    for offset, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail="volume_groups entries must be objects")
        index = _parse_int_field(item, field_name="index", default=offset)
        if index <= 0:
            raise HTTPException(status_code=400, detail="index must be a positive integer")
        if index in seen_indexes:
            raise HTTPException(status_code=400, detail=f"Duplicate volume-group index {index}")
        seen_indexes.add(index)
        existing = existing_by_index.get(index, {})
        fallback_name = str(existing.get("name", f"Volume Group {index}"))
        name = _normalize_volume_group_name(item.get("name"), fallback=fallback_name)
        lowered_name = name.lower()
        if lowered_name in seen_names:
            raise HTTPException(status_code=400, detail=f"Duplicate volume-group name {name}")
        seen_names.add(lowered_name)
        policy = _normalize_volume_group_policy(
            item.get("policy"), fallback=str(existing.get("policy", "balanced"))
        )
        if _is_protected_volume_group(index) and existing:
            if name != str(existing.get("name", name)):
                raise HTTPException(
                    status_code=400, detail=f"Volume group {index} name is protected"
                )
            if policy != str(existing.get("policy", policy)):
                raise HTTPException(
                    status_code=400, detail=f"Volume group {index} policy is protected"
                )
        tapes = _normalize_volume_group_tapes(item.get("tapes"))
        _validate_volume_group_tapes_exist(tapes)
        normalized.append(
            {
                "index": index,
                "name": name,
                "state": str(item.get("state", existing.get("state", "READY"))).strip().upper()
                or "READY",
                "reason": str(item.get("reason", existing.get("reason", "NONE"))).strip().upper()
                or "NONE",
                "policy": policy,
                "tapes": tapes,
                "mediaCount": len(tapes),
            }
        )
    if not normalized:
        raise HTTPException(status_code=400, detail="At least one volume group is required")
    for index in _PROTECTED_VOLUME_GROUP_INDEXES:
        if index in existing_by_index and index not in seen_indexes:
            raise HTTPException(status_code=400, detail=f"Volume group {index} cannot be removed")
    _validate_unique_tape_assignments(normalized)
    aml_state.replace_iblade_volume_groups(normalized)
    return _serialize_volume_groups()


@router.get("/volume-groups/{index}", response_model=IBladeVolumeGroup)
async def get_volume_group(
    index: int,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeVolumeGroup:
    _ensure_state(context)
    return IBladeVolumeGroup.model_validate(_volume_group_or_404(index))


@router.put("/volume-groups/{index}", response_model=IBladeVolumeGroup)
async def put_volume_group(
    index: int,
    payload: dict[str, Any] = Body(default_factory=dict),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeVolumeGroup:
    _ensure_state(context)
    if "index" in payload:
        target_index = _parse_int_field(payload, field_name="index", default=index)
        if target_index != index:
            raise HTTPException(status_code=400, detail="Volume-group index cannot be changed")
    current = _volume_group_or_404(index)
    name = _normalize_volume_group_name(
        payload.get("name", current.get("name")),
        fallback=str(current.get("name", f"Volume Group {index}")),
    )
    policy = _normalize_volume_group_policy(
        payload.get("policy", current.get("policy")), fallback="balanced"
    )
    if _is_protected_volume_group(index):
        if name != str(current.get("name", name)):
            raise HTTPException(status_code=400, detail=f"Volume group {index} name is protected")
        if policy != str(current.get("policy", policy)):
            raise HTTPException(status_code=400, detail=f"Volume group {index} policy is protected")
    tapes = _normalize_volume_group_tapes(payload.get("tapes", current.get("tapes", [])))
    _validate_volume_group_tapes_exist(tapes)
    others = [
        item.model_dump(mode="json")
        for item in _serialize_volume_groups()
        if int(item.index) != index
    ]
    candidate = {
        "index": index,
        "name": name,
        "state": str(payload.get("state", current.get("state", "READY"))).strip().upper()
        or "READY",
        "reason": str(payload.get("reason", current.get("reason", "NONE"))).strip().upper()
        or "NONE",
        "policy": policy,
        "tapes": tapes,
        "mediaCount": len(tapes),
    }
    if any(name.lower() == str(item.get("name", "")).strip().lower() for item in others):
        raise HTTPException(status_code=400, detail=f"Volume-group name {name} already exists")
    _validate_unique_tape_assignments([*others, candidate])
    aml_state.replace_iblade_volume_groups([*others, candidate])
    return IBladeVolumeGroup.model_validate(_volume_group_or_404(index))


@router.delete("/volume-groups/{index}", response_model=IBladeVolumeGroup)
async def delete_volume_group(
    index: int,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> IBladeVolumeGroup:
    _ensure_state(context)
    if _is_protected_volume_group(index):
        raise HTTPException(status_code=400, detail=f"Volume group {index} is protected")
    removed = _volume_group_or_404(index)
    current_groups = _serialize_volume_groups()
    if len(current_groups) <= 1:
        raise HTTPException(status_code=400, detail="At least one volume group is required")
    groups = [item.model_dump(mode="json") for item in current_groups if item.index != index]
    aml_state.replace_iblade_volume_groups(groups)
    return IBladeVolumeGroup.model_validate(removed)
