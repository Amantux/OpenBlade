from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import AppContext, create_context, reset_context
from openblade.config import OpenBladeConfig
from openblade.nas.service import NasService
from openblade.nas.types import (
    HydrationRequest,
    NasDataset,
    NasFileRecord,
    NasFileState,
    PathMappingRecord,
    VirtualFileStatus,
)
from openblade.nas.virtual_fs import VirtualFilesystem
from openblade.sftp.mock_server import MockSftpSession, OfflineFileError

client = TestClient(app)


@pytest.fixture()
def context(tmp_path: Path) -> AppContext:
    runtime = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'virtual-fs.db'}"))
    reset_context(runtime)
    return runtime


@pytest.fixture()
def catalog_service(context: AppContext) -> NasService:
    return NasService(context.catalog)


@pytest.fixture()
def filesystem(context: AppContext) -> VirtualFilesystem:
    return VirtualFilesystem(context.catalog)


def seed_dataset(
    service: NasService,
    *,
    pool_name: str = "critical",
    dataset_id: str = "2026",
) -> NasDataset:
    return service.upsert_dataset(
        NasDataset(
            id=dataset_id,
            pool_id=pool_name,
            volume_group_id=pool_name,
            name=f"dataset-{dataset_id}",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-02T00:00:00Z",
        )
    )


def seed_file(
    service: NasService,
    *,
    dataset_id: str,
    pool_name: str = "critical",
    relative_path: str = "file.mov",
    status: NasFileState = NasFileState.ONLINE_CACHED,
    tape_barcode: str | None = "TAPE001",
    checksum_sha256: str = "abc123",
    size_bytes: int = 128,
    mtime: str = "2026-01-03T04:05:06Z",
) -> NasFileRecord:
    return service.upsert_file_record(
        NasFileRecord(
            dataset_id=dataset_id,
            pool_id=pool_name,
            relative_path=relative_path,
            size_bytes=size_bytes,
            mtime=mtime,
            checksum_sha256=checksum_sha256,
            tape_barcode=tape_barcode,
            status=status,
        )
    )


def seed_mapping(
    context: AppContext,
    *,
    path: str,
    pool_name: str,
    dataset_id: str,
    file_record_id: str,
    barcode: str = "TAPE001",
    state: NasFileState = NasFileState.OFFLINE_ON_TAPE,
) -> None:
    context.catalog.upsert_path_mapping(
        PathMappingRecord(
            logical_path=path,
            pool_id=pool_name,
            dataset_id=dataset_id,
            primary_barcode=barcode,
            all_barcodes=[barcode],
            file_record_id=file_record_id,
            file_state=state,
            size=128,
            checksum="abc123",
        )
    )


def _login() -> None:
    response = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert response.status_code == 200


def test_list_root_returns_pools(catalog_service: NasService, filesystem: VirtualFilesystem) -> None:
    seed_dataset(catalog_service, pool_name="critical", dataset_id="2026")
    seed_dataset(catalog_service, pool_name="archive", dataset_id="2027")

    listing = filesystem.list_directory("/")

    assert [entry.name for entry in listing.entries] == ["archive", "critical"]
    assert all(entry.is_directory for entry in listing.entries)


def test_list_pools_returns_datasets(catalog_service: NasService, filesystem: VirtualFilesystem) -> None:
    seed_dataset(catalog_service, pool_name="critical", dataset_id="2026")
    seed_dataset(catalog_service, pool_name="critical", dataset_id="2027")

    listing = filesystem.list_directory("/pools/critical")

    assert [entry.name for entry in listing.entries] == ["2026", "2027"]
    assert all(entry.is_directory for entry in listing.entries)


def test_list_dataset_returns_files(catalog_service: NasService, filesystem: VirtualFilesystem) -> None:
    seed_dataset(catalog_service)
    seed_file(catalog_service, dataset_id="2026", relative_path="alpha.mov")
    seed_file(catalog_service, dataset_id="2026", relative_path="beta.mov")

    listing = filesystem.list_directory("/pools/critical/2026")

    assert [entry.name for entry in listing.entries] == ["alpha.mov", "beta.mov"]
    assert [entry.size_bytes for entry in listing.entries] == [128, 128]


def test_list_unknown_path_returns_empty(filesystem: VirtualFilesystem) -> None:
    listing = filesystem.list_directory("/pools/missing")

    assert listing.path == "/pools/missing"
    assert listing.entries == []
    assert listing.total_entries == 0


def test_stat_file_returns_entry(context: AppContext, catalog_service: NasService, filesystem: VirtualFilesystem) -> None:
    seed_dataset(catalog_service)
    record = seed_file(catalog_service, dataset_id="2026", relative_path="alpha.mov")
    seed_mapping(
        context,
        path="/pools/critical/2026/alpha.mov",
        pool_name="critical",
        dataset_id="2026",
        file_record_id=record.id,
        state=NasFileState.ONLINE_CACHED,
    )

    entry = filesystem.stat_file("/pools/critical/2026/alpha.mov")

    assert entry.name == "alpha.mov"
    assert entry.size_bytes == 128
    assert entry.status is VirtualFileStatus.ONLINE_CACHED


