"""Canonical mock Scalar i3 configuration shared by simulator and AML state.

The library shape is built from a :class:`ScalarI3Profile` so the emulator can
represent any supported i3 configuration (the cross-repo contract advertises
``EMULATOR_SLOT_COUNT`` / ``EMULATOR_DRIVE_COUNT`` / ``EMULATOR_PROFILE`` /
``EMULATOR_OCCUPANCY_PERCENT`` — see ``openblade/emulator_contract/contract.json``).
``scalar_i3_default_config()`` returns the canonical ``scalar-i3-50-3`` shape
(byte-identical to the historical literal); ``scalar_i3_active_config()`` returns
the env-selected shape, defaulting to that same default when nothing is set. An
invalid configuration raises ``ValueError`` at build time (fail-fast — the emulator
must behave like a real i3, which cannot exceed 6 drives).
"""

from __future__ import annotations

import math
import os
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

# --- Grounded hardware limits -------------------------------------------------
# Scalar i3 supports up to 6 drives (docs/sharding.md). Slot counts have no crisp
# in-repo documented maximum, so they are validated as sane positive values and
# treated as OpenBlade emulator profiles rather than exact SKU fidelity.
_MAX_DRIVES = 6

# Capacity (native, bytes) by LTO generation. LTO-9 = 18 TB is already used in
# bootstrap.py / routes_aml_drives.py.
_CAPACITY_BY_GENERATION: dict[str, int] = {
    "LTO-7": 6_000_000_000_000,
    "LTO-8": 12_000_000_000_000,
    "LTO-9": 18_000_000_000_000,
}

# Per-generation drive firmware for generated (non-default) drives.
_FIRMWARE_BY_GENERATION: dict[str, str] = {
    "LTO-7": "G7A3",
    "LTO-8": "H8B2",
    "LTO-9": "J9C2",
}

_DATA_POOLS = ("Critical Projects", "General Archive", "Cold Storage", "Media Cache")
_DATA_OWNERS = ("engineering", "media", "operations", "backup")
_DATA_RETENTION = ("critical", "standard", "cold", "backup")


@dataclass(frozen=True)
class ScalarI3Profile:
    """A validated Scalar i3 library shape."""

    profile_name: str
    slot_count: int
    # One LTO generation per drive; its length is the drive count.
    drive_generation_mix: tuple[str, ...]
    occupancy_percent: int
    ie_slot_count: int = 2
    partition_count: int = 1
    # Optional canonical drive list (used by the default profile to stay
    # byte-identical to the historical literal). When None, drives are generated.
    drive_overrides: tuple[dict[str, Any], ...] | None = None

    @property
    def drive_count(self) -> int:
        return len(self.drive_generation_mix)

    def validate(self) -> None:
        if not 1 <= self.drive_count <= _MAX_DRIVES:
            raise ValueError(
                f"drive_count must be between 1 and {_MAX_DRIVES} (Scalar i3 max); "
                f"got {self.drive_count} for profile {self.profile_name!r}"
            )
        unknown = [g for g in self.drive_generation_mix if g not in _CAPACITY_BY_GENERATION]
        if unknown:
            raise ValueError(
                f"unknown LTO generation(s) {unknown} in profile {self.profile_name!r}; "
                f"supported: {sorted(_CAPACITY_BY_GENERATION)}"
            )
        if self.slot_count < self.drive_count + 1:
            raise ValueError(
                f"slot_count ({self.slot_count}) must be at least drive_count + 1 "
                f"({self.drive_count + 1}) to fit one cleaning slot per drive plus data"
            )
        if not 0 <= self.occupancy_percent <= 100:
            raise ValueError(
                f"occupancy_percent must be in 0..100; got {self.occupancy_percent}"
            )
        if self.partition_count < 1:
            raise ValueError(f"partition_count must be >= 1; got {self.partition_count}")
        if self.partition_count > self.drive_count:
            raise ValueError(
                f"partition_count ({self.partition_count}) cannot exceed drive_count "
                f"({self.drive_count}); each partition needs at least one drive"
            )
        if self.ie_slot_count < 0:
            raise ValueError(f"ie_slot_count must be >= 0; got {self.ie_slot_count}")
        if self.drive_overrides is not None and len(self.drive_overrides) != self.drive_count:
            raise ValueError(
                f"drive_overrides length ({len(self.drive_overrides)}) must match "
                f"drive_count ({self.drive_count})"
            )


