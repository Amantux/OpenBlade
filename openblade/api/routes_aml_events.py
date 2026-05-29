"""AML events, RAS, logs, alerts, and health routes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from openblade.api import aml_state
from openblade.api.library_context import get_active_library, get_library_profile
from openblade.api.routes_aml_auth import WSResultCode, _ensure_state, _require_admin, require_auth
from openblade.bootstrap import AppContext, get_context
from openblade.catalog.models import AmlUser

router = APIRouter()

_ALLOWED_SEVERITIES = {"critical", "warning", "info"}
_ALLOWED_LOG_LEVELS = {"TRACE", "DEBUG", "INFO", "WARN", "WARNING", "ERROR", "CRITICAL"}
_EVENT_TYPES: list[dict[str, str]] = [
    {"name": "library", "description": "General library lifecycle events", "severity": "info"},
    {"name": "drive", "description": "Drive maintenance and health events", "severity": "warning"},
    {"name": "robotics", "description": "Robotics motion and service events", "severity": "warning"},
    {"name": "media", "description": "Media inventory and movement events", "severity": "info"},
    {"name": "system", "description": "Controller and platform events", "severity": "critical"},
]
_HEALTH_ORDER = {"good": 0, "warning": 1, "critical": 2}


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    timestamp: str
    severity: str
    component: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class EventListResource(BaseModel):
    event: list[Event]


class EventListResponse(BaseModel):
    eventList: EventListResource


class EventResponse(BaseModel):
    event: Event


class EventSummary(BaseModel):
    critical: int
    warning: int
    info: int
    total: int
    lastEvent: str | None = None


class EventSummaryResponse(BaseModel):
    eventSummary: EventSummary


class EventType(BaseModel):
    name: str
    description: str
    severity: str


class TypeListResource(BaseModel):
    type: list[EventType]


class TypeListResponse(BaseModel):
    typeList: TypeListResource


class SubscriptionPayload(BaseModel):
    severity: str | None = None
    components: list[str] = Field(default_factory=list)
    callback: str


class SubscriptionRequest(BaseModel):
    subscription: SubscriptionPayload


class Ticket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    timestamp: str
    severity: str
    status: str
    component: str
    description: str
    resolution: str | None = None
    assignee: str | None = None


class TicketListResource(BaseModel):
    ticket: list[Ticket]


class TicketListResponse(BaseModel):
    ticketList: TicketListResource


class TicketResponse(BaseModel):
    ticket: Ticket


class TicketCreatePayload(BaseModel):
    severity: str
    component: str
    description: str


class TicketCreateRequest(BaseModel):
    ticket: TicketCreatePayload


class TicketUpdatePayload(BaseModel):
    status: str | None = None
    resolution: str | None = None
    assignee: str | None = None

    @field_validator("status")
    @classmethod
    def _validate_status(cls, v: str | None) -> str | None:
        _VALID_STATUSES = {"open", "acknowledged", "resolved", "closed"}
        if v is not None and v.lower() not in _VALID_STATUSES:
            raise ValueError(f"status must be one of {sorted(_VALID_STATUSES)}")
        return v.lower() if v is not None else v


class TicketUpdateRequest(BaseModel):
    ticket: TicketUpdatePayload


class ResolutionPayload(BaseModel):
    description: str


class ResolutionRequest(BaseModel):
    resolution: ResolutionPayload


class TicketSummary(BaseModel):
    open: int
    acknowledged: int
    resolved: int
    critical: int
    warning: int


class TicketSummaryResponse(BaseModel):
    ticketSummary: TicketSummary


class LogFile(BaseModel):
    name: str
    size: int
    lastModified: str
    type: str


class LogListResource(BaseModel):
    log: list[LogFile]


class LogListResponse(BaseModel):
    logList: LogListResource


class LogContent(BaseModel):
    name: str
    lines: list[str] = Field(default_factory=list)
    totalLines: int
    offset: int


class LogContentResponse(BaseModel):
    logContent: LogContent


class LogLevel(BaseModel):
    level: str
    components: dict[str, str] = Field(default_factory=dict)


class LogLevelRequest(BaseModel):
    logLevel: LogLevel


class LogLevelUpdatePayload(BaseModel):
    level: str


class LogLevelUpdateRequest(BaseModel):
    logLevel: LogLevelUpdatePayload


class LogLevelResponse(BaseModel):
    logLevel: LogLevel


class Alert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    timestamp: str
    severity: str
    component: str
    message: str
    acknowledged: bool = False


class AlertListResource(BaseModel):
    alert: list[Alert]


class AlertListResponse(BaseModel):
    alertList: AlertListResource


class AlertResponse(BaseModel):
    alert: Alert


class AlertSummary(BaseModel):
    critical: int
    warning: int
    info: int
    total: int


class AlertSummaryResponse(BaseModel):
    alertSummary: AlertSummary


class TapeAlert(BaseModel):
    flag: str
    severity: str
    message: str
    drive: str
    timestamp: str


class TapeAlertListResource(BaseModel):
    tapeAlert: list[TapeAlert]


class TapeAlertListResponse(BaseModel):
    tapeAlertList: TapeAlertListResource


class Notification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    timestamp: str
    type: str
    message: str
    read: bool = False


class NotificationListResource(BaseModel):
    notification: list[Notification]


class NotificationListResponse(BaseModel):
    notificationList: NotificationListResource


class UnreadCount(BaseModel):
    count: int


class UnreadCountResponse(BaseModel):
    unreadCount: UnreadCount


class HealthComponents(BaseModel):
    library: str
    robotics: str
    drives: str
    media: str
    network: str
    system: str


class HealthSummary(BaseModel):
    overall: str
    components: HealthComponents
    activeAlerts: int
    openTickets: int


class HealthSummaryResponse(BaseModel):
    healthSummary: HealthSummary


class DriveSummary(BaseModel):
    total: int
    online: int
    attention: int


class SlotSummary(BaseModel):
    total: int
    used: int
    utilizationPercent: int


class JobSummary(BaseModel):
    total: int
    active: int
    pending: int
    completed: int
    failed: int


class EventCounts(BaseModel):
    total: int
    critical: int
    warning: int
    info: int


class DashboardSummary(BaseModel):
    overall: str
    drives: DriveSummary
    slots: SlotSummary
    jobs: JobSummary
    events: EventCounts
    activeAlerts: int
    openTickets: int


class DashboardSummaryResponse(BaseModel):
    summary: DashboardSummary


def _ws_result(summary: str = "Operation completed") -> WSResultCode:
    return WSResultCode(summary=summary)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str | None, *, field_name: str) -> datetime | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}") from exc


def _validate_text(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail=f"{field_name} is required")
    return normalized


def _validate_severity(value: str | None, *, field_name: str = "severity", required: bool = True) -> str | None:
    if value is None:
        if required:
            raise HTTPException(status_code=400, detail=f"{field_name} is required")
        return None
    normalized = value.strip().lower()
    if not normalized and not required:
        return None
    if normalized not in _ALLOWED_SEVERITIES:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}")
    return normalized


def _validate_log_level(value: str) -> str:
    normalized = value.strip().upper()
    if normalized not in _ALLOWED_LOG_LEVELS:
        raise HTTPException(status_code=400, detail="Invalid log level")
    return normalized


def _serialize_event(event: dict[str, Any]) -> Event:
    return Event.model_validate(event)


def _serialize_ticket(ticket: dict[str, Any]) -> Ticket:
    return Ticket.model_validate(ticket)


def _serialize_log(log: dict[str, Any]) -> LogFile:
    lines = log.get("lines") if isinstance(log.get("lines"), list) else []
    size = log.get("size")
    if not isinstance(size, int) or size < 0:
        size = sum(len(line) + 1 for line in lines)
    payload = dict(log)
    payload["size"] = size
    return LogFile.model_validate(payload)


def _serialize_alert(alert: dict[str, Any]) -> Alert:
    return Alert.model_validate(alert)


def _serialize_tapealert(item: dict[str, Any]) -> TapeAlert:
    return TapeAlert.model_validate(item)


def _serialize_notification(notification: dict[str, Any]) -> Notification:
    return Notification.model_validate(notification)


def _get_event_or_404(event_id: str) -> dict[str, Any]:
    event = aml_state.get_aml_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


def _get_ticket_or_404(ticket_id: str) -> dict[str, Any]:
    ticket = aml_state.get_aml_ras_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="RAS ticket not found")
    return ticket


def _get_log_or_404(name: str) -> dict[str, Any]:
    log = aml_state.get_aml_log(name)
    if log is None:
        raise HTTPException(status_code=404, detail="Log not found")
    return log


def _get_alert_or_404(alert_id: str) -> dict[str, Any]:
    alert = aml_state.get_aml_alert(alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    return alert


def _get_notification_or_404(notification_id: str) -> dict[str, Any]:
    notification = aml_state.get_aml_notification(notification_id)
    if notification is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    return notification


def _record_event(*, severity: str, component: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    event = {
        "id": str(uuid4()),
        "timestamp": _timestamp(),
        "severity": severity,
        "component": component,
        "message": message,
        "details": details or {},
    }
    return aml_state.append_aml_event(event)


def _create_notification(*, notification_type: str, message: str) -> dict[str, Any]:
    notification_id = str(uuid4())
    return aml_state.set_aml_notification(
        notification_id,
        {
            "id": notification_id,
            "timestamp": _timestamp(),
            "type": notification_type,
            "message": message,
            "read": False,
        },
    )


def _refresh_log(name: str, log: dict[str, Any]) -> dict[str, Any]:
    payload = dict(log)
    lines = payload.get("lines") if isinstance(payload.get("lines"), list) else []
    payload["lines"] = list(lines)
    payload["size"] = sum(len(line) + 1 for line in lines)
    payload.setdefault("lastModified", _timestamp())
    return aml_state.set_aml_log(name, payload)


def _event_summary(events: list[dict[str, Any]]) -> EventSummary:
    counts = {severity: sum(1 for event in events if str(event.get("severity", "")).lower() == severity) for severity in _ALLOWED_SEVERITIES}
    ordered = sorted(events, key=lambda item: str(item.get("timestamp", "")), reverse=True)
    return EventSummary(
        critical=counts["critical"],
        warning=counts["warning"],
        info=counts["info"],
        total=len(events),
        lastEvent=ordered[0].get("timestamp") if ordered else None,
    )


def _ticket_summary(tickets: list[dict[str, Any]]) -> TicketSummary:
    return TicketSummary(
        open=sum(1 for ticket in tickets if str(ticket.get("status", "open")).lower() == "open"),
        acknowledged=sum(1 for ticket in tickets if str(ticket.get("status", "")).lower() == "acknowledged"),
        resolved=sum(1 for ticket in tickets if str(ticket.get("status", "")).lower() == "resolved"),
        critical=sum(1 for ticket in tickets if str(ticket.get("severity", "")).lower() == "critical"),
        warning=sum(1 for ticket in tickets if str(ticket.get("severity", "")).lower() == "warning"),
    )


def _alert_summary(alerts: list[dict[str, Any]]) -> AlertSummary:
    return AlertSummary(
        critical=sum(1 for alert in alerts if str(alert.get("severity", "")).lower() == "critical"),
        warning=sum(1 for alert in alerts if str(alert.get("severity", "")).lower() == "warning"),
        info=sum(1 for alert in alerts if str(alert.get("severity", "")).lower() == "info"),
        total=len(alerts),
    )


def _component_status(*statuses: str) -> str:
    return max(statuses, key=lambda item: _HEALTH_ORDER.get(item, 0))


def _health_summary(context: AppContext) -> HealthSummary:
    active_library = get_active_library(context.catalog)
    profile = get_library_profile(active_library)
    library = "critical" if aml_state.get_library_mode() == "offline" else "good"
    robots = list(aml_state.get_aml_robots().values())
    drives = aml_state.list_aml_drives()[: profile["drive_count"]]
    eth_blades = list(aml_state.get_eth_blades().values())
    tickets = aml_state.list_aml_ras_tickets()
    alerts = aml_state.list_aml_alerts()
    tapealerts = aml_state.list_aml_tapealerts()

    robotics = "warning" if any(str(robot.get("status", "online")).lower() != "online" for robot in robots) else "good"
    drive_statuses: list[str] = []
    for drive in drives:
        if str(drive.get("status", "online")).lower() == "offline" or int(drive.get("errorCount", 0)) >= 5:
            drive_statuses.append("critical")
        elif bool(drive.get("cleaningRequired", False)) or int(drive.get("errorCount", 0)) > 0:
            drive_statuses.append("warning")
        else:
            drive_statuses.append("good")
    drives_health = _component_status(*drive_statuses) if drive_statuses else "good"

    media_flags = [str(item.get("severity", "info")).lower() for item in tapealerts]
    media = "critical" if "critical" in media_flags else "warning" if "warning" in media_flags else "good"
    network = "warning" if any(str(blade.get("status", "online")).lower() != "online" for blade in eth_blades) else "good"
    system_items = [
        str(ticket.get("severity", "info")).lower()
        for ticket in tickets
        if str(ticket.get("component", "")).lower() == "system" and str(ticket.get("status", "open")).lower() != "resolved"
    ] + [
        str(alert.get("severity", "info")).lower()
        for alert in alerts
        if str(alert.get("component", "")).lower() == "system"
    ]
    system = "critical" if "critical" in system_items else "warning" if "warning" in system_items else "good"

    overall = _component_status(library, robotics, drives_health, media, network, system)
    open_tickets = sum(1 for ticket in tickets if str(ticket.get("status", "open")).lower() != "resolved")
    return HealthSummary(
        overall=overall,
        components=HealthComponents(
            library=library,
            robotics=robotics,
            drives=drives_health,
            media=media,
            network=network,
            system=system,
        ),
        activeAlerts=min(len(alerts), profile["alerts_count"]) if active_library is not None else len(alerts),
        openTickets=open_tickets,
    )


def _dashboard_summary(context: AppContext) -> DashboardSummary:
    active_library = get_active_library(context.catalog)
    profile = get_library_profile(active_library)
    health = _health_summary(context)
    drives = aml_state.list_aml_drives()[: profile["drive_count"]]
    partitions = aml_state.list_aml_partitions()
    active_jobs = aml_state.list_aml_jobs()
    history_jobs = aml_state.list_aml_job_history()
    all_jobs = [*active_jobs, *history_jobs]
    events = aml_state.list_aml_events()

    drive_total = len(drives)
    drive_online = sum(1 for drive in drives if str(drive.get("status", "")).lower() == "online")
    drive_attention = sum(
        1
        for drive in drives
        if str(drive.get("status", "")).lower() not in {"online", "ready"}
        or str(drive.get("state", "")).lower() in {"faulted", "failed", "offline", "error"}
    )

    slot_total = profile["slot_count"] if active_library is not None else sum(
        int(partition.get("slotCount", 0)) + int(partition.get("ieSlotCount", 0)) for partition in partitions
    )
    slot_used = profile["occupied_slot_count"] if active_library is not None else len(aml_state.list_aml_media())
    slot_utilization_percent = round((slot_used / slot_total) * 100) if slot_total else 0

    job_statuses = [str(job.get("status", "unknown")).lower() for job in all_jobs]
    active_statuses = {"active", "running", "in_progress"}
    pending_statuses = {"pending", "queued", "paused"}
    completed_statuses = {"completed", "complete", "succeeded", "success"}
    failed_statuses = {"failed", "cancelled", "canceled", "error"}

    event_severities = [str(event.get("severity", "info")).lower() for event in events]

    return DashboardSummary(
        overall=health.overall,
        drives=DriveSummary(total=drive_total, online=drive_online, attention=drive_attention),
        slots=SlotSummary(total=slot_total, used=slot_used, utilizationPercent=slot_utilization_percent),
        jobs=JobSummary(
            total=profile["active_job_count"] if active_library is not None else len(all_jobs),
            active=profile["active_job_count"] if active_library is not None else sum(1 for status in job_statuses if status in active_statuses),
            pending=sum(1 for status in job_statuses if status in pending_statuses),
            completed=sum(1 for status in job_statuses if status in completed_statuses),
            failed=sum(1 for status in job_statuses if status in failed_statuses),
        ),
        events=EventCounts(
            total=len(events),
            critical=sum(1 for severity in event_severities if severity == "critical"),
            warning=sum(1 for severity in event_severities if severity == "warning"),
            info=sum(1 for severity in event_severities if severity == "info"),
        ),
        activeAlerts=health.activeAlerts,
        openTickets=health.openTickets,
    )


@router.get("/events", response_model=EventListResponse)
async def list_events(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    severity: str | None = None,
    component: str | None = None,
    startTime: str | None = None,
    endTime: str | None = None,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> EventListResponse:
    _ensure_state(context)
    severity_filter = _validate_severity(severity, required=False)
    component_filter = component.strip().lower() if component else None
    start_time = _parse_timestamp(startTime, field_name="startTime")
    end_time = _parse_timestamp(endTime, field_name="endTime")
    events = aml_state.list_aml_events()
    filtered: list[dict[str, Any]] = []
    for event in events:
        if severity_filter and str(event.get("severity", "")).lower() != severity_filter:
            continue
        if component_filter and str(event.get("component", "")).lower() != component_filter:
            continue
        event_time = _parse_timestamp(str(event.get("timestamp")), field_name="timestamp")
        if start_time and event_time and event_time < start_time:
            continue
        if end_time and event_time and event_time > end_time:
            continue
        filtered.append(event)
    items = [_serialize_event(item) for item in filtered[offset : offset + limit]]
    return EventListResponse(eventList=EventListResource(event=items))


@router.delete("/events", response_model=WSResultCode)
async def clear_events(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    aml_state.clear_aml_events()
    _create_notification(notification_type="events", message="All events cleared")
    return _ws_result("Cleared all events")


@router.get("/events/summary", response_model=EventSummaryResponse)
async def get_event_summary(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> EventSummaryResponse:
    _ensure_state(context)
    return EventSummaryResponse(eventSummary=_event_summary(aml_state.list_aml_events()))


@router.get("/events/types", response_model=TypeListResponse)
async def list_event_types(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> TypeListResponse:
    _ensure_state(context)
    return TypeListResponse(typeList=TypeListResource(type=[EventType.model_validate(item) for item in _EVENT_TYPES]))


@router.post("/events/subscribe", response_model=WSResultCode)
async def subscribe_events(
    payload: SubscriptionRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    severity = _validate_severity(payload.subscription.severity, required=False)
    callback = _validate_text(payload.subscription.callback, field_name="callback")
    components = [_validate_text(item, field_name="components") for item in payload.subscription.components]
    aml_state.add_aml_event_subscription({"severity": severity, "components": components, "callback": callback})
    _record_event(severity="info", component="system", message="Event subscription added", details={"callback": callback})
    return _ws_result("Subscribed to events")


@router.delete("/events/subscribe", response_model=WSResultCode)
async def unsubscribe_events(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    aml_state.clear_aml_event_subscriptions()
    _record_event(severity="info", component="system", message="Event subscriptions cleared")
    return _ws_result("Unsubscribed from events")


@router.get("/event/{id}", response_model=EventResponse)
async def get_event(
    resource_id: str = Path(..., alias="id", min_length=1),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> EventResponse:
    _ensure_state(context)
    return EventResponse(event=_serialize_event(_get_event_or_404(_validate_text(resource_id, field_name="id"))))


@router.get("/ras/tickets", response_model=TicketListResponse)
async def list_ras_tickets(
    status: str | None = None,
    severity: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> TicketListResponse:
    _ensure_state(context)
    status_filter = status.strip().lower() if status else None
    severity_filter = _validate_severity(severity, required=False)
    tickets = aml_state.list_aml_ras_tickets()
    filtered = []
    for ticket in sorted(tickets, key=lambda item: str(item.get("timestamp", "")), reverse=True):
        if status_filter and str(ticket.get("status", "")).lower() != status_filter:
            continue
        if severity_filter and str(ticket.get("severity", "")).lower() != severity_filter:
            continue
        filtered.append(ticket)
    return TicketListResponse(ticketList=TicketListResource(ticket=[_serialize_ticket(item) for item in filtered[:limit]]))


@router.post("/ras/ticket", response_model=TicketResponse, status_code=status.HTTP_201_CREATED)
async def create_ras_ticket(
    payload: TicketCreateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> TicketResponse:
    _ensure_state(context)
    _require_admin(current_user)
    ticket_id = str(uuid4())
    ticket = aml_state.set_aml_ras_ticket(
        ticket_id,
        {
            "id": ticket_id,
            "timestamp": _timestamp(),
            "severity": _validate_severity(payload.ticket.severity) or "info",
            "status": "open",
            "component": _validate_text(payload.ticket.component, field_name="component"),
            "description": _validate_text(payload.ticket.description, field_name="description"),
            "resolution": None,
            "assignee": None,
        },
    )
    _record_event(
        severity=str(ticket["severity"]),
        component=str(ticket["component"]),
        message=f"RAS ticket {ticket_id} created",
        details={"ticketId": ticket_id},
    )
    _create_notification(notification_type="ras", message=f"RAS ticket {ticket_id} created")
    return TicketResponse(ticket=_serialize_ticket(ticket))


@router.get("/ras/tickets/summary", response_model=TicketSummaryResponse)
async def get_ras_ticket_summary(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> TicketSummaryResponse:
    _ensure_state(context)
    return TicketSummaryResponse(ticketSummary=_ticket_summary(aml_state.list_aml_ras_tickets()))


@router.get("/ras/ticket/{id}", response_model=TicketResponse)
async def get_ras_ticket(
    resource_id: str = Path(..., alias="id", min_length=1),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> TicketResponse:
    _ensure_state(context)
    return TicketResponse(ticket=_serialize_ticket(_get_ticket_or_404(_validate_text(resource_id, field_name="id"))))


@router.put("/ras/ticket/{id}", response_model=TicketResponse)
async def update_ras_ticket(
    payload: TicketUpdateRequest,
    resource_id: str = Path(..., alias="id", min_length=1),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> TicketResponse:
    _ensure_state(context)
    _require_admin(current_user)
    ticket_id = _validate_text(resource_id, field_name="id")
    _get_ticket_or_404(ticket_id)
    updates = payload.ticket.model_dump(exclude_none=True)
    if "status" in updates:
        updates["status"] = _validate_text(str(updates["status"]), field_name="status").lower()
    if "resolution" in updates:
        updates["resolution"] = _validate_text(str(updates["resolution"]), field_name="resolution")
    if "assignee" in updates:
        updates["assignee"] = _validate_text(str(updates["assignee"]), field_name="assignee")
    updated = aml_state.update_aml_ras_ticket(ticket_id, updates)
    if updated is None:
        raise HTTPException(status_code=404, detail="RAS ticket not found")
    _create_notification(notification_type="ras", message=f"RAS ticket {ticket_id} updated")
    return TicketResponse(ticket=_serialize_ticket(updated))


@router.delete("/ras/ticket/{id}", response_model=WSResultCode)
async def delete_ras_ticket(
    resource_id: str = Path(..., alias="id", min_length=1),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    ticket_id = _validate_text(resource_id, field_name="id")
    _get_ticket_or_404(ticket_id)
    aml_state.pop_aml_ras_ticket(ticket_id)
    _record_event(severity="info", component="system", message=f"RAS ticket {ticket_id} deleted")
    return _ws_result(f"Deleted RAS ticket {ticket_id}")


@router.post("/ras/ticket/{id}/acknowledge", response_model=WSResultCode)
async def acknowledge_ras_ticket(
    resource_id: str = Path(..., alias="id", min_length=1),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    ticket_id = _validate_text(resource_id, field_name="id")
    ticket = _get_ticket_or_404(ticket_id)
    aml_state.update_aml_ras_ticket(ticket_id, {"status": "acknowledged"})
    _create_notification(notification_type="ras", message=f"RAS ticket {ticket_id} acknowledged")
    _record_event(severity=str(ticket.get("severity", "info")), component=str(ticket.get("component", "system")), message=f"RAS ticket {ticket_id} acknowledged")
    return _ws_result(f"Acknowledged RAS ticket {ticket_id}")


@router.post("/ras/ticket/{id}/resolve", response_model=WSResultCode)
async def resolve_ras_ticket(
    payload: ResolutionRequest,
    resource_id: str = Path(..., alias="id", min_length=1),
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    ticket_id = _validate_text(resource_id, field_name="id")
    ticket = _get_ticket_or_404(ticket_id)
    resolution = _validate_text(payload.resolution.description, field_name="description")
    aml_state.update_aml_ras_ticket(ticket_id, {"status": "resolved", "resolution": resolution})
    _create_notification(notification_type="ras", message=f"RAS ticket {ticket_id} resolved")
    _record_event(severity="info", component=str(ticket.get("component", "system")), message=f"RAS ticket {ticket_id} resolved", details={"resolution": resolution})
    return _ws_result(f"Resolved RAS ticket {ticket_id}")


@router.get("/logs", response_model=LogListResponse)
async def list_logs(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> LogListResponse:
    _ensure_state(context)
    logs = []
    for item in aml_state.list_aml_logs():
        refreshed = _refresh_log(str(item["name"]), item)
        logs.append(_serialize_log(refreshed))
    return LogListResponse(logList=LogListResource(log=logs))


@router.post("/logs/rotate", response_model=WSResultCode)
async def rotate_logs(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    now = _timestamp()
    for log in aml_state.list_aml_logs():
        name = str(log["name"])
        rotated_name = f"{name}.1"
        lines = list(log.get("lines") if isinstance(log.get("lines"), list) else [])
        aml_state.set_aml_log(
            rotated_name,
            {
                "name": rotated_name,
                "size": sum(len(line) + 1 for line in lines),
                "lastModified": now,
                "type": str(log.get("type", "system")),
                "lines": lines,
            },
        )
        aml_state.set_aml_log(
            name,
            {
                "name": name,
                "size": 0,
                "lastModified": now,
                "type": str(log.get("type", "system")),
                "lines": [],
            },
        )
    _create_notification(notification_type="logs", message="Logs rotated")
    return _ws_result("Rotated logs")


@router.get("/logs/level", response_model=LogLevelResponse)
async def get_log_level(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> LogLevelResponse:
    _ensure_state(context)
    return LogLevelResponse(logLevel=LogLevel.model_validate(aml_state.get_aml_log_level()))


@router.put("/logs/level", response_model=WSResultCode)
async def set_log_level(
    payload: LogLevelUpdateRequest,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    current = aml_state.get_aml_log_level()
    aml_state.set_aml_log_level({
        "level": _validate_log_level(payload.logLevel.level),
        "components": dict(current.get("components") if isinstance(current.get("components"), dict) else {}),
    })
    _create_notification(notification_type="logs", message="Log level updated")
    return _ws_result("Updated log level")


@router.get("/logs/{name}", response_model=LogContentResponse)
async def get_log_content(
    name: str,
    lines: int = Query(100, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> LogContentResponse:
    _ensure_state(context)
    log = _refresh_log(_validate_text(name, field_name="name"), _get_log_or_404(_validate_text(name, field_name="name")))
    content = log.get("lines") if isinstance(log.get("lines"), list) else []
    return LogContentResponse(
        logContent=LogContent(name=str(log["name"]), lines=[str(line) for line in content[offset : offset + lines]], totalLines=len(content), offset=offset)
    )


@router.delete("/logs/{name}", response_model=WSResultCode)
async def clear_log(
    name: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    log_name = _validate_text(name, field_name="name")
    log = _get_log_or_404(log_name)
    aml_state.set_aml_log(
        log_name,
        {
            "name": log_name,
            "size": 0,
            "lastModified": _timestamp(),
            "type": str(log.get("type", "system")),
            "lines": [],
        },
    )
    return _ws_result(f"Cleared log {log_name}")


@router.get("/alerts", response_model=AlertListResponse)
async def list_alerts(
    severity: str | None = None,
    component: str | None = None,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> AlertListResponse:
    _ensure_state(context)
    severity_filter = _validate_severity(severity, required=False)
    component_filter = component.strip().lower() if component else None
    alerts = []
    for alert in sorted(aml_state.list_aml_alerts(), key=lambda item: str(item.get("timestamp", "")), reverse=True):
        if severity_filter and str(alert.get("severity", "")).lower() != severity_filter:
            continue
        if component_filter and str(alert.get("component", "")).lower() != component_filter:
            continue
        alerts.append(_serialize_alert(alert))
    return AlertListResponse(alertList=AlertListResource(alert=alerts))


@router.delete("/alerts", response_model=WSResultCode)
async def clear_alerts(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    aml_state.clear_aml_alerts()
    _create_notification(notification_type="alerts", message="All alerts cleared")
    return _ws_result("Cleared all alerts")


@router.get("/alerts/summary", response_model=AlertSummaryResponse)
async def get_alert_summary(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> AlertSummaryResponse:
    _ensure_state(context)
    return AlertSummaryResponse(alertSummary=_alert_summary(aml_state.list_aml_alerts()))


@router.get("/alert/{id}", response_model=AlertResponse)
async def get_alert(
    resource_id: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> AlertResponse:
    _ensure_state(context)
    return AlertResponse(alert=_serialize_alert(_get_alert_or_404(_validate_text(resource_id, field_name="id"))))


@router.post("/alert/{id}/acknowledge", response_model=WSResultCode)
async def acknowledge_alert(
    resource_id: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    alert_id = _validate_text(resource_id, field_name="id")
    _get_alert_or_404(alert_id)
    aml_state.update_aml_alert(alert_id, {"acknowledged": True})
    _create_notification(notification_type="alerts", message=f"Alert {alert_id} acknowledged")
    return _ws_result(f"Acknowledged alert {alert_id}")


@router.delete("/alert/{id}", response_model=WSResultCode)
async def delete_alert(
    resource_id: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    alert_id = _validate_text(resource_id, field_name="id")
    _get_alert_or_404(alert_id)
    aml_state.pop_aml_alert(alert_id)
    return _ws_result(f"Dismissed alert {alert_id}")


@router.get("/tapealerts", response_model=TapeAlertListResponse)
async def list_tapealerts(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> TapeAlertListResponse:
    _ensure_state(context)
    return TapeAlertListResponse(
        tapeAlertList=TapeAlertListResource(tapeAlert=[_serialize_tapealert(item) for item in aml_state.list_aml_tapealerts()])
    )


@router.delete("/tapealerts", response_model=WSResultCode)
async def clear_tapealerts(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    aml_state.clear_aml_tapealerts()
    return _ws_result("Cleared all TapeAlert flags")


@router.get("/tapealerts/drive/{serialNumber}", response_model=TapeAlertListResponse)
async def get_tapealerts_for_drive(
    serialNumber: str,
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> TapeAlertListResponse:
    _ensure_state(context)
    drive = _validate_text(serialNumber, field_name="serialNumber")
    items = [
        _serialize_tapealert(item)
        for item in aml_state.list_aml_tapealerts()
        if str(item.get("drive", "")).lower() == drive.lower()
    ]
    return TapeAlertListResponse(tapeAlertList=TapeAlertListResource(tapeAlert=items))


@router.get("/notifications", response_model=NotificationListResponse)
async def list_notifications(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> NotificationListResponse:
    _ensure_state(context)
    notifications = sorted(aml_state.list_aml_notifications(), key=lambda item: str(item.get("timestamp", "")), reverse=True)
    return NotificationListResponse(
        notificationList=NotificationListResource(notification=[_serialize_notification(item) for item in notifications])
    )


@router.delete("/notifications", response_model=WSResultCode)
async def clear_notifications(
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    aml_state.clear_aml_notifications()
    return _ws_result("Cleared all notifications")


@router.get("/notifications/unread", response_model=UnreadCountResponse)
async def get_unread_notification_count(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> UnreadCountResponse:
    _ensure_state(context)
    count = sum(1 for item in aml_state.list_aml_notifications() if not bool(item.get("read", False)))
    return UnreadCountResponse(unreadCount=UnreadCount(count=count))


@router.post("/notification/{id}/read", response_model=WSResultCode)
async def mark_notification_read(
    resource_id: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    notification_id = _validate_text(resource_id, field_name="id")
    _get_notification_or_404(notification_id)
    aml_state.update_aml_notification(notification_id, {"read": True})
    return _ws_result(f"Marked notification {notification_id} as read")


@router.delete("/notification/{id}", response_model=WSResultCode)
async def delete_notification(
    resource_id: str,
    current_user: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _ensure_state(context)
    _require_admin(current_user)
    notification_id = _validate_text(resource_id, field_name="id")
    _get_notification_or_404(notification_id)
    aml_state.pop_aml_notification(notification_id)
    return _ws_result(f"Deleted notification {notification_id}")


@router.get("/health", response_model=HealthSummaryResponse)
async def get_health_summary(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> HealthSummaryResponse:
    _ensure_state(context)
    return HealthSummaryResponse(healthSummary=_health_summary(context))


@router.get("/summary", response_model=DashboardSummaryResponse)
async def get_dashboard_summary(
    _: AmlUser = Depends(require_auth),
    context: AppContext = Depends(get_context),
) -> DashboardSummaryResponse:
    _ensure_state(context)
    return DashboardSummaryResponse(summary=_dashboard_summary(context))
