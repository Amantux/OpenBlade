"""NAS configuration API endpoints."""

from __future__ import annotations

import hashlib
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from openblade.api.routes_aml_auth import AmlUser, require_auth
from openblade.bootstrap import get_context
from openblade.catalog.db import get_catalog_repository
from openblade.nas.fuse_hook import FuseHook
from openblade.nas.hydration import HydrationExecutor
from openblade.nas.ingest import (
    IngestJob,
    StartIngestResponse,
    cancel_ingest_job,
    get_archive_plan,
    get_ingest_job,
    register_archive_plan,
    run_ingest_job,
    start_ingest_job,
)
from openblade.nas.path_mapping import PathMappingService
from openblade.nas.planner import ArchivePlanner
from openblade.nas.restore_planner import RestorePlan, RestorePlanner
from openblade.nas.service import NasService
from openblade.nas.sidecar import SidecarResolver
from openblade.nas.types import (
    ArchivePlan,
    ArchivePlanRequest,
    CacheDriveConfig,
    DatasetStatus,
    EffectivePolicy,
    IngestMode,
    NasFileRecord,
    NasFileState,
    NasPool,
    NasRestoreJob,
    NasShareDefinition,
    PathLookupResult,
    PathMappingBulkUpsertRequest,
    PathMappingRecord,
    PathMappingSearchRequest,
    RestoreJobStatus,
    RestorePlanRequest,
    SidecarValidationError,
    SourceStreamConfig,
    StoragePolicy,
)

router = APIRouter(prefix="/nas", tags=["NAS Config"])
_FUSE_HOOKS: dict[int, FuseHook] = {}


def get_nas_service(repo=Depends(get_catalog_repository)) -> NasService:
    return NasService(repo)


def get_path_mapping_service(repo=Depends(get_catalog_repository)) -> PathMappingService:
    return PathMappingService(repo)