# --- The canonical default drive list (byte-identical to the historical literal) ---
_DEFAULT_DRIVES: tuple[dict[str, Any], ...] = (
    {
        "id": "DRV-001",
        "serial": "IBM-LTO7-001",
        "model": "IBM LTO-7 HH",
        "type": "LTO-7",
        "location": "1,1,1",
        "firmware": "G7A3",
        "status": "online",
        "state": "idle",
        "loadCount": 143,
        "cleaningCount": 4,
        "lastCleaned": "2024-01-20T03:15:00Z",
    },
    {
        "id": "DRV-002",
        "serial": "IBM-LTO7-002",
        "model": "IBM LTO-7 HH",
        "type": "LTO-7",
        "location": "1,1,2",
        "firmware": "G7A3",
        "status": "online",
        "state": "idle",
        "loadCount": 97,
        "cleaningCount": 3,
        "lastCleaned": "2024-01-18T11:40:00Z",
    },
    {
        "id": "DRV-003",
        "serial": "IBM-LTO8-001",
        "model": "IBM LTO-8 HH",
        "type": "LTO-8",
        "location": "1,2,1",
        "firmware": "H8B2",
        "status": "online",
        "state": "idle",
        "loadCount": 61,
        "cleaningCount": 2,
        "lastCleaned": "2024-01-17T22:05:00Z",
    },
)

DEFAULT_PROFILE = ScalarI3Profile(
    profile_name="scalar-i3-50-3",
    slot_count=50,
    drive_generation_mix=("LTO-7", "LTO-7", "LTO-8"),
    # 60% of the 47 non-cleaning slots -> round(28.2) == 28 data tapes (unchanged).
    occupancy_percent=60,
    ie_slot_count=2,
    partition_count=1,
    drive_overrides=_DEFAULT_DRIVES,
)

# Representative valid configurations exercised by the compliance suite + CI.
NAMED_PROFILES: dict[str, ScalarI3Profile] = {
    "scalar-i3-25-1": ScalarI3Profile(
        profile_name="scalar-i3-25-1",
        slot_count=25,
        drive_generation_mix=("LTO-7",),
        occupancy_percent=60,
    ),
    "scalar-i3-50-3": DEFAULT_PROFILE,
    "scalar-i3-100-3": ScalarI3Profile(
        profile_name="scalar-i3-100-3",
        slot_count=100,
        drive_generation_mix=("LTO-7", "LTO-8", "LTO-8"),
        occupancy_percent=55,
    ),
    "scalar-i3-50-6": ScalarI3Profile(
        profile_name="scalar-i3-50-6",
        slot_count=50,
        drive_generation_mix=("LTO-8", "LTO-8", "LTO-8", "LTO-8", "LTO-8", "LTO-8"),
        occupancy_percent=70,
    ),
    "scalar-i3-50-3-lto9": ScalarI3Profile(
        profile_name="scalar-i3-50-3-lto9",
        slot_count=50,
        drive_generation_mix=("LTO-9", "LTO-9", "LTO-9"),
        occupancy_percent=60,
    ),
    "scalar-i3-50-4-p2": ScalarI3Profile(
        profile_name="scalar-i3-50-4-p2",
        slot_count=50,
        drive_generation_mix=("LTO-7", "LTO-7", "LTO-8", "LTO-8"),
        occupancy_percent=60,
        partition_count=2,
    ),
}


def _slots_per_bay(slot_count: int) -> int:
    return slot_count // 2


def _slot_address(slot_id: int, slot_count: int) -> str:
    slots_per_bay = _slots_per_bay(slot_count)
    bay = 1 if slot_id <= slots_per_bay else 2
    bay_slot = slot_id if bay == 1 else slot_id - slots_per_bay
    return f"1,{bay},{bay_slot}"


def _data_tape_count(profile: ScalarI3Profile) -> int:
    fillable = profile.slot_count - profile.drive_count  # cleaning slots excluded
    return max(0, round(profile.occupancy_percent / 100 * fillable))


def _tape_generation_boundaries(profile: ScalarI3Profile, data_count: int) -> list[str]:
    """Assign a generation to each data slot 1..data_count.

    Distinct generations follow the drive mix order, weighted by their share of the
    drives (``floor`` per generation, remainder to the last). For the default this
    yields 18×LTO-7 then 10×LTO-8 — the historical slot<=18 boundary — byte-identical.
    """
    generations: list[str] = list(dict.fromkeys(profile.drive_generation_mix))
    if not generations:
        return []
    counts: list[int] = [
        math.floor(data_count * profile.drive_generation_mix.count(gen) / profile.drive_count)
        for gen in generations
    ]
    counts[-1] = data_count - sum(counts[:-1])
    per_slot: list[str] = []
    for gen, count in zip(generations, counts, strict=True):
        per_slot.extend([gen] * count)
    return per_slot


