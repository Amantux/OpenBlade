"""Dashboard aggregation endpoints."""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from openblade.bootstrap import AppContext, get_context

router = APIRouter()


class StorageSummary(BaseModel):
    totalFiles: int
    totalBytes: int
    volumeGroupCount: int
    totalAssignedTapes: int
    totalCatalogTapes: int
    totalTapeCapacityBytes: int
    availableTapeCapacityBytes: int
    utilizationPercent: int


class VolumeGroupStorage(BaseModel):
    id: str
    name: str
    assignedTapes: int
    fileCount: int
    storedBytes: int


class DashboardStatsResponse(BaseModel):
    storage: StorageSummary
    volumeGroups: list[VolumeGroupStorage]


@router.get("/stats", response_model=DashboardStatsResponse)
async def get_dashboard_stats(
    context: AppContext = Depends(get_context),
) -> DashboardStatsResponse:
    groups = context.catalog.list_volume_groups()
    files = context.catalog.list_file_records("/")
    assigned_tapes = [cartridge for cartridge in context.catalog.list_cartridges() if cartridge.volume_group_id is not None]
    catalog_tapes = context.catalog.list_catalog_tape_barcodes()

    file_count_by_group: dict[str, int] = defaultdict(int)
    bytes_by_group: dict[str, int] = defaultdict(int)
    total_bytes = 0
    for record in files:
        total_bytes += record.size_bytes
        file_count_by_group[record.volume_group_id] += 1
        bytes_by_group[record.volume_group_id] += record.size_bytes

    total_tape_capacity_bytes = sum(cartridge.capacity_bytes for cartridge in assigned_tapes)
    available_tape_capacity_bytes = max(total_tape_capacity_bytes - total_bytes, 0)
    utilization_percent = round((total_bytes / total_tape_capacity_bytes) * 100) if total_tape_capacity_bytes else 0

    return DashboardStatsResponse(
        storage=StorageSummary(
            totalFiles=len(files),
            totalBytes=total_bytes,
            volumeGroupCount=len(groups),
            totalAssignedTapes=len({cartridge.barcode for cartridge in assigned_tapes}),
            totalCatalogTapes=len(catalog_tapes),
            totalTapeCapacityBytes=total_tape_capacity_bytes,
            availableTapeCapacityBytes=available_tape_capacity_bytes,
            utilizationPercent=utilization_percent,
        ),
        volumeGroups=[
            VolumeGroupStorage(
                id=group.id,
                name=group.name,
                assignedTapes=len(group.barcodes),
                fileCount=file_count_by_group.get(group.id, 0),
                storedBytes=bytes_by_group.get(group.id, 0),
            )
            for group in groups
        ],
    )
