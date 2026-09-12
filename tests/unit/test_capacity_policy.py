"""Free-space policy: the LTFS-index reserve.

A real-data-campaign finding the runbook recorded under "Reported rather than
changed" (docs/runbooks/real-data-campaign.md):

* ``_has_room_for`` rejected only a tape with *exactly* zero bytes free, while
  the stated cause -- LTFS index overhead -- means a tape with 512 bytes left
  ENOSPCs on an empty file just the same.
"""

from __future__ import annotations

from openblade.domain.capacity import (
    CAPACITY_RESERVE_MAX_BYTES,
    capacity_reserve_bytes,
    has_room_for,
    usable_remaining_bytes,
)

RIG_CAPACITY = 6_569_328_640  # measured on the rig for an 8000 MB cartridge
LTO8_CAPACITY = 11_863_283_924_992  # a real LTO-8 cartridge, 12 TB native
MHVTL_SMALL_CAPACITY = 99_614_720  # ...and for the 400 MB cartridge used to force spillover


class TestReserveSizing:
    def test_large_media_get_the_flat_ceiling(self) -> None:
        assert capacity_reserve_bytes(LTO8_CAPACITY) == CAPACITY_RESERVE_MAX_BYTES
        # 0.0006% of the cartridge: the ceiling costs nothing that matters.
        assert CAPACITY_RESERVE_MAX_BYTES * 1000 < LTO8_CAPACITY

    def test_the_rig_cartridge_lands_just_under_the_ceiling(self) -> None:
        """6.57 GB * 1% is a shade below 64 MiB, so the fraction arm wins there."""
        assert capacity_reserve_bytes(RIG_CAPACITY) == RIG_CAPACITY // 100
        assert capacity_reserve_bytes(RIG_CAPACITY) < CAPACITY_RESERVE_MAX_BYTES

    def test_small_media_get_one_percent_instead(self) -> None:
        """A flat 64 MiB reserve would write off two thirds of the rig's 95 MiB tape."""
        reserve = capacity_reserve_bytes(MHVTL_SMALL_CAPACITY)
        assert reserve == MHVTL_SMALL_CAPACITY // 100
        assert reserve < CAPACITY_RESERVE_MAX_BYTES
        # Still enormously more than the few hundred bytes that provably ENOSPCs.
        assert reserve > 512

    def test_nonsense_capacity_reserves_nothing(self) -> None:
        assert capacity_reserve_bytes(0) == 0
        assert capacity_reserve_bytes(-1) == 0


class TestReserveBoundary:
    """The boundary is the reserve, not zero. This is the whole fix."""

    def test_free_equal_to_the_reserve_means_full(self) -> None:
        reserve = capacity_reserve_bytes(RIG_CAPACITY)
        used = RIG_CAPACITY - reserve
        assert usable_remaining_bytes(RIG_CAPACITY, used) == 0
        # Not even a zero-byte file: it still costs a directory entry and index space.
        assert has_room_for(RIG_CAPACITY, used, 0) is False
        assert has_room_for(RIG_CAPACITY, used, 1) is False

    def test_one_byte_above_the_reserve_fits(self) -> None:
        reserve = capacity_reserve_bytes(RIG_CAPACITY)
        used = RIG_CAPACITY - reserve - 1
        assert usable_remaining_bytes(RIG_CAPACITY, used) == 1
        assert has_room_for(RIG_CAPACITY, used, 0) is True
        assert has_room_for(RIG_CAPACITY, used, 1) is True
        assert has_room_for(RIG_CAPACITY, used, 2) is False

    def test_the_512_bytes_the_runbook_names(self) -> None:
        """ "a tape with 512 bytes left still ENOSPCs on an empty file"."""
        assert has_room_for(RIG_CAPACITY, RIG_CAPACITY - 512, 0) is False

    def test_a_perfect_fit_below_the_reserve_is_still_a_fit(self) -> None:
        """The reserve must not become an off-by-one that rejects an exact fit."""
        reserve = capacity_reserve_bytes(RIG_CAPACITY)
        used = RIG_CAPACITY - reserve - 4096
        assert has_room_for(RIG_CAPACITY, used, 4096) is True
        assert has_room_for(RIG_CAPACITY, used, 4097) is False

    def test_overfull_media_never_reports_negative_room(self) -> None:
        assert usable_remaining_bytes(RIG_CAPACITY, RIG_CAPACITY * 2) == 0
        assert has_room_for(RIG_CAPACITY, RIG_CAPACITY * 2, 0) is False
