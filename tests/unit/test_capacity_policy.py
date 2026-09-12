"""Free-space policy: the LTFS-index reserve, and where the numbers come from.

Both halves are real-data-campaign findings that the runbook recorded under
"Reported rather than changed" (docs/runbooks/real-data-campaign.md):

* ``_has_room_for`` rejected only a tape with *exactly* zero bytes free, while
  the stated cause -- LTFS index overhead -- means a tape with 512 bytes left
  ENOSPCs on an empty file just the same.
* ``RealLTFSBackend._tapes`` was never hydrated, so after a restart every tape
  reported the fictional 12 GB default until it had been mounted once.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from openblade.domain.capacity import (
    CAPACITY_RESERVE_MAX_BYTES,
    capacity_reserve_bytes,
    has_room_for,
    usable_remaining_bytes,
)
from openblade.domain.policies import RealHardwareGuard
from openblade.hardware.ltfs import RealLTFSBackend
from openblade.hardware.runner import SafeRunner

RIG_CAPACITY = 6_569_328_640  # measured on the rig for an 8000 MB cartridge
LTO8_CAPACITY = 11_863_283_924_992  # a real LTO-8 cartridge, 12 TB native
MHVTL_SMALL_CAPACITY = 99_614_720  # ...and for the 400 MB cartridge used to force spillover


def _backend(tmp_path: Path, known_tapes: dict[str, tuple[int, int]] | None = None):
    return RealLTFSBackend(
        library=object(),  # type: ignore[arg-type]  -- unused on these paths
        guard=RealHardwareGuard(
            config_backend="real",
            config_real_hardware_enabled=True,
            operator_acknowledgment="capacity-tests",
        ),
        runner=SafeRunner(dry_run=True),
        mount_root=tmp_path,
        known_tapes=known_tapes,
    )


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


class TestTapeHydration:
    def test_capacities_come_from_the_catalog_not_the_default(self, tmp_path: Path) -> None:
        backend = _backend(
            tmp_path,
            {"OB0001L8": (RIG_CAPACITY, 4_000_000_000), "OB0002L8": (MHVTL_SMALL_CAPACITY, 0)},
        )

        assert backend.ensure_tape("OB0001L8").capacity_bytes == RIG_CAPACITY
        assert backend.ensure_tape("OB0001L8").used_bytes == 4_000_000_000
        assert backend.remaining_capacity("OB0001L8") == RIG_CAPACITY - 4_000_000_000
        assert backend.ensure_tape("OB0002L8").capacity_bytes == MHVTL_SMALL_CAPACITY
        # The point of the fix: none of these is the fictional default.
        assert backend.ensure_tape("OB0001L8").capacity_bytes != 12_000_000_000

    def test_a_hydrated_tape_is_full_before_it_is_ever_mounted(self, tmp_path: Path) -> None:
        """Spill selection has to be right on the first file after a restart."""
        backend = _backend(tmp_path, {"OB0001L8": (RIG_CAPACITY, RIG_CAPACITY - 512)})
        tape = backend.ensure_tape("OB0001L8")

        assert has_room_for(tape.capacity_bytes, tape.used_bytes, 0) is False

    def test_a_missing_row_falls_back_to_the_default_with_a_debug_log(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        backend = _backend(tmp_path, {"OB0001L8": (RIG_CAPACITY, 0)})

        with caplog.at_level(logging.DEBUG, logger="openblade.hardware.ltfs"):
            tape = backend.ensure_tape("OB0009L8")

        assert tape.capacity_bytes == 12_000_000_000
        assert tape.used_bytes == 0
        assert any("no measured capacity" in record.message for record in caplog.records)

    def test_no_catalog_at_all_is_not_a_crash(self, tmp_path: Path) -> None:
        backend = _backend(tmp_path, None)
        assert backend.ensure_tape("OB0001L8").capacity_bytes == 12_000_000_000

    def test_a_nonsense_row_is_ignored_rather_than_trusted(self, tmp_path: Path) -> None:
        backend = _backend(tmp_path, {"OB0001L8": (0, 0), "OB0002L8": (-5, 10)})

        assert backend.ensure_tape("OB0001L8").capacity_bytes == 12_000_000_000
        assert backend.ensure_tape("OB0002L8").capacity_bytes == 12_000_000_000

    def test_used_is_clamped_into_the_capacity(self, tmp_path: Path) -> None:
        backend = _backend(tmp_path, {"OB0001L8": (RIG_CAPACITY, RIG_CAPACITY * 3)})

        assert backend.ensure_tape("OB0001L8").used_bytes == RIG_CAPACITY
        assert backend.remaining_capacity("OB0001L8") == 0
