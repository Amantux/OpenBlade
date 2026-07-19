"""AML ``moveMedium`` robotics endpoint (real Quantum i3 dialect).

Quantum's Web Services move a cartridge with ``POST aml/media/operations/moveMedium``
(Web Services Guide Rev D, Table 133) carrying a ``sourceCoordinate`` and
``destinationCoordinate`` plus a ``moveClass`` (0 = normal, 3 = unload to home
slot). OpenBlade's own emulator historically exposed ``/aml/operations/move``
instead; this module adds the faithful ``moveMedium`` surface so the
``scalar_http`` client (which must speak the real i3 dialect) can be developed and
tested against the emulator, and so the emulator matches the parity matrix.

Fidelity note: a real i3 coordinate keys on a globally-unique SCSI
``elementAddress``. The emulator's element addresses are per-type (slot ids and
drive ids overlap), so this endpoint accepts a coordinate object of
``{elementAddress, elementType}`` where ``elementType`` disambiguates slot vs
drive. Mapping to a real library's unified element-address space is a
milestone-2 hardening item (tracked with the serial<->/dev/st correlation).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from openblade.api.routes_aml_auth import (
    WSResultCode,
    _require_admin,
    _ws_result,
    require_auth,
)
from openblade.bootstrap import AppContext, get_context
from openblade.catalog.models import AmlUser

router = APIRouter()


def _parse_coordinate(value: Any) -> tuple[str, int] | None:
    if not isinstance(value, dict):
        return None
    address = value.get("elementAddress")
    element_type = value.get("elementType")
    if address is None or element_type is None:
        return None
    try:
        return str(element_type), int(address)
    except (TypeError, ValueError):
        return None


def _first_empty_slot(context: AppContext) -> int:
    for slot in context.library.inventory().slots:
        if not slot.occupied:
            return slot.slot_id
    raise HTTPException(status_code=400, detail="No empty storage slot available for unload")


@router.post(
    "/media/operations/moveMedium",
    response_model=WSResultCode,
    dependencies=[Depends(require_auth)],
)
async def move_medium(
    current_user: AmlUser = Depends(require_auth),
    payload: dict[str, Any] = Body(...),
    context: AppContext = Depends(get_context),
) -> WSResultCode:
    _require_admin(current_user)
    raw = payload.get("moveMedium")
    data = raw if isinstance(raw, dict) else payload

    try:
        move_class = int(data.get("moveClass", 0) or 0)
    except (TypeError, ValueError):
        move_class = 0

    source = _parse_coordinate(data.get("sourceCoordinate"))
    if source is None:
        raise HTTPException(
            status_code=422,
            detail="sourceCoordinate with elementAddress and elementType is required",
        )
    destination = _parse_coordinate(data.get("destinationCoordinate"))
    source_type, source_address = source

    try:
        if move_class == 3 or (source_type == "drive" and destination is None):
            if source_type != "drive":
                raise HTTPException(
                    status_code=422, detail="Unload (moveClass 3) requires a drive source"
                )
            result = context.library.unload(source_address, _first_empty_slot(context))
        elif destination is None:
            raise HTTPException(status_code=422, detail="destinationCoordinate is required")
        else:
            destination_type, destination_address = destination
            if destination_type == "drive":
                result = context.library.load(source_address, destination_address)
            elif source_type == "drive":
                result = context.library.unload(source_address, destination_address)
            else:
                result = context.library.move(source_address, destination_address)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - surface backend failure as an AML error
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not getattr(result, "success", False):
        raise HTTPException(status_code=400, detail=getattr(result, "message", "moveMedium failed"))
    return _ws_result("moveMedium completed")