def test_stat_missing_file_raises(filesystem: VirtualFilesystem) -> None:
    with pytest.raises(FileNotFoundError):
        filesystem.stat_file("/pools/critical/2026/missing.mov")


def test_offline_file_has_offline_status(
    context: AppContext,
    catalog_service: NasService,
    filesystem: VirtualFilesystem,
) -> None:
    seed_dataset(catalog_service)
    record = seed_file(
        catalog_service,
        dataset_id="2026",
        relative_path="offline.mov",
        status=NasFileState.OFFLINE_ON_TAPE,
    )
    seed_mapping(
        context,
        path="/pools/critical/2026/offline.mov",
        pool_name="critical",
        dataset_id="2026",
        file_record_id=record.id,
        state=NasFileState.OFFLINE_ON_TAPE,
    )

    entry = filesystem.stat_file("/pools/critical/2026/offline.mov")

    assert entry.status is VirtualFileStatus.OFFLINE_ON_TAPE
    assert entry.tape_barcode == "TAPE001"


def test_request_hydration_returns_queued_job(
    context: AppContext,
    catalog_service: NasService,
    filesystem: VirtualFilesystem,
) -> None:
    seed_dataset(catalog_service)
    record = seed_file(
        catalog_service,
        dataset_id="2026",
        relative_path="offline.mov",
        status=NasFileState.OFFLINE_ON_TAPE,
    )
    seed_mapping(
        context,
        path="/pools/critical/2026/offline.mov",
        pool_name="critical",
        dataset_id="2026",
        file_record_id=record.id,
    )

    job = filesystem.request_hydration(HydrationRequest(paths=["/pools/critical/2026/offline.mov"]))

    assert job.job_id
    assert job.status == "queued"
    assert job.total_files == 1
    assert filesystem.stat_file("/pools/critical/2026/offline.mov").status is VirtualFileStatus.HYDRATING


def test_request_hydration_assigns_required_tapes(
    context: AppContext,
    catalog_service: NasService,
    filesystem: VirtualFilesystem,
) -> None:
    seed_dataset(catalog_service)
    record = seed_file(
        catalog_service,
        dataset_id="2026",
        relative_path="offline.mov",
        status=NasFileState.OFFLINE_ON_TAPE,
        tape_barcode="TAPE777",
    )
    seed_mapping(
        context,
        path="/pools/critical/2026/offline.mov",
        pool_name="critical",
        dataset_id="2026",
        file_record_id=record.id,
        barcode="TAPE777",
    )

    job = filesystem.request_hydration(HydrationRequest(paths=["/pools/critical/2026/offline.mov"]))

    assert job.required_tapes == ["TAPE777"]


def test_get_hydration_job_found(
    context: AppContext,
    catalog_service: NasService,
    filesystem: VirtualFilesystem,
) -> None:
    seed_dataset(catalog_service)
    record = seed_file(
        catalog_service,
        dataset_id="2026",
        relative_path="offline.mov",
        status=NasFileState.OFFLINE_ON_TAPE,
    )
    seed_mapping(
        context,
        path="/pools/critical/2026/offline.mov",
        pool_name="critical",
        dataset_id="2026",
        file_record_id=record.id,
    )
    job = filesystem.request_hydration(HydrationRequest(paths=["/pools/critical/2026/offline.mov"]))

    fetched = filesystem.get_hydration_job(job.job_id)

    assert fetched.job_id == job.job_id
    assert fetched.status == "queued"


def test_get_hydration_job_not_found_raises(filesystem: VirtualFilesystem) -> None:
    with pytest.raises(KeyError):
        filesystem.get_hydration_job("missing-job")


def test_cancel_queued_job(
    context: AppContext,
    catalog_service: NasService,
    filesystem: VirtualFilesystem,
) -> None:
    seed_dataset(catalog_service)
    record = seed_file(
        catalog_service,
        dataset_id="2026",
        relative_path="offline.mov",
        status=NasFileState.OFFLINE_ON_TAPE,
    )
    seed_mapping(
        context,
        path="/pools/critical/2026/offline.mov",
        pool_name="critical",
        dataset_id="2026",
        file_record_id=record.id,
    )
    job = filesystem.request_hydration(HydrationRequest(paths=["/pools/critical/2026/offline.mov"]))

    cancelled = filesystem.cancel_hydration_job(job.job_id)

    assert cancelled.status == "cancelled"


def test_cancel_completed_job_raises(filesystem: VirtualFilesystem) -> None:
    filesystem._jobs["job-1"] = filesystem.request_hydration(  # type: ignore[attr-defined]
        HydrationRequest(paths=[])
    ).model_copy(update={"job_id": "job-1", "status": "completed"})

    with pytest.raises(ValueError):
        filesystem.cancel_hydration_job("job-1")


