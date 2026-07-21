"""Read-only tools the assistant can call to ground answers in live state.

Every tool is side-effect free. Executors defensively return an ``{"error": ...}``
dict rather than raising, so a partial-state failure never breaks a chat turn.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from openblade.bootstrap import get_context


def _inventory() -> dict[str, Any]:
    context = get_context()
    inv = context.library.inventory()
    occupied = [s for s in inv.slots if s.occupied]
    return {
        "library_id": getattr(inv, "library_id", ""),
        "changer_state": getattr(getattr(inv, "changer_state", None), "value", ""),
        "slots_total": len(inv.slots),
        "slots_occupied": len(occupied),
        "slots_empty": len(inv.slots) - len(occupied),
        "drives_total": len(inv.drives),
        "drives": [
            {
                "drive_id": d.drive_id,
                "state": getattr(d.drive_state, "value", str(d.drive_state)),
                "mount_state": getattr(d.mount_state, "value", str(d.mount_state)),
                "barcode": d.barcode.value if d.barcode else None,
            }
            for d in inv.drives
        ],
    }


def get_library_inventory() -> dict[str, Any]:
    try:
        return _inventory()
    except Exception as error:  # noqa: BLE001 - surface as data, never crash the turn
        return {"error": f"inventory unavailable: {error}"}


def get_drives_detail() -> dict[str, Any]:
    try:
        inv = _inventory()
        return {"drives_total": inv["drives_total"], "drives": inv["drives"]}
    except Exception as error:  # noqa: BLE001
        return {"error": f"drives unavailable: {error}"}


def get_safety_posture() -> dict[str, Any]:
    try:
        config = get_context().config
        backend = getattr(config.backend, "value", str(config.backend))
        return {
            "backend_mode": backend,
            "real_hardware_enabled": bool(config.real_hardware_enabled),
            "real_hardware_active": backend == "real" and bool(config.real_hardware_enabled),
            "scalar_api_only": bool(getattr(config, "scalar_api_only", False)),
            "hardware_dry_run": bool(getattr(config, "hardware_dry_run", False)),
            "notes": (
                "Format requires barcode confirmation + a one-time safety token; "
                "unload is blocked while LTFS is mounted or dirty. These gates are "
                "enforced by the controller and cannot be bypassed via chat."
            ),
        }
    except Exception as error:  # noqa: BLE001
        return {"error": f"safety posture unavailable: {error}"}


def get_recent_jobs() -> dict[str, Any]:
    try:
        jobs = get_context().catalog.list_jobs()
        items = [
            {
                "id": getattr(job, "id", ""),
                "type": getattr(getattr(job, "job_type", None), "value", ""),
                "state": getattr(getattr(job, "state", None), "value", str(getattr(job, "state", ""))),
            }
            for job in jobs[-15:]
        ]
        return {"job_count": len(jobs), "recent": items}
    except Exception as error:  # noqa: BLE001
        return {"error": f"jobs unavailable: {error}"}


# name -> (executor, OpenAI function-tool spec)
_TOOLS: dict[str, tuple[Callable[[], dict[str, Any]], dict[str, Any]]] = {
    "get_library_inventory": (
        get_library_inventory,
        {"description": "Slot occupancy, drive states, and loaded cartridges of the tape library."},
    ),
    "get_drives_detail": (
        get_drives_detail,
        {"description": "Per-drive status: state, mount state, and any loaded barcode."},
    ),
    "get_safety_posture": (
        get_safety_posture,
        {"description": "Backend mode and safety gating: real-hardware enablement, dry-run, API scope."},
    ),
    "get_recent_jobs": (
        get_recent_jobs,
        {"description": "Recent archive/restore/format/inventory jobs and their states."},
    ),
}


def tool_specs() -> list[dict[str, Any]]:
    """OpenAI-format tool definitions (all parameterless read-only queries)."""
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": meta["description"],
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        }
        for name, (_, meta) in _TOOLS.items()
    ]


def execute_tool(name: str) -> dict[str, Any]:
    entry = _TOOLS.get(name)
    if entry is None:
        return {"error": f"unknown tool: {name}"}
    executor, _ = entry
    return executor()


def build_grounding_snapshot() -> dict[str, Any]:
    """A compact read-only snapshot injected into the system context each turn."""
    return {
        "inventory": get_library_inventory(),
        "safety": get_safety_posture(),
        "jobs": get_recent_jobs(),
    }
