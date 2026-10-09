"""LTO media generations, drive compatibility, and foreign-media detection."""

from __future__ import annotations

from enum import IntEnum

from pydantic import BaseModel, ConfigDict, Field


class MediaGeneration(IntEnum):
    UNKNOWN = 0
    LTO5 = 5
    LTO6 = 6
    LTO7 = 7
    LTO8 = 8
    LTO9 = 9


# LTO Program (lto.org) compatibility rules:
# - LTO-5, LTO-6, LTO-7 drives follow the classic rule: read back two generations
#   (N-2) and write back one generation (N-1).
# - LTO-8 broke the N-2 read rule: an LTO-8 drive reads and writes LTO-7 and
#   LTO-8 media only (no LTO-6 read).
# - LTO-9 reads and writes LTO-8 and LTO-9 media only.
# (LTO-7 Type M / M8 media is not modelled; it is reported as LTO7.)
_READ: dict[MediaGeneration, frozenset[MediaGeneration]] = {
    # LTO-5 also reads LTO-3/LTO-4, which are outside the modelled range.
    MediaGeneration.LTO5: frozenset({MediaGeneration.LTO5}),
    MediaGeneration.LTO6: frozenset({MediaGeneration.LTO5, MediaGeneration.LTO6}),
    MediaGeneration.LTO7: frozenset(
        {MediaGeneration.LTO5, MediaGeneration.LTO6, MediaGeneration.LTO7}
    ),
    MediaGeneration.LTO8: frozenset({MediaGeneration.LTO7, MediaGeneration.LTO8}),
    MediaGeneration.LTO9: frozenset({MediaGeneration.LTO8, MediaGeneration.LTO9}),
}
_WRITE: dict[MediaGeneration, frozenset[MediaGeneration]] = {
    MediaGeneration.LTO5: frozenset({MediaGeneration.LTO5}),
    MediaGeneration.LTO6: frozenset({MediaGeneration.LTO5, MediaGeneration.LTO6}),
    MediaGeneration.LTO7: frozenset({MediaGeneration.LTO6, MediaGeneration.LTO7}),
    MediaGeneration.LTO8: frozenset({MediaGeneration.LTO7, MediaGeneration.LTO8}),
    MediaGeneration.LTO9: frozenset({MediaGeneration.LTO8, MediaGeneration.LTO9}),
}


def can_read(drive_gen: MediaGeneration, media_gen: MediaGeneration) -> bool:
    return media_gen in _READ.get(drive_gen, frozenset())


def can_write(drive_gen: MediaGeneration, media_gen: MediaGeneration) -> bool:
    return media_gen in _WRITE.get(drive_gen, frozenset())


class IncompatibleMediaError(ValueError):
    """Drive generation cannot perform the requested operation on the media."""

    def __init__(self, drive_gen: MediaGeneration, media_gen: MediaGeneration, op: str) -> None:
        super().__init__(f"{drive_gen.name} drive cannot {op} {media_gen.name} media")
        self.drive_gen = drive_gen
        self.media_gen = media_gen
        self.op = op


class ForeignMediaError(ValueError):
    """Media not formatted by OpenBlade; must be explicitly adopted first."""

    def __init__(self, barcode: str) -> None:
        super().__init__(f"cartridge {barcode} is foreign media; adopt it before use")
        self.barcode = barcode


class Cartridge(BaseModel):
    model_config = ConfigDict(frozen=True)

    barcode: str = Field(min_length=1)
    generation: MediaGeneration = MediaGeneration.UNKNOWN
    has_tape_json: bool = False
    adopted: bool = False


def is_foreign(cartridge: Cartridge) -> bool:
    """True for media OpenBlade did not format (no tape.json) and that was not adopted.

    Foreign media is read-only for planning and is never auto-formatted.
    """
    return not cartridge.has_tape_json and not cartridge.adopted


def require_writable(cartridge: Cartridge, drive_gen: MediaGeneration) -> None:
    """Planner guard for a tape write: refuses foreign or incompatible media."""
    if is_foreign(cartridge):
        raise ForeignMediaError(cartridge.barcode)
    if not can_write(drive_gen, cartridge.generation):
        raise IncompatibleMediaError(drive_gen, cartridge.generation, "write")