def _bad_request(exc: ValueError | ValidationError | SidecarValidationError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


def _get_hydration_executor(service: NasService) -> HydrationExecutor:
    return HydrationExecutor(service, get_context().ltfs)


def _require_restore_job(service: NasService, job_id: str) -> NasRestoreJob:
    job = service.get_restore_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Restore job {job_id} not found")
    return job


def _require_dataset(service: NasService, dataset_id: str):
    dataset = service.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Dataset {dataset_id} not found")
    return dataset


def _get_fuse_hook(service: NasService) -> FuseHook:
    repository_id = id(service.repository)
    hook = _FUSE_HOOKS.get(repository_id)
    if hook is None:
        hook = FuseHook(service)
        _FUSE_HOOKS[repository_id] = hook
    else:
        hook.service = service
    return hook


def _dataset_detail_or_404(service: NasService, dataset_id: str) -> dict[str, object]:
    try:
        return service.get_dataset_detail(dataset_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Dataset {dataset_id} not found") from exc


def _manifest_payload(service: NasService, dataset_id: str) -> dict[str, object]:
    detail = _dataset_detail_or_404(service, dataset_id)
    records = service.list_file_records(dataset_id)
    return {
        "dataset_id": detail["id"],
        "policy_id": detail["policy_id"],
        "ingest_mode": detail["ingest_mode"],
        "tape_set": detail["tape_set"],
        "shard_map": detail["shard_map"],
        "files": [
            {
                "logical_path": record.relative_path,
                "size_bytes": record.size_bytes,
                "checksum_sha256": record.checksum_sha256,
                "tape_barcode": record.tape_barcode,
                "state": record.status.value,
            }
            for record in records
        ],
        "total_files": detail["file_count"],
        "total_bytes": detail["total_bytes"],
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


def _report_payload(service: NasService, dataset_id: str) -> dict[str, object]:
    detail = _dataset_detail_or_404(service, dataset_id)
    records = service.list_file_records(dataset_id)
    return {
        "dataset": detail,
        "files": [
            {
                **record.model_dump(mode="json"),
                "logical_path": record.relative_path,
            }
            for record in records
        ],
        "checksums": {record.relative_path: record.checksum_sha256 for record in records},
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


class ResolvePolicyRequest(BaseModel):
    directory: str
    share_id: str | None = None


class StartIngestRequest(BaseModel):
    plan_id: str
    dataset_name: str
    pool_id: str | None = None
    cache_drive_id: str | None = None


class CancelIngestResponse(BaseModel):
    cancelled: bool


class FuseOpenRequest(BaseModel):
    pool_id: str
    logical_path: str


@router.get("/datasets")
async def list_datasets(
    pool_id: str | None = Query(default=None),
    status_filter: DatasetStatus | None = Query(default=None, alias="status"),
    service: NasService = Depends(get_nas_service),
) -> list[dict[str, object]]:
    datasets = service.list_datasets(pool_id)
    if status_filter is not None:
        datasets = [dataset for dataset in datasets if dataset.status is status_filter]
    return [service.get_dataset_detail(dataset.id) for dataset in datasets]


@router.get("/datasets/{dataset_id}")
async def get_dataset_detail(dataset_id: str, service: NasService = Depends(get_nas_service)) -> dict[str, object]:
    return _dataset_detail_or_404(service, dataset_id)


@router.get("/datasets/{dataset_id}/files", response_model=list[NasFileRecord])
async def list_dataset_files(
    dataset_id: str,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
    service: NasService = Depends(get_nas_service),
) -> list[NasFileRecord]:
    _require_dataset(service, dataset_id)
    return service.list_file_records(dataset_id)[skip : skip + limit]


@router.get("/datasets/{dataset_id}/manifest")
async def get_dataset_manifest(dataset_id: str, service: NasService = Depends(get_nas_service)) -> dict[str, object]:
    return _manifest_payload(service, dataset_id)


@router.post("/datasets/{dataset_id}/verify")
async def verify_dataset(dataset_id: str, service: NasService = Depends(get_nas_service)) -> dict[str, object]:
    _require_dataset(service, dataset_id)
    files_verified = 0
    files_corrupt = 0
    files_updated = 0
    checksums: dict[str, str] = {}

    for record in service.list_file_records(dataset_id):
        expected = hashlib.sha256(f"{record.relative_path}:{record.tape_barcode}".encode()).hexdigest()
        files_verified += 1
        checksums[record.relative_path] = expected
        if record.checksum_sha256 in {None, expected}:
            updates = {"checksum_sha256": expected}
            if record.checksum_sha256 is None:
                files_updated += 1
            service.upsert_file_record(record.model_copy(update=updates))
            continue
        files_corrupt += 1
        service.upsert_file_record(record.model_copy(update={"status": NasFileState.CORRUPT}))

    return {
        "dataset_id": dataset_id,
        "files_verified": files_verified,
        "files_corrupt": files_corrupt,
        "files_updated": files_updated,
        "checksums": checksums,
    }


@router.post("/datasets/{dataset_id}/export")
async def export_dataset(dataset_id: str, service: NasService = Depends(get_nas_service)) -> dict[str, object]:
    dataset = _require_dataset(service, dataset_id)
    service.upsert_dataset(dataset.model_copy(update={"status": DatasetStatus.EXPORTED}))
    for record in service.list_file_records(dataset_id):
        service.upsert_file_record(record.model_copy(update={"status": NasFileState.EXPORTED}))
    return _dataset_detail_or_404(service, dataset_id)


@router.get("/datasets/{dataset_id}/report")
async def get_dataset_report(dataset_id: str, service: NasService = Depends(get_nas_service)) -> dict[str, object]:
    return _report_payload(service, dataset_id)


@router.post("/fuse/open")
async def open_virtual_file(
    request: FuseOpenRequest,
    service: NasService = Depends(get_nas_service),
) -> dict[str, object]:
    return _get_fuse_hook(service).on_file_open(request.pool_id, request.logical_path)


@router.get("/fuse/log")
async def get_fuse_log(service: NasService = Depends(get_nas_service)) -> list[dict[str, object]]:
    return _get_fuse_hook(service).get_access_log()


@router.post("/path-mappings", response_model=PathMappingRecord)
async def upsert_path_mapping(
    record: PathMappingRecord,
    service: PathMappingService = Depends(get_path_mapping_service),
    _: AmlUser = Depends(require_auth),
) -> PathMappingRecord:
    try:
        return service.record_file(record)
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.get("/path-mappings/lookup", response_model=PathLookupResult)
async def lookup_path_mapping(
    path: str = Query(...),
    pool_id: str = Query(default=""),
    service: PathMappingService = Depends(get_path_mapping_service),
    _: AmlUser = Depends(require_auth),
) -> PathLookupResult:
    return service.lookup(path, pool_id)


@router.post("/path-mappings/search", response_model=list[PathMappingRecord])
async def search_path_mappings(
    request: PathMappingSearchRequest,
    service: PathMappingService = Depends(get_path_mapping_service),
    _: AmlUser = Depends(require_auth),
) -> list[PathMappingRecord]:
    return service.search(request)


@router.post("/path-mappings/bulk")
async def bulk_upsert_path_mappings(
    request: PathMappingBulkUpsertRequest,
    service: PathMappingService = Depends(get_path_mapping_service),
    _: AmlUser = Depends(require_auth),
) -> dict[str, int]:
    try:
        return {"upserted": service.bulk_record_files(request)}
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.delete("/path-mappings")
async def delete_path_mapping(
    path: str = Query(...),
    pool_id: str = Query(default=""),
    service: PathMappingService = Depends(get_path_mapping_service),
    _: AmlUser = Depends(require_auth),
) -> dict[str, bool]:
    deleted = service.remove(path, pool_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="path mapping not found")
    return {"deleted": True}


@router.get("/path-mappings/stats")
async def get_path_mapping_stats(
    pool_id: str = Query(default=""),
    dataset_id: str = Query(default=""),
    service: PathMappingService = Depends(get_path_mapping_service),
    _: AmlUser = Depends(require_auth),
) -> dict[str, object]:
    return service.get_stats(pool_id=pool_id, dataset_id=dataset_id)


@router.get("/policies", response_model=list[StoragePolicy])
async def list_policies(service: NasService = Depends(get_nas_service)) -> list[StoragePolicy]:
    return service.get_policies()


@router.post("/policies", response_model=StoragePolicy)
async def create_or_update_policy(
    policy: StoragePolicy,
    service: NasService = Depends(get_nas_service),
) -> JSONResponse:
    created = service.get_policy(policy.id) is None
    try:
        saved = service.upsert_policy(policy)
    except ValueError as exc:
        raise _bad_request(exc) from exc
    return JSONResponse(
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        content=saved.model_dump(mode="json"),
    )


@router.get("/policies/{policy_id}", response_model=StoragePolicy)
async def get_policy(policy_id: str, service: NasService = Depends(get_nas_service)) -> StoragePolicy:
    policy = service.get_policy(policy_id)
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Policy {policy_id} not found")
    return policy


@router.delete("/policies/{policy_id}")
async def delete_policy(policy_id: str, service: NasService = Depends(get_nas_service)) -> dict[str, bool]:
    try:
        deleted = service.delete_policy(policy_id)
    except ValueError as exc:
        raise _bad_request(exc) from exc
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Policy {policy_id} not found")
    return {"deleted": True}


@router.get("/cache-drives", response_model=list[CacheDriveConfig])
async def list_cache_drives(
    service: NasService = Depends(get_nas_service),
) -> list[CacheDriveConfig]:
    return service.get_cache_drives()


@router.post("/cache-drives", response_model=CacheDriveConfig)
async def create_or_update_cache_drive(
    drive: CacheDriveConfig,
    service: NasService = Depends(get_nas_service),
) -> JSONResponse:
    created = service.get_cache_drive(drive.id) is None
    try:
        saved = service.upsert_cache_drive(drive)
    except ValueError as exc:
        raise _bad_request(exc) from exc
    return JSONResponse(
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        content=saved.model_dump(mode="json"),
    )


@router.get("/cache-drives/{drive_id}", response_model=CacheDriveConfig)
async def get_cache_drive(
    drive_id: str,
    service: NasService = Depends(get_nas_service),
) -> CacheDriveConfig:
    drive = service.get_cache_drive(drive_id)
    if drive is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Cache drive {drive_id} not found")
    return drive


@router.delete("/cache-drives/{drive_id}")
async def delete_cache_drive(
    drive_id: str,
    service: NasService = Depends(get_nas_service),
) -> dict[str, bool]:
    deleted = service.delete_cache_drive(drive_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Cache drive {drive_id} not found")
    return {"deleted": True}


@router.get("/source-stream", response_model=SourceStreamConfig)
async def get_source_stream_config(
    service: NasService = Depends(get_nas_service),
) -> SourceStreamConfig:
    return service.get_source_stream_config()


@router.put("/source-stream", response_model=SourceStreamConfig)
async def update_source_stream_config(
    config: SourceStreamConfig,
    service: NasService = Depends(get_nas_service),
) -> SourceStreamConfig:
    try:
        return service.update_source_stream_config(config)
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.delete("/source-stream")
async def delete_source_stream_config(
    service: NasService = Depends(get_nas_service),
) -> dict[str, bool]:
    deleted = service.delete_source_stream_config()
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source stream config not found")
    return {"deleted": True}


@router.get("/shares", response_model=list[NasShareDefinition])
async def list_shares(service: NasService = Depends(get_nas_service)) -> list[NasShareDefinition]:
    return service.get_nas_shares()


@router.post("/shares", response_model=NasShareDefinition)
async def create_or_update_share(
    share: NasShareDefinition,
    service: NasService = Depends(get_nas_service),
) -> JSONResponse:
    created = service.get_share(share.path) is None
    try:
        saved = service.upsert_share(share)
    except ValueError as exc:
        raise _bad_request(exc) from exc
    return JSONResponse(
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        content=saved.model_dump(mode="json"),
    )


@router.get("/shares/{share_id:path}", response_model=NasShareDefinition)
async def get_share(
    share_id: str,
    service: NasService = Depends(get_nas_service),
) -> NasShareDefinition:
    share = service.get_share("/" + share_id.lstrip("/"))
    if share is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Share /{share_id.lstrip('/')} not found")
    return share


@router.delete("/shares/{share_id:path}")
async def delete_share(
    share_id: str,
    service: NasService = Depends(get_nas_service),
) -> dict[str, bool]:
    normalized_share_id = "/" + share_id.lstrip("/")
    deleted = service.delete_share(normalized_share_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Share {normalized_share_id} not found")
    return {"deleted": True}


@router.get("/pools", response_model=list[NasPool])
async def list_pools(service: NasService = Depends(get_nas_service)) -> list[NasPool]:
    return service.list_pools()


@router.post("/pools", response_model=NasPool)
async def create_pool(pool: NasPool, service: NasService = Depends(get_nas_service)) -> JSONResponse:
    created = service.get_pool(pool.id) is None
    try:
        saved = service.upsert_pool(pool)
    except ValueError as exc:
        raise _bad_request(exc) from exc
    return JSONResponse(
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        content=saved.model_dump(mode="json"),
    )


@router.get("/pools/{pool_id}", response_model=NasPool)
async def get_pool(pool_id: str, service: NasService = Depends(get_nas_service)) -> NasPool:
    pool = service.get_pool(pool_id)
    if pool is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Pool {pool_id} not found")
    return pool


@router.put("/pools/{pool_id}", response_model=NasPool)
async def update_pool(
    pool_id: str,
    pool: NasPool,
    service: NasService = Depends(get_nas_service),
) -> NasPool:
    if service.get_pool(pool_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Pool {pool_id} not found")
    try:
        return service.upsert_pool(pool.model_copy(update={"id": pool_id}))
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.delete("/pools/{pool_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pool(pool_id: str, service: NasService = Depends(get_nas_service)) -> Response:
    if not service.delete_pool(pool_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Pool {pool_id} not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/pools/{pool_id}/browse")
async def browse_pool(
    pool_id: str,
    path: str = Query(default=""),
    service: NasService = Depends(get_nas_service),
) -> dict[str, object]:
    try:
        return service.browse_pool(pool_id, path)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Pool {pool_id} not found") from exc


@router.get("/pools/{pool_id}/files/{file_path:path}", response_model=NasFileRecord)
async def get_pool_file_detail(
    pool_id: str,
    file_path: str,
    service: NasService = Depends(get_nas_service),
) -> NasFileRecord:
    try:
        return service.get_pool_file_detail(pool_id, file_path)
    except KeyError as exc:
        detail = exc.args[0] if exc.args else "file not found"
        if detail == "pool not found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Pool {pool_id} not found") from exc
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found") from exc


@router.post("/restore-plan", response_model=RestorePlan)
async def restore_plan(
    request: RestorePlanRequest,
    service: NasService = Depends(get_nas_service),
) -> RestorePlan:
    if request.pool_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="pool_id is required")
    if service.get_pool(request.pool_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Pool {request.pool_id} not found")
    return RestorePlanner(service).plan(request)


@router.post("/pools/{pool_id}/request-restore", response_model=NasRestoreJob)
async def request_restore(
    pool_id: str,
    request: RestorePlanRequest,
    service: NasService = Depends(get_nas_service),
) -> JSONResponse:
    if service.get_pool(pool_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Pool {pool_id} not found")

    plan = RestorePlanner(service).plan(request.model_copy(update={"pool_id": pool_id}))
    job = NasRestoreJob(
        id=str(uuid4()),
        pool_id=pool_id,
        paths=request.paths,
        destination=request.destination,
        priority=request.priority,
        allow_parallel=request.allow_parallel,
        max_drives=request.max_drives,
        status=RestoreJobStatus.QUEUED,
        required_tapes=plan.required_tapes,
        missing_tapes=plan.missing_tapes,
        exported_tapes=plan.exported_tapes,
        tape_load_order=plan.tape_load_order,
        parallel_restore_groups={
            f"group-{index + 1}": group for index, group in enumerate(plan.parallel_restore_groups)
        },
        estimated_bytes=plan.estimated_bytes,
        unavailable_files=plan.unavailable_files,
        warnings=plan.warnings,
    )
    saved = service.upsert_restore_job(job)
    return JSONResponse(status_code=status.HTTP_201_CREATED, content=saved.model_dump(mode="json"))


@router.get("/restore-jobs", response_model=list[NasRestoreJob])
async def list_restore_jobs(service: NasService = Depends(get_nas_service)) -> list[NasRestoreJob]:
    return service.list_restore_jobs()


@router.get("/restore-jobs/{job_id}", response_model=NasRestoreJob)
async def get_restore_job(
    job_id: str,
    service: NasService = Depends(get_nas_service),
) -> NasRestoreJob:
    job = service.get_restore_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Restore job {job_id} not found")
    return job


@router.delete("/restore-jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_restore_job(
    job_id: str,
    service: NasService = Depends(get_nas_service),
) -> Response:
    if not service.update_restore_job_status(job_id, RestoreJobStatus.CANCELLED.value):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Restore job {job_id} not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/restore-jobs/{job_id}/run", response_model=NasRestoreJob, status_code=status.HTTP_202_ACCEPTED)
async def run_restore_job_endpoint(
    job_id: str,
    service: NasService = Depends(get_nas_service),
) -> NasRestoreJob:
    _require_restore_job(service, job_id)
    try:
        return _get_hydration_executor(service).run(job_id)
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.post("/restore-jobs/{job_id}/cancel", response_model=NasRestoreJob)
async def cancel_restore_job_endpoint(
    job_id: str,
    service: NasService = Depends(get_nas_service),
) -> NasRestoreJob:
    _require_restore_job(service, job_id)
    try:
        return _get_hydration_executor(service).cancel(job_id)
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.post("/restore-jobs/{job_id}/pause", response_model=NasRestoreJob)
async def pause_restore_job_endpoint(
    job_id: str,
    service: NasService = Depends(get_nas_service),
) -> NasRestoreJob:
    _require_restore_job(service, job_id)
    try:
        return _get_hydration_executor(service).pause(job_id)
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.post("/restore-jobs/{job_id}/resume", response_model=NasRestoreJob)
async def resume_restore_job_endpoint(
    job_id: str,
    service: NasService = Depends(get_nas_service),
) -> NasRestoreJob:
    _require_restore_job(service, job_id)
    try:
        return _get_hydration_executor(service).resume(job_id)
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.post("/restore-jobs/{job_id}/retry", response_model=NasRestoreJob)
async def retry_restore_job_endpoint(
    job_id: str,
    service: NasService = Depends(get_nas_service),
) -> NasRestoreJob:
    _require_restore_job(service, job_id)
    try:
        return _get_hydration_executor(service).retry(job_id)
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.post("/resolve-policy", response_model=EffectivePolicy)
async def resolve_policy(
    request: ResolvePolicyRequest,
    service: NasService = Depends(get_nas_service),
) -> EffectivePolicy:
    normalized_share_id = None if request.share_id is None else "/" + request.share_id.lstrip("/")
    share_default_policy = None

    if normalized_share_id is not None:
        share = service.get_share(normalized_share_id)
        if share is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Share {normalized_share_id} not found",
            )
        if share.default_policy_id is not None:
            share_default_policy = service.get_policy(share.default_policy_id)

    resolver = SidecarResolver(nas_service=service)
    system_default_policy = service.get_policy("balanced")

    try:
        return resolver.resolve_effective_policy(
            directory=request.directory,
            share_default_policy=share_default_policy,
            system_default_policy=system_default_policy,
        )
    except (ValidationError, SidecarValidationError, ValueError) as exc:
        raise _bad_request(exc) from exc


@router.post("/archive-plan", response_model=ArchivePlan)
async def archive_plan(
    request: ArchivePlanRequest,
    service: NasService = Depends(get_nas_service),
) -> ArchivePlan:
    policy = None
    if request.policy_id is not None:
        policy = service.get_policy(request.policy_id)
        if policy is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Policy {request.policy_id} not found",
            )

        updates: dict[str, object] = {"policy_type": policy.policy_type}
        if "ingest_mode" not in request.model_fields_set:
            updates["ingest_mode"] = policy.default_ingest_mode
        if "copies" not in request.model_fields_set:
            updates["copies"] = policy.copies_required
        if "verify_before_archive" not in request.model_fields_set:
            updates["verify_before_archive"] = policy.verify_before_archive
        if "verify_after_archive" not in request.model_fields_set:
            updates["verify_after_archive"] = policy.verify_after_archive
        if "shard_strategy" not in request.model_fields_set and policy.shard_strategy is not None:
            updates["shard_strategy"] = policy.shard_strategy
        if "max_parallelism" not in request.model_fields_set:
            updates["max_parallelism"] = policy.max_parallelism
        request = request.model_copy(update=updates)

    plan = ArchivePlanner().plan(request)
    if policy is not None:
        plan.policy_name = policy.name
        plan.policy_type = policy.policy_type
    return register_archive_plan(plan)


@router.post("/ingest/start", response_model=StartIngestResponse)
async def start_ingest(
    request: StartIngestRequest,
    background_tasks: BackgroundTasks,
    service: NasService = Depends(get_nas_service),
) -> StartIngestResponse:
    plan = get_archive_plan(request.plan_id)
    if plan is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Plan {request.plan_id} not found")
    if not plan.is_safe_to_enqueue:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Archive plan is not safe to enqueue")
    if plan.ingest_mode is IngestMode.CACHE_DRIVE:
        if request.cache_drive_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="cache_drive_id is required for cache-drive ingest",
            )
        if service.get_cache_drive(request.cache_drive_id) is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cache drive {request.cache_drive_id} not found",
            )
    context = get_context()
    job = start_ingest_job(
        plan=plan,
        dataset_name=request.dataset_name,
        pool_id=request.pool_id,
        nas_service=service,
        cache_drive_id=request.cache_drive_id,
    )
    background_tasks.add_task(
        run_ingest_job,
        job.job_id,
        nas_service=service,
        library=context.library,
        ltfs=context.ltfs,
        cache_drive_id=request.cache_drive_id,
    )
    return StartIngestResponse(job_id=job.job_id, dataset_id=job.dataset_id, status="running")


@router.get("/ingest/{job_id}", response_model=IngestJob)
async def ingest_status(job_id: str) -> IngestJob:
    job = get_ingest_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Ingest job {job_id} not found")
    return job


@router.post("/ingest/{job_id}/cancel", response_model=CancelIngestResponse)
async def cancel_ingest(job_id: str) -> CancelIngestResponse:
    if not cancel_ingest_job(job_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Ingest job {job_id} not found")
    return CancelIngestResponse(cancelled=True)