def test_list_hydration_jobs_returns_all(
    context: AppContext,
    catalog_service: NasService,
    filesystem: VirtualFilesystem,
) -> None:
    seed_dataset(catalog_service)
    first = seed_file(
        catalog_service,
        dataset_id="2026",
        relative_path="first.mov",
        status=NasFileState.OFFLINE_ON_TAPE,
    )
    second = seed_file(
        catalog_service,
        dataset_id="2026",
        relative_path="second.mov",
        status=NasFileState.OFFLINE_ON_TAPE,
        tape_barcode="TAPE002",
    )
    seed_mapping(
        context,
        path="/pools/critical/2026/first.mov",
        pool_name="critical",
        dataset_id="2026",
        file_record_id=first.id,
        barcode="TAPE001",
    )
    seed_mapping(
        context,
        path="/pools/critical/2026/second.mov",
        pool_name="critical",
        dataset_id="2026",
        file_record_id=second.id,
        barcode="TAPE002",
    )
    filesystem.request_hydration(HydrationRequest(paths=["/pools/critical/2026/first.mov"]))
    filesystem.request_hydration(HydrationRequest(paths=["/pools/critical/2026/second.mov"]))

    jobs = filesystem.list_hydration_jobs()

    assert len(jobs) == 2
    assert {job.paths[0] for job in jobs} == {
        "/pools/critical/2026/first.mov",
        "/pools/critical/2026/second.mov",
    }


def test_mock_sftp_listdir_returns_names(catalog_service: NasService, filesystem: VirtualFilesystem) -> None:
    seed_dataset(catalog_service)
    seed_file(catalog_service, dataset_id="2026", relative_path="alpha.mov")
    seed_file(catalog_service, dataset_id="2026", relative_path="beta.mov")
    session = MockSftpSession(filesystem)

    names = session.listdir("/pools/critical/2026")

    assert names == ["alpha.mov", "beta.mov"]


def test_mock_sftp_stat_returns_attrs(context: AppContext, catalog_service: NasService, filesystem: VirtualFilesystem) -> None:
    seed_dataset(catalog_service)
    record = seed_file(catalog_service, dataset_id="2026", relative_path="alpha.mov")
    seed_mapping(
        context,
        path="/pools/critical/2026/alpha.mov",
        pool_name="critical",
        dataset_id="2026",
        file_record_id=record.id,
        state=NasFileState.ONLINE_CACHED,
    )
    session = MockSftpSession(filesystem)

    attrs = session.stat("/pools/critical/2026/alpha.mov")

    assert attrs.filename == "alpha.mov"
    assert attrs.st_size == 128
    assert attrs.st_mode == 0o100644


def test_mock_sftp_open_offline_raises_with_hydration(
    context: AppContext,
    catalog_service: NasService,
    filesystem: VirtualFilesystem,
) -> None:
    seed_dataset(catalog_service)
    record = seed_file(
        catalog_service,
        dataset_id="2026",
        relative_path="offline.mov",
        status=NasFileState.OFFLINE_ON_TAPE,
    )
    seed_mapping(
        context,
        path="/pools/critical/2026/offline.mov",
        pool_name="critical",
        dataset_id="2026",
        file_record_id=record.id,
        barcode="TAPE404",
    )
    session = MockSftpSession(filesystem)

    with pytest.raises(OfflineFileError, match=r"File is offline on tape TAPE404\. Hydration queued as job"):
        session.open("/pools/critical/2026/offline.mov")

    assert len(filesystem.list_hydration_jobs()) == 1


def test_virtual_ls_route_requires_auth() -> None:
    anon = TestClient(app)

    response = anon.get("/virtual/ls", params={"path": "/"})

    assert response.status_code in (401, 403)


def test_virtual_hydrate_route_returns_job(context: AppContext, catalog_service: NasService) -> None:
    seed_dataset(catalog_service)
    record = seed_file(
        catalog_service,
        dataset_id="2026",
        relative_path="offline.mov",
        status=NasFileState.OFFLINE_ON_TAPE,
    )
    seed_mapping(
        context,
        path="/pools/critical/2026/offline.mov",
        pool_name="critical",
        dataset_id="2026",
        file_record_id=record.id,
    )
    _login()

    response = client.post(
        "/virtual/hydrate",
        json={"paths": ["/pools/critical/2026/offline.mov"], "pool": "critical"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "queued"


def test_virtual_job_route_returns_job(context: AppContext, catalog_service: NasService) -> None:
    seed_dataset(catalog_service)
    record = seed_file(
        catalog_service,
        dataset_id="2026",
        relative_path="offline.mov",
        status=NasFileState.OFFLINE_ON_TAPE,
    )
    seed_mapping(
        context,
        path="/pools/critical/2026/offline.mov",
        pool_name="critical",
        dataset_id="2026",
        file_record_id=record.id,
    )
    _login()
    create_response = client.post(
        "/virtual/hydrate",
        json={"paths": ["/pools/critical/2026/offline.mov"], "pool": "critical"},
    )
    job_id = create_response.json()["job_id"]

    response = client.get(f"/virtual/jobs/{job_id}")

    assert response.status_code == 200
    assert response.json()["job_id"] == job_id
