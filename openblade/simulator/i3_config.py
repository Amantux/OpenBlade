"""Canonical mock Scalar i3 configuration shared by simulator and AML state."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_SCALAR_I3_DEFAULT: dict[str, Any] = {
    "library": {
        "name": "OpenBlade Scalar i3",
        "serial": "MOCK-I3-001",
        "model": "Scalar i3",
        "mockLibraryId": "mock-i3-001",
    },
    "drives": [
        {
            "id": "DRV-001",
            "serial": "IBM-LTO9-001",
            "model": "IBM LTO-9 HH",
            "type": "LTO-9",
            "location": "1,1,1",
        },
        {
            "id": "DRV-002",
            "serial": "IBM-LTO9-002",
            "model": "IBM LTO-9 HH",
            "type": "LTO-9",
            "location": "1,1,2",
        },
    ],
    "partition": {
        "name": "partition1",
        "id": "PART-001",
        "status": "online",
        "type": "data",
        "slotCount": 20,
        "ieSlotCount": 2,
        "cleaningSlots": 2,
    },
    "media": [
        *[
            {
                "barcode": f"VOL{slot:03d}L9",
                "type": "LTO-9",
                "partition": "partition1",
                "slotAddress": f"1,1,{slot}",
                "mockSlotId": slot,
                "role": "data",
            }
            for slot in range(1, 11)
        ],
        *[
            {
                "barcode": f"CLN{slot:03d}L9",
                "type": "LTO-9-CLN",
                "partition": "partition1",
                "slotAddress": f"1,2,{slot}",
                "mockSlotId": 18 + slot,
                "role": "cleaning",
            }
            for slot in range(1, 3)
        ],
    ],
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
            ],
            "media": [{"barcode": f"VOL{slot:03d}L9", "state": "cataloged", "type": "LTO-9"}],
        }
        for slot in range(1, 11)
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
