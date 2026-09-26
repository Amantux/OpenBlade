from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _coerce_int(value: Any, *, default: int = 0) -> int:
    """int() a backend-supplied field without raising.

    ``int(payload.get("id", 0))`` dies on an explicit ``None`` -- ``.get`` only
    returns the default when the key is ABSENT, and the API can legitimately send
    ``{"id": null}``. That turned a device listing into a 500.
    """
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True, slots=True)
class Device:
    id: int
    name: str
    emulator_url: str
    model: str
    role: str
    status: str
    enabled: bool
    sort_order: int


def parse_device(payload: dict[str, Any]) -> Device:
    return Device(
        id=_coerce_int(payload.get("id")),
        name=str(payload.get("name", "Unknown Device")),
        emulator_url=str(payload.get("emulator_url", "")),
        model=str(payload.get("model", "Scalar i3")),
        role=str(payload.get("role", "primary")),
        status=str(payload.get("status", "unknown")).lower(),
        enabled=bool(payload.get("enabled", True)),
        sort_order=_coerce_int(payload.get("sort_order")),
    )