def _partition_name_for_slot(slot_id: int, profile: ScalarI3Profile) -> str:
    if profile.partition_count == 1:
        return "partition1"
    per_partition = math.ceil(profile.slot_count / profile.partition_count)
    index = min((slot_id - 1) // per_partition, profile.partition_count - 1)
    return f"partition{index + 1}"


def _build_data_media(profile: ScalarI3Profile) -> list[dict[str, Any]]:
    data_count = _data_tape_count(profile)
    per_slot_gen = _tape_generation_boundaries(profile, data_count)
    media: list[dict[str, Any]] = []
    for slot in range(1, data_count + 1):
        tape_type = per_slot_gen[slot - 1]
        capacity_bytes = _CAPACITY_BY_GENERATION[tape_type]
        utilization = 0.24 + ((slot * 11) % 52) / 100
        used_bytes = int(capacity_bytes * utilization)
        media.append(
            {
                # Keep existing barcode pattern to remain compatible with seeded demo data/tests.
                "barcode": f"VOL{slot:03d}L9",
                "type": tape_type,
                "partition": _partition_name_for_slot(slot, profile),
                "slotAddress": _slot_address(slot, profile.slot_count),
                "mockSlotId": slot,
                "role": "data",
                "poolName": _DATA_POOLS[(slot - 1) % len(_DATA_POOLS)],
                "usedBytes": used_bytes,
                "capacityBytes": capacity_bytes,
                "metadata": {
                    "dataset": f"Dataset {slot:03d}",
                    "owner": _DATA_OWNERS[(slot - 1) % len(_DATA_OWNERS)],
                    "retentionClass": _DATA_RETENTION[(slot - 1) % len(_DATA_RETENTION)],
                },
                "loadCount": 60 + (slot * 3),
                "errorCount": 1 if slot % 9 == 0 else 0,
                "lastLoaded": f"2024-01-{10 + (slot % 18):02d}T{2 + (slot % 20):02d}:{(slot * 7) % 60:02d}:00Z",
            }
        )
    return media


def _cleaning_slot_ids(profile: ScalarI3Profile) -> tuple[int, ...]:
    # One cleaning slot per drive, at the top-most slot ids.
    return tuple(range(profile.slot_count - profile.drive_count + 1, profile.slot_count + 1))


def _build_cleaning_media(profile: ScalarI3Profile) -> list[dict[str, Any]]:
    cleaning: list[dict[str, Any]] = []
    for index, (slot_id, drive_type) in enumerate(
        zip(_cleaning_slot_ids(profile), profile.drive_generation_mix, strict=True), start=1
    ):
        cleaning.append(
            {
                "barcode": f"CLN{index:03d}L9",
                "type": f"{drive_type}-CLN",
                "partition": None,
                "slotAddress": _slot_address(slot_id, profile.slot_count),
                "mockSlotId": slot_id,
                "role": "cleaning",
                "poolName": "Cleaning Pool",
                "usedBytes": 0,
                "capacityBytes": 0,
                "metadata": {"cleaningCyclesRemaining": 36 - (index * 6), "owner": "library"},
                "loadCount": 18 + (index * 4),
                "errorCount": 0,
                "lastLoaded": f"2024-01-{20 + index:02d}T05:30:00Z",
            }
        )
    return cleaning


def _build_drives(profile: ScalarI3Profile) -> list[dict[str, Any]]:
    if profile.drive_overrides is not None:
        return [deepcopy(drive) for drive in profile.drive_overrides]
    drives_in_bay1 = math.ceil(profile.drive_count / 2)
    per_generation_serial: dict[str, int] = {}
    drives: list[dict[str, Any]] = []
    for index, gen in enumerate(profile.drive_generation_mix, start=1):
        bay = 1 if index <= drives_in_bay1 else 2
        position = index if bay == 1 else index - drives_in_bay1
        serial_seq = per_generation_serial.get(gen, 0) + 1
        per_generation_serial[gen] = serial_seq
        gen_compact = gen.replace("-", "")
        drives.append(
            {
                "id": f"DRV-{index:03d}",
                "serial": f"IBM-{gen_compact}-{serial_seq:03d}",
                "model": f"IBM {gen} HH",
                "type": gen,
                "location": f"1,{bay},{position}",
                "firmware": _FIRMWARE_BY_GENERATION[gen],
                "status": "online",
                "state": "idle",
                "loadCount": 140 - (index - 1) * 20,
                "cleaningCount": max(1, 5 - index),
                "lastCleaned": f"2024-01-{20 - (index - 1):02d}T03:15:00Z",
            }
        )
    return drives


def _build_partition(profile: ScalarI3Profile, data_media: list[dict[str, Any]], cleaning_count: int) -> dict[str, Any]:
    return {
        "name": "partition1",
        "id": "PART-001",
        "status": "online",
        "type": "data",
        "slotCount": profile.slot_count,
        "ieSlotCount": profile.ie_slot_count,
        "cleaningSlots": cleaning_count,
        "slotAddresses": [item["slotAddress"] for item in data_media],
    }


def _build_partitions(profile: ScalarI3Profile, drives: list[dict[str, Any]]) -> list[dict[str, Any]]:
    per_partition_slots = math.ceil(profile.slot_count / profile.partition_count)
    partitions: list[dict[str, Any]] = []
    for index in range(profile.partition_count):
        start = index * per_partition_slots + 1
        end = min((index + 1) * per_partition_slots, profile.slot_count)
        partition_drives = [d["id"] for i, d in enumerate(drives) if i % profile.partition_count == index]
        partitions.append(
            {
                "name": f"partition{index + 1}",
                "id": f"PART-{index + 1:03d}",
                "status": "online",
                "type": "data",
                "slotCount": max(0, end - start + 1),
                "ieSlotCount": profile.ie_slot_count,
                "driveIds": partition_drives,
            }
        )
    return partitions


def _build_ie_stations(profile: ScalarI3Profile) -> list[dict[str, Any]]:
    return [
        {
            "id": "IE-1",
            "serialNumber": "IE0001",
            "status": "online",
            "state": "closed",
            "slots": [
                {
                    "id": f"IE-1-S{slot}",
                    "address": f"0,0,{slot}",
                    "state": "empty",
                    "barcode": None,
                    "type": "ie",
                }
                for slot in range(1, profile.ie_slot_count + 1)
            ],
        }
    ]


def _build_ltfs_sections(profile: ScalarI3Profile, data_media: list[dict[str, Any]], drives: list[dict[str, Any]]) -> list[dict[str, Any]]:
    roles = ("primary", "secondary", "auxiliary")
    drive_refs = [
        {
            "serialNumber": drive["id"],
            "state": "available",
            "role": roles[index] if index < len(roles) else "auxiliary",
        }
        for index, drive in enumerate(drives)
    ]
    return [
        {
            "sectionNumber": section_number,
            "name": str(media["barcode"]),
            "status": "unmounted",
            "mounted": False,
            "mountPoint": f"/ltfs/{media['barcode']}",
            "fileSystem": "LTFS 2.4",
            "partitionName": str(media["partition"]),
            "readOnly": False,
            "lastMounted": None,
            "drives": deepcopy(drive_refs),
            "media": [
                {"barcode": str(media["barcode"]), "state": "cataloged", "type": str(media["type"])}
            ],
        }
        for section_number, media in enumerate(data_media, start=1)
    ]


def build_scalar_i3_config(profile: ScalarI3Profile) -> dict[str, Any]:
    """Build a full mock Scalar i3 config dict from a validated profile."""
    profile.validate()
    data_media = _build_data_media(profile)
    cleaning_media = _build_cleaning_media(profile)
    drives = _build_drives(profile)
    config: dict[str, Any] = {
        "library": {
            "name": "OpenBlade Scalar i3",
            "serial": "OB-SCALAR-I3-001",
            "serialNumber": "OB-SCALAR-I3-001",
            "model": "Scalar i3",
            "firmware": "i3-6.0.1-openblade",
            "status": "online",
            "mockLibraryId": "mock-i3-001",
        },
        "drives": drives,
        "partition": _build_partition(profile, data_media, len(cleaning_media)),
        "media": [*data_media, *cleaning_media],
        "ieStations": _build_ie_stations(profile),
        "ltfsSections": _build_ltfs_sections(profile, data_media, drives),
    }
    # The `partitions` list is additive and only present for multi-partition
    # profiles, so single-partition (incl. default) output stays byte-identical.
    if profile.partition_count > 1:
        config["partitions"] = _build_partitions(profile, drives)
    return config


_SCALAR_I3_DEFAULT: dict[str, Any] = build_scalar_i3_config(DEFAULT_PROFILE)


def scalar_i3_default_config() -> dict[str, Any]:
    """Return the canonical default ``scalar-i3-50-3`` config (deep-copied)."""
    return deepcopy(_SCALAR_I3_DEFAULT)


_PROFILE_NAME_RE = re.compile(r"\Ascalar-i3-(\d+)-(\d+)(?:-.*)?\Z")


def _profile_from_env() -> ScalarI3Profile | None:
    """Build a profile from the contracted EMULATOR_* env knobs, or None if unset."""
    profile_name = os.environ.get("EMULATOR_PROFILE", "").strip()
    slot_override = os.environ.get("EMULATOR_SLOT_COUNT", "").strip()
    drive_override = os.environ.get("EMULATOR_DRIVE_COUNT", "").strip()
    occupancy_override = os.environ.get("EMULATOR_OCCUPANCY_PERCENT", "").strip()
    if not any((profile_name, slot_override, drive_override, occupancy_override)):
        return None

    base = DEFAULT_PROFILE
    slot_count = base.slot_count
    drive_count = base.drive_count
    occupancy = base.occupancy_percent
    ie_slot_count = base.ie_slot_count
    partition_count = base.partition_count
    mix: tuple[str, ...] | None = None

    if profile_name:
        named = NAMED_PROFILES.get(profile_name)
        if named is not None:
            base = named
            slot_count = named.slot_count
            drive_count = named.drive_count
            occupancy = named.occupancy_percent
            ie_slot_count = named.ie_slot_count
            partition_count = named.partition_count
            mix = named.drive_generation_mix
        else:
            match = _PROFILE_NAME_RE.match(profile_name)
            if match is None:
                raise ValueError(
                    f"EMULATOR_PROFILE {profile_name!r} is not a known profile nor of the "
                    f"form 'scalar-i3-<slots>-<drives>'"
                )
            slot_count = int(match.group(1))
            drive_count = int(match.group(2))

    # Per-field env overrides win over the parsed/named profile.
    if slot_override:
        slot_count = int(slot_override)
    if drive_override:
        drive_count = int(drive_override)
    if occupancy_override:
        occupancy = int(occupancy_override)

    # Derive the generation mix when a raw count changed it (extend LTO-7…LTO-8).
    if mix is None or len(mix) != drive_count:
        mix = _default_generation_mix(drive_count)

    return ScalarI3Profile(
        profile_name=profile_name or f"scalar-i3-{slot_count}-{drive_count}",
        slot_count=slot_count,
        drive_generation_mix=mix,
        occupancy_percent=occupancy,
        ie_slot_count=ie_slot_count,
        partition_count=min(partition_count, drive_count),
        drive_overrides=None,
    )


def _default_generation_mix(drive_count: int) -> tuple[str, ...]:
    """A deterministic LTO mix for an arbitrary drive count (2/3 LTO-7, rest LTO-8)."""
    if drive_count < 1:
        raise ValueError(f"drive_count must be >= 1; got {drive_count}")
    lto7 = max(1, math.ceil(drive_count * 2 / 3))
    lto7 = min(lto7, drive_count)
    return tuple(["LTO-7"] * lto7 + ["LTO-8"] * (drive_count - lto7))


def scalar_i3_active_config() -> dict[str, Any]:
    """Return the env-selected config, or the default when no EMULATOR_* knob is set."""
    profile = _profile_from_env()
    if profile is None:
        return scalar_i3_default_config()
    return build_scalar_i3_config(profile)


def scalar_i3_mock_library_barcodes(*, include_cleaning: bool = True) -> list[str]:
    # Bound to the DEFAULT config on purpose: bootstrap.py seeds the demo NAS
    # catalog from fixed VOLxxxL9 barcodes via a membership guard, so this must
    # stay deterministic regardless of the active emulator profile.
    media = scalar_i3_default_config()["media"]
    return [
        str(item["barcode"])
        for item in media
        if include_cleaning or str(item.get("role", "data")) != "cleaning"
    ]


def scalar_i3_data_barcodes() -> list[str]:
    return scalar_i3_mock_library_barcodes(include_cleaning=False)
