"""Faithful Scalar i3 coordinate + moveMedium classification models (roadmap Phase 0).

The real i3 addresses an element by a physical coordinate
(frame/rack/section/column/row + type), not a bare integer, and classifies a
moveMedium with a ``moveClass`` bit field. OpenBlade's emulator already carries the
full coordinate dict in places (`aml_state`), but the moveMedium *input* accepts a
simplified ``{elementAddress, elementType}`` and treats ``moveClass`` as a small
magic integer. These models formalize the real structure so the convenient integer
forms live only in the adapter boundary.

IMPORTANT — values vs structure: the STRUCTURE here (coordinate fields, moveClass
bit flags) mirrors the documented i3 shape. The exact wire VALUES for ``moveClass``
are UNVERIFIED against a real appliance — the external review indicates the real
"unload" value differs from the emulator's current ``3``. The wire mapping is
centralized in :func:`MoveClass.from_wire`/:meth:`MoveClass.to_wire` so a captured
compatibility case can certify/replace it without touching call sites.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntFlag, auto


@dataclass(frozen=True)
class ScalarCoordinate:
    """A physical element coordinate on a Scalar i3.

    All fields are present so the full coordinate the appliance returns is
    preserved rather than reduced to an integer. ``element_type`` mirrors the
    library's numeric element-type code (slot/drive/etc.).
    """

    frame: int
    rack: int
    section: int
    column: int
    row: int
    element_type: int

    def to_dict(self) -> dict[str, int]:
        return {
            "frame": self.frame,
            "rack": self.rack,
            "section": self.section,
            "column": self.column,
            "row": self.row,
            "type": self.element_type,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> ScalarCoordinate:
        """Parse the full {frame,rack,section,column,row,type} form."""
        try:
            return cls(
                frame=int(data["frame"]),  # type: ignore[arg-type]
                rack=int(data["rack"]),  # type: ignore[arg-type]
                section=int(data["section"]),  # type: ignore[arg-type]
                column=int(data["column"]),  # type: ignore[arg-type]
                row=int(data["row"]),  # type: ignore[arg-type]
                element_type=int(data.get("type", data.get("element_type", 0))),  # type: ignore[arg-type]
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid ScalarCoordinate dict: {data!r}") from exc


class MoveClass(IntFlag):
    """moveMedium classification, modeled as the documented bit field.

    NORMAL is the empty flag. IMPORT/EXPORT/UNLOAD/NO_EJECT are independent bits so
    documented combinations compose (e.g. ``UNLOAD | NO_EJECT``). See the module
    docstring: bit STRUCTURE is faithful; wire VALUES are certified separately.
    """

    NORMAL = 0
    IMPORT = auto()
    EXPORT = auto()
    UNLOAD = auto()
    NO_EJECT = auto()

    @property
    def is_unload(self) -> bool:
        return bool(self & MoveClass.UNLOAD)

    @classmethod
    def from_wire(cls, value: int) -> MoveClass:
        """Map an emulator-native moveClass integer to the structured flag."""
        # Unknown integers fall back to NORMAL; callers needing strictness validate.
        return _LEGACY_MOVECLASS_WIRE.get(int(value), cls.NORMAL)

    def to_wire(self) -> int:
        """Map a structured flag back to the emulator-native integer."""
        for wire, flag in _LEGACY_MOVECLASS_WIRE.items():
            if self == flag:
                return wire
        return 0


# The single certification point: OpenBlade emulator's CURRENT moveClass integers.
# UNVERIFIED vs a real i3 (the external review indicates the real "unload" value is
# a distinct bit value, not 3). Replace once a captured compatibility case certifies
# the real mapping — no call site changes needed.
_LEGACY_MOVECLASS_WIRE: dict[int, MoveClass] = {0: MoveClass.NORMAL, 3: MoveClass.UNLOAD}
