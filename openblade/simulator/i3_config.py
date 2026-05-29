"""Canonical mock Scalar i3 configuration shared by simulator and AML state."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_TOTAL_SLOTS = 50
_DATA_TAPE_COUNT = 28
_CLEANING_SLOT_IDS = (49, 50)
_SLOTS_PER_BAY = _TOTAL_SLOTS // 2


def _slot_address(slot_id: int) -> str:
    bay = 1 if slot_id <= _SLOTS_PER_BAY else 2
    bay_slot = slot_id if bay == 1 else slot_id - _SLOTS_PER_BAY
    return f"1,{bay},{bay_slot}"


def _tape_type(slot: int) -> str:
    return "LTO-7" if slot <= 18 else "LTO-8"


def _tape_capacity_bytes(tape_type: str) -> int:
    return 6_000_000_000_000 if tape_type == "LTO-7" else 12_000_000_000_000


def _build_data_media() -> list[dict[str, Any]]:
    pools = ("Critical Projects", "General Archive", "Cold Storage", "Media Cache")
    owners = ("engineering", "media", "operations", "backup")
    retention = ("critical", "standard", "cold", "backup")
    media: list[dict[str, Any]] = []
    for slot in range(1, _DATA_TAPE_COUNT + 1):
        tape_type = _tape_type(slot)
        capacity_bytes = _tape_capacity_bytes(tape_type)
        utilization = 0.24 + ((slot * 11) % 52) / 100
        used_bytes = int(capacity_bytes * utilization)
        media.append(
            {
                # Keep existing barcode pattern to remain compatible with seeded demo data/tests.
                "barcode": f"VOL{slot:03d}L9",
                "type": tape_type,
                "partition": "partition1",
                "slotAddress": _slot_address(slot),
                "mockSlotId": slot,
                "role": "data",
                "poolName": pools[(slot - 1) % len(pools)],
                "usedBytes": used_bytes,
                "capacityBytes": capacity_bytes,
                "metadata": {
                    "dataset": f"Dataset {slot:03d}",
                    "owner": owners[(slot - 1) % len(owners)],
                    "retentionClass": retention[(slot - 1) % len(retention)],
                },
                "loadCount": 60 + (slot * 3),
                "errorCount": 1 if slot % 9 == 0 else 0,
                "lastLoaded": f"2024-01-{10 + (slot % 18):02d}T{2 + (slot % 20):02d}:{(slot * 7) % 60:02d}:00Z",
            }
        )
    return media


def _build_cleaning_media() -> list[dict[str, Any]]:
    cleaning: list[dict[str, Any]] = []
    for index, slot_id in enumerate(_CLEANING_SLOT_IDS, start=1):
        cleaning.append(
            {
                "barcode": f"CLN{index:03d}L9",
                "type": "LTO-7-CLN" if index == 1 else "LTO-8-CLN",
                "partition": None,
                "slotAddress": _slot_address(slot_id),
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


_DATA_MEDIA = _build_data_media()
_CLEANING_MEDIA = _build_cleaning_media()

_SCALAR_I3_DEFAULT: dict[str, Any] = {
    "library": {
        "name": "OpenBlade Scalar i3",
        "serial": "OB-SCALAR-I3-001",
        "serialNumber": "OB-SCALAR-I3-001",
        "model": "Scalar i3",
        "firmware": "i3-6.0.1-openblade",
        "status": "online",
        "mockLibraryId": "mock-i3-001",
    },
    "drives": [
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
    ],
    "partition": {
        "name": "partition1",
        "id": "PART-001",
        "status": "online",
        "type": "data",
        "slotCount": _TOTAL_SLOTS,
        "ieSlotCount": 2,
        "cleaningSlots": len(_CLEANING_MEDIA),
        "slotAddresses": [item["slotAddress"] for item in _DATA_MEDIA],
    },
    "media": [*_DATA_MEDIA, *_CLEANING_MEDIA],
    "ieStations": [
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
                for slot in range(1, 3)
            ],
        }
    ],
    "ltfsSections": [
        {
            "sectionNumber": section_number,
            "name": str(media["barcode"]),
            "status": "unmounted",
            "mounted": False,
            "mountPoint": f"/ltfs/{media['barcode']}",
            "fileSystem": "LTFS 2.4",
            "partitionName": "partition1",
            "readOnly": False,
            "lastMounted": None,
            "drives": [
                {"serialNumber": "DRV-001", "state": "available", "role": "primary"},
                {"serialNumber": "DRV-002", "state": "available", "role": "secondary"},
                {"serialNumber": "DRV-003", "state": "available", "role": "auxiliary"},
            ],
            "media": [
                {"barcode": str(media["barcode"]), "state": "cataloged", "type": str(media["type"])}
            ],
        }
        for section_number, media in enumerate(_DATA_MEDIA, start=1)
    ],
}


def scalar_i3_default_config() -> dict[str, Any]:
    return deepcopy(_SCALAR_I3_DEFAULT)


def scalar_i3_mock_library_barcodes(*, include_cleaning: bool = True) -> list[str]:
    media = scalar_i3_default_config()["media"]
    return [
        str(item["barcode"])
        for item in media
        if include_cleaning or str(item.get("role", "data")) != "cleaning"
    ]


def scalar_i3_data_barcodes() -> list[str]:
    return scalar_i3_mock_library_barcodes(include_cleaning=False)
