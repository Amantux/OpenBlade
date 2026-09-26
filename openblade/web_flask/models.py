from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
        id=int(payload.get("id", 0)),
        name=str(payload.get("name", "Unknown Device")),
        emulator_url=str(payload.get("emulator_url", "")),
        model=str(payload.get("model", "Scalar i3")),
        role=str(payload.get("role", "primary")),
        status=str(payload.get("status", "unknown")).lower(),
        enabled=bool(payload.get("enabled", True)),
        sort_order=int(payload.get("sort_order", 0)),
    )
