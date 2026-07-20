"""AML ``moveMedium`` robotics endpoint — OpenBlade-native APPROXIMATION, not certified.

Quantum's Web Services move a cartridge with ``POST aml/media/operations/moveMedium``
(Web Services Guide Rev D, Table 133). This module exposes that path so the
``scalar_http`` client can be developed against the emulator, BUT it is a
simplified OpenBlade dialect, NOT a certified match for a real i3:

- Coordinates are a simplified ``{elementAddress, elementType}`` object (element
  addresses are per-type here; slot and drive ids overlap). A real i3 coordinate
  is a physical ``frame/rack/section/column/row`` + type keyed on a globally-unique
  SCSI element address. Modelling that faithfully is roadmap Phase 0
  (``ScalarCoordinate``).
- ``moveClass`` is parsed as the documented bit field (0=Normal, 8=Unload,
  16=No-Eject, 32=Closest-slot; the single-integer 3=Unload is deprecated but still
  accepted) via ``MoveClass.from_wire`` — see docs/reference/i3-contract-notes.md.

Because the emulator and the ``scalar_http`` client currently share these
simplifications, client<->emulator tests passing does NOT prove i3 fidelity. That
is enforced only by the compatibility corpus (``compatibility/``, all cases still
``inferred``). Do not treat this endpoint as strict AML parity until a real
appliance capture certifies it.
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
from openblade.domain.scalar_coordinate import MoveClass

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
        move_class = MoveClass.from_wire(int(data.get("moveClass", 0) or 0))
    except (TypeError, ValueError):
        move_class = MoveClass.NORMAL

    if not move_class.is_supported_on_i3:
        # Import(2)/Export(4) are documented as not supported on Scalar i3/i6.
        raise HTTPException(
            status_code=422,
            detail="moveClass import/export is not supported on Scalar i3/i6",
        )

    source = _parse_coordinate(data.get("sourceCoordinate"))
    if source is None:
        raise HTTPException(
            status_code=422,
            detail="sourceCoordinate with elementAddress and elementType is required",
        )
    destination = _parse_coordinate(data.get("destinationCoordinate"))
    source_type, source_address = source

    try:
        if move_class.is_unload or (source_type == "drive" and destination is None):
            if source_type != "drive":
                raise HTTPException(
                    status_code=422, detail="Unload move requires a drive source"
                )
            # Real moveClass=8 unload takes the drive source only; if a destination
            # slot is supplied (client hint), honor it, else the library picks an
            # empty/home slot (Web Services manual). Guard the destination type:
            # slot and drive addresses overlap, so an accidental drive destination
            # would otherwise silently misroute the cartridge.
            if destination is not None and destination[0] != "slot":
                raise HTTPException(
                    status_code=422, detail="Unload destination must be a storage slot"
                )
            target_slot = destination[1] if destination is not None else _first_empty_slot(context)
            result = context.library.unload(source_address, target_slot)
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
