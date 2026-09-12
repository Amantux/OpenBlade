"""Free-space policy for tape media. One place, used by every selection path.

Every "can this tape still take these bytes?" decision in the product goes
through :func:`has_room_for`. There used to be two-and-a-half different answers
to that question -- ``remaining >= size`` in ``jobs/archive.py``, nothing at all
in the sharded path -- and the campaign showed what that costs.

Why a reserve rather than "> 0 bytes free"
------------------------------------------
``docs/runbooks/real-data-campaign.md`` §3.6 fixed the first half of this: a tape
with *exactly* zero bytes free was accepting zero-byte files, because
``0 >= 0`` is True, and LTFS answered ENOSPC on the directory entry. The review
(§"Reported rather than changed") pointed out the fix stopped one byte short --
the stated cause is LTFS index overhead, so the threshold has to be a reserve,
not a single byte. A tape with 512 bytes free ENOSPCs on an empty file for
exactly the same reason a tape with 0 bytes free does.

What the reserve has to cover, and why it is sized the way it is:

* LTFS writes a **complete copy of the index** into the data partition on every
  sync and on unmount; the index is XML and grows with the *file count*, on the
  order of a kilobyte per file. A tape carrying the campaign's 1,073 files
  needs roughly a megabyte of index, and a densely-populated LTO cartridge with
  a few hundred thousand files needs tens of megabytes.
* Writes are rounded up to the drive's block size (512 KiB on LTO-8), so the
  last few bytes of a medium are never actually usable.
* ``statvfs`` ``f_bavail`` is a snapshot taken while mounted. It does not
  account for the index that will be written when we unmount.
* Free space does not arrive one byte at a time. ``used_bytes`` for a tape this
  process has not mounted comes from the catalog and is byte-granular, so a
  free figure of "512 bytes" is entirely representable there and is exactly the
  state nothing can be written into.

Measured on the mhvtl rig (2026-09-11, one 8000 MB cartridge formatted, filled
with incompressible data until ``write`` returned ENOSPC):

* capacity 6,569,328,640 bytes, ``f_frsize`` 524,288 -- LTFS reports free space
  in 512 KiB blocks, so on that path ``f_bavail`` steps 524,288 -> 0 and the
  sub-block tail of the medium is never visible as free space at all.
* at ``free == 0``, creating a **zero-byte file** fails with ``ENOSPC`` --
  the campaign's failure, reproduced directly.
* unmounting that 100%-full tape succeeded, so on this rig the drive's own
  end-of-medium margin absorbed the final index write.

In other words the rig can demonstrate the zero-free half of the problem but
not the nonzero-free half, because its free-space reporting cannot express it.
The reserve is therefore sized conservatively rather than fitted to a measured
failure point: it costs 0.001% of a real LTO-8 cartridge, and the failure it
prevents cost the campaign an aborted 1,073-file archive job.

So the reserve is **the smaller of 64 MiB and 1% of the medium's capacity**:

* 64 MiB is the ceiling, and comfortably covers a full-cartridge index plus
  block-size rounding on real LTO media. Against LTO-8's ~6.6 TiB usable that is
  0.001% of the tape -- unmeasurable waste in exchange for never ENOSPCing.
* The 1% floor keeps the policy usable on *small* media, which is not a
  hypothetical: the campaign's mhvtl rig deliberately runs 400 MB cartridges
  presenting **99,614,720 bytes (95 MiB)** of usable LTFS capacity in order to
  force spillover (runbook §1). A flat 64 MiB reserve would write off two thirds
  of such a tape. 1% of 95 MiB is ~1 MiB, which is still ~2000x the 512 bytes
  that provably ENOSPCs, and scales with the file count the medium can hold.

The reserve is deliberately a constant policy and not a config knob: an operator
tuning it down to 0 reintroduces the exact campaign failure.
"""

from __future__ import annotations

# Ceiling on the reserve. See the module docstring for the derivation.
CAPACITY_RESERVE_MAX_BYTES = 64 * 1024 * 1024  # 64 MiB
# ...and the fraction of the medium used when that ceiling is too coarse.
CAPACITY_RESERVE_DIVISOR = 100  # 1%


def capacity_reserve_bytes(capacity_bytes: int) -> int:
    """Bytes held back on a medium of ``capacity_bytes`` for LTFS overhead."""
    if capacity_bytes <= 0:
        return 0
    return min(CAPACITY_RESERVE_MAX_BYTES, capacity_bytes // CAPACITY_RESERVE_DIVISOR)


def usable_remaining_bytes(capacity_bytes: int, used_bytes: int) -> int:
    """Free bytes a caller may actually plan to write, after the reserve."""
    free = capacity_bytes - used_bytes
    return max(0, free - capacity_reserve_bytes(capacity_bytes))


def has_room_for(capacity_bytes: int, used_bytes: int, size_bytes: int) -> bool:
    """Can this medium still take a file of ``size_bytes``?

    ``size_bytes`` may be 0 and a zero-byte file still consumes a directory
    entry and index space, so a tape with *no* usable room takes nothing at all
    -- this is the campaign's ENOSPC-on-empty-files bug, and the reason for the
    ``usable > 0`` term rather than a bare ``usable >= size_bytes``.
    """
    usable = usable_remaining_bytes(capacity_bytes, used_bytes)
    return usable > 0 and usable >= size_bytes
