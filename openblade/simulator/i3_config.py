"""Canonical mock Scalar i3 configuration shared by simulator and AML state."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_DATA_MEDIA: list[dict[str, Any]] = [
    {
        "barcode": f"VOL{slot:03d}L9",
        "type": "LTO-9",
        "partition": "partition1",
        "slotAddress": f"1,1,{slot if slot <= 10 else slot + 1}",
        "mockSlotId": slot if slot <= 10 else slot + 1,
        "role": "data",
        "poolName": [
            "Critical Projects",
            "Critical Projects",
            "Cold Storage",
            "Cold Storage",
            "General Archive",
            "General Archive",
            "General Archive",
            "Media Cache",
            "Media Cache",
            "Cold Storage",
            "General Archive",
            "Critical Projects",
        ][slot - 1],
        "usedBytes": [
            1_950_000_000,
            3_250_000_000,
            5_100_000_000,
            620_000_000,
            8_450_000_000,
            2_760_000_000,
            11_200_000_000,
            420_000_000,
            1_300_000_000,
            7_800_000_000,
            4_480_000_000,
            960_000_000,
        ][slot - 1],
        "capacityBytes": 18_000_000_000,
        "metadata": {
            "dataset": [
                "Project Alpha",
                "Media Archive 2024",
                "Cold Tier Samples",
                "Backup Set A",
                "General Archive Batch 1",
                "General Archive Batch 2",
                "Operations Snapshots",
                "Cache Staging",
                "Media Cache Export",
                "Deep Archive Index",
                "Compliance Retention",
                "Project Delta",
            ][slot - 1],
            "owner": [
                "engineering",
                "media",
                "operations",
                "backup",
                "archive",
                "archive",
                "ops",
                "cache",
                "cache",
                "records",
                "compliance",
                "engineering",
            ][slot - 1],
            "retentionClass": [
                "critical",
                "critical",
                "cold",
                "backup",
                "standard",
                "standard",
                "standard",
                "cache",
                "cache",
                "cold",
                "standard",
                "critical",
            ][slot - 1],
        },
        "loadCount": [132, 118, 71, 94, 156, 88, 167, 59, 63, 143, 104, 128][slot - 1],
        "errorCount": [0, 1, 0, 0, 2, 0, 1, 0, 0, 1, 0, 0][slot - 1],
        "lastLoaded": [
            "2024-01-21T09:15:00Z",
            "2024-01-22T14:05:00Z",
            "2024-01-19T07:40:00Z",
            "2024-01-20T18:10:00Z",
            "2024-01-22T04:30:00Z",
            "2024-01-18T12:20:00Z",
            "2024-01-23T02:05:00Z",
            "2024-01-17T21:55:00Z",
            "2024-01-16T08:12:00Z",
            "2024-01-21T23:45:00Z",
            "2024-01-20T10:00:00Z",
            "2024-01-22T16:50:00Z",
        ][slot - 1],
    }
    for slot in range(1, 13)
]

_CLEANING_MEDIA: list[dict[str, Any]] = [
    {
        "barcode": f"CLN{slot:03d}L9",
        "type": "LTO-9-CLN",
        "partition": None,
        "slotAddress": f"1,2,{slot}",
        "mockSlotId": 18 + slot,
        "role": "cleaning",
        "poolName": "Cleaning Pool",
        "usedBytes": 0,
        "capacityBytes": 0,
        "metadata": {"cleaningCyclesRemaining": 40 - (slot * 6), "owner": "library"},
        "loadCount": 18 + (slot * 4),
        "errorCount": 0,
        "lastLoaded": f"2024-01-2{slot}T05:30:00Z",
    }
    for slot in range(1, 3)
]

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
            "serial": "IBM-LTO9-001",
            "model": "IBM LTO-9 HH",
            "type": "LTO-9",
            "location": "1,1,1",
            "firmware": "H9A3",
            "status": "online",
            "state": "idle",
            "loadCount": 143,
            "cleaningCount": 4,
            "lastCleaned": "2024-01-20T03:15:00Z",
        },
        {
            "id": "DRV-002",
            "serial": "IBM-LTO9-002",
            "model": "IBM LTO-9 HH",
            "type": "LTO-9",
            "location": "1,1,2",
            "firmware": "H9A3",
            "status": "online",
            "state": "idle",
            "loadCount": 97,
            "cleaningCount": 3,
            "lastCleaned": "2024-01-18T11:40:00Z",
        },
        {
            "id": "DRV-003",
            "serial": "IBM-LTO9-003",
            "model": "IBM LTO-9 HH",
            "type": "LTO-9",
            "location": "1,1,3",
            "firmware": "H9A2",
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
        "slotCount": max(item["mockSlotId"] for item in [*_DATA_MEDIA, *_CLEANING_MEDIA]),
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
            "sectionNumber": slot,
            "name": f"VOL{slot:03d}L9",
            "status": "unmounted",
            "mounted": False,
            "mountPoint": f"/ltfs/VOL{slot:03d}L9",
            "fileSystem": "LTFS 2.4",
            "partitionName": "partition1",
            "readOnly": False,
            "lastMounted": None,
            "drives": [
                {"serialNumber": "DRV-001", "state": "available", "role": "primary"},
                {"serialNumber": "DRV-002", "state": "available", "role": "secondary"},
                {"serialNumber": "DRV-003", "state": "available", "role": "auxiliary"},
            ],
            "media": [{"barcode": f"VOL{slot:03d}L9", "state": "cataloged", "type": "LTO-9"}],
        }
        for slot in range(1, 13)
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
