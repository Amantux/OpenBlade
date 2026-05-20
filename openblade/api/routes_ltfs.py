"""Catalog-backed LTFS browse endpoints."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from openblade.bootstrap import AppContext, get_context
from openblade.catalog.repository import CatalogBrowseEntry

router = APIRouter()


class LtfsBrowseEntryResponse(BaseModel):
    path: str
    size: int
    tape_barcode: str
    archived_at: datetime | None
    shard_count: int


def _serialize_entry(entry: CatalogBrowseEntry) -> LtfsBrowseEntryResponse:
    return LtfsBrowseEntryResponse(
        path=entry.path,
        size=entry.size,
        tape_barcode=entry.tape_barcode,
        archived_at=entry.archived_at,
        shard_count=entry.shard_count,
    )


@router.get("/browse", response_model=list[LtfsBrowseEntryResponse])
async def browse_ltfs_catalog(
    tape_barcode: str | None = Query(default=None, min_length=1),
    path_prefix: str = Query(default="/", min_length=1),
    context: AppContext = Depends(get_context),
) -> list[LtfsBrowseEntryResponse]:
    entries = context.catalog.list_ltfs_entries(
        tape_barcode=tape_barcode,
        path_prefix=path_prefix,
    )
    return [_serialize_entry(entry) for entry in entries]


@router.get("/tapes", response_model=list[str])
async def list_ltfs_catalog_tapes(
    context: AppContext = Depends(get_context),
) -> list[str]:
    return context.catalog.list_catalog_tape_barcodes()
