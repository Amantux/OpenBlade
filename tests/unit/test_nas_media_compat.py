from __future__ import annotations

import pytest

from openblade.nas.media import (
    Cartridge,
    ForeignMediaError,
    IncompatibleMediaError,
    MediaGeneration,
    can_read,
    can_write,
    is_foreign,
    require_writable,
)
from openblade.nas.planner import ArchivePlanner
from openblade.nas.types import ArchivePlanRequest, PolicyType

G = MediaGeneration


@pytest.mark.parametrize(
    ("drive", "readable", "writable"),
    [
        (G.LTO6, {G.LTO5, G.LTO6}, {G.LTO5, G.LTO6}),
        (G.LTO7, {G.LTO5, G.LTO6, G.LTO7}, {G.LTO6, G.LTO7}),
        # LTO-8 and LTO-9 break the N-2 read rule.
        (G.LTO8, {G.LTO7, G.LTO8}, {G.LTO7, G.LTO8}),
        (G.LTO9, {G.LTO8, G.LTO9}, {G.LTO8, G.LTO9}),
    ],
)
def test_compat_matrix(drive: G, readable: set[G], writable: set[G]) -> None:
    assert {m for m in G if can_read(drive, m)} == readable
    assert {m for m in G if can_write(drive, m)} == writable


def test_lto8_cannot_read_lto6_and_lto9_cannot_read_lto7() -> None:
    assert not can_read(G.LTO8, G.LTO6)
    assert not can_read(G.LTO9, G.LTO7)


def test_unknown_media_never_compatible() -> None:
    assert not any(can_read(d, G.UNKNOWN) or can_write(d, G.UNKNOWN) for d in G)


def test_foreign_detection() -> None:
    assert is_foreign(Cartridge(barcode="F00001L8"))
    assert not is_foreign(Cartridge(barcode="F00001L8", adopted=True))
    assert not is_foreign(Cartridge(barcode="A00001L8", has_tape_json=True))


def test_require_writable_typed_errors() -> None:
    with pytest.raises(ForeignMediaError):
        require_writable(Cartridge(barcode="F1", generation=G.LTO8), G.LTO8)
    with pytest.raises(IncompatibleMediaError):
        require_writable(Cartridge(barcode="A1", generation=G.LTO6, has_tape_json=True), G.LTO8)


def _request(cart: Cartridge, drive: G | None = None) -> ArchivePlanRequest:
    return ArchivePlanRequest(
        files=["/a"],
        file_sizes={"/a": 10},
        available_tapes=[cart.barcode],
        cartridges={cart.barcode: cart},
        drive_generation=drive,
    )


def test_archive_planner_refuses_foreign_media_without_adopt() -> None:
    foreign = Cartridge(barcode="F00001L8", generation=G.LTO8)
    with pytest.raises(ForeignMediaError):
        ArchivePlanner().plan(_request(foreign))
    adopted = foreign.model_copy(update={"adopted": True})
    ArchivePlanner().plan(_request(adopted))


def test_archive_planner_refuses_unwritable_pairing() -> None:
    old = Cartridge(barcode="A00001L6", generation=G.LTO6, has_tape_json=True)
    with pytest.raises(IncompatibleMediaError):
        ArchivePlanner().plan(_request(old, G.LTO8))
    ArchivePlanner().plan(_request(old, G.LTO7))


def test_archive_plan_reserved_bytes_block_plan_that_would_otherwise_fit() -> None:
    def req(reserved: int) -> ArchivePlanRequest:
        return ArchivePlanRequest(
            policy_type=PolicyType.CRITICAL_SEQUENTIAL,
            files=["/a"],
            file_sizes={"/a": 80},
            available_tapes=["T1"],
            tape_capacities={"T1": 100},
            reserved_bytes={"T1": reserved},
        )

    assert ArchivePlanner().plan(req(0)).is_safe_to_enqueue
    assert not ArchivePlanner().plan(req(30)).is_safe_to_enqueue
