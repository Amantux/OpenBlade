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
from enum import IntFlag


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


# Deprecated single-integer "Unload" (Web Services manual marks moveClass=3 as
# deprecated in favour of the bit field). The emulator historically used it; still
# accepted on input for backward compatibility.
_DEPRECATED_UNLOAD_WIRE = 3


class MoveClass(IntFlag):
    """moveMedium classification — the real documented bit field.

    Values are from the Quantum Web Services manual (6-68185-01 Rev D); see
    docs/reference/i3-contract-notes.md. Flags compose, e.g. ``24 = UNLOAD | NO_EJECT``.
    """

    NORMAL = 0
    IMPORT = 2
    EXPORT = 4
    UNLOAD = 8
    NO_EJECT = 16
    CLOSEST_SLOT = 32

    @property
    def is_unload(self) -> bool:
        return bool(self & MoveClass.UNLOAD)

    @classmethod
    def from_wire(cls, value: int) -> MoveClass:
        """Parse a moveClass integer into the structured flag.

        Accepts the real bit field (8=unload, 16=no-eject, 24=8+16, 32=closest, …)
        and the deprecated single-integer ``3`` (=unload). Unknown bits are ignored.
        """
        value = int(value)
        if value == _DEPRECATED_UNLOAD_WIRE:
            return cls.UNLOAD
        result = cls.NORMAL
        for flag in (cls.IMPORT, cls.EXPORT, cls.UNLOAD, cls.NO_EJECT, cls.CLOSEST_SLOT):
            if value & flag.value:
                result |= flag
        return result

    def to_wire(self) -> int:
        """The documented integer for this flag (bit field). Unload -> 8, not the deprecated 3."""
        return int(self)
