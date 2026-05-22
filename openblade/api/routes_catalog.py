"""Catalog API endpoints."""

from __future__ import annotations

import re
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel

from openblade.bootstrap import AppContext, get_context, seed_demo_environment
from openblade.catalog.models import FileInstance, FileRecord
from openblade.domain.errors import FileNotFoundError

router = APIRouter()
_SHARD_SUFFIX_PATTERN = re.compile(r"\.shard(\d+)$")


class CatalogFileSummaryResponse(BaseModel):
    id: str
    source_path: str
    size_bytes: int
    checksum: str
    created_at: datetime
    instance_count: int
    shard_count: int
    shard_index: int | None
    block_size: int | None
    shard_profile: str | None
    parent_id: str | None


class FileInstanceResponse(BaseModel):
    id: str
    barcode: str
    tape_path: str
    shard_index: int
    created_at: datetime


class CatalogFileDetailResponse(CatalogFileSummaryResponse):
    instances: list[FileInstanceResponse]


class CatalogListResponse(BaseModel):
    files: list[CatalogFileSummaryResponse]
    total: int


class CatalogSeedDemoResponse(BaseModel):
    status: str
    datasets: int
    files: int


def _get_shard_index(instance: FileInstance) -> int:
    match = _SHARD_SUFFIX_PATTERN.search(instance.tape_path)
    if match is not None:
        return int(match.group(1))
    return 0


def _serialize_instance(instance: FileInstance) -> FileInstanceResponse:
    return FileInstanceResponse(
        id=instance.id,
        barcode=instance.barcode,
        tape_path=instance.tape_path,
        shard_index=_get_shard_index(instance),
        created_at=instance.created_at,
    )


def _serialize_record(record: FileRecord) -> CatalogFileSummaryResponse:
    unique_barcodes = {instance.barcode for instance in record.instances}
    return CatalogFileSummaryResponse(
        id=record.id,
        source_path=record.path,
        size_bytes=record.size_bytes,
        checksum=record.checksum_sha256,
        created_at=record.created_at,
        instance_count=len(unique_barcodes),
        shard_count=record.shard_count or len(record.instances) or 1,
        shard_index=record.shard_index,
        block_size=record.block_size,
        shard_profile=record.shard_profile,
        parent_id=record.parent_id,
    )


def _get_record_or_404(context: AppContext, file_id: str) -> FileRecord:
    record = context.catalog.get_file_record_by_id(file_id)
    if record is None:
        raise FileNotFoundError(f"Catalog file {file_id} not found")
    return record


@router.get("/", response_model=CatalogListResponse)
async def list_catalog_files(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    search: str | None = Query(default=None, min_length=1),
    context: AppContext = Depends(get_context),
) -> CatalogListResponse:
    files, total = context.catalog.list_catalog_files(limit=limit, offset=offset, search=search)
    return CatalogListResponse(files=[_serialize_record(record) for record in files], total=total)


@router.get("/{file_id}", response_model=CatalogFileDetailResponse)
async def get_catalog_file(
    file_id: str,
    context: AppContext = Depends(get_context),
) -> CatalogFileDetailResponse:
    record = _get_record_or_404(context, file_id)
    summary = _serialize_record(record)
    return CatalogFileDetailResponse(**summary.model_dump(), instances=[_serialize_instance(instance) for instance in record.instances])


@router.get("/{file_id}/instances", response_model=list[FileInstanceResponse])
async def list_catalog_file_instances(
    file_id: str,
    context: AppContext = Depends(get_context),
) -> list[FileInstanceResponse]:
    record = _get_record_or_404(context, file_id)
    return [_serialize_instance(instance) for instance in record.instances]


@router.get("/{file_id}/shards", response_model=list[CatalogFileDetailResponse])
async def list_catalog_file_shards(
    file_id: str,
    context: AppContext = Depends(get_context),
) -> list[CatalogFileDetailResponse]:
    _get_record_or_404(context, file_id)
    shard_records = context.catalog.list_shard_records(file_id)
    responses: list[CatalogFileDetailResponse] = []
    for record in shard_records:
        summary = _serialize_record(record)
        responses.append(
            CatalogFileDetailResponse(
                **summary.model_dump(),
                instances=[_serialize_instance(instance) for instance in record.instances],
            )
        )
    return responses


@router.get("/seed-demo", response_model=CatalogSeedDemoResponse)
async def seed_demo_catalog(
    context: AppContext = Depends(get_context),
) -> CatalogSeedDemoResponse:
    seed_demo_environment(context.catalog)
    files, total = context.catalog.list_catalog_files(limit=500, offset=0)
    return CatalogSeedDemoResponse(
        status="ok",
        datasets=len(context.catalog.list_nas_datasets()),
        files=total or len(files),
    )


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_catalog_file(
    file_id: str,
    context: AppContext = Depends(get_context),
) -> Response:
    context.catalog.delete_file_record(file_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
