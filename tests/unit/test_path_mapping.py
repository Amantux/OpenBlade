from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, reset_context
from openblade.catalog.db import get_session, init_db
from openblade.catalog.repository import CatalogRepository
from openblade.config import OpenBladeConfig
from openblade.nas.path_mapping import PathMappingService
from openblade.nas.types import (
    NasFileState,
    PathLookupResult,
    PathMappingBulkUpsertRequest,
    PathMappingRecord,
    PathMappingSearchRequest,
)

client = TestClient(app)


def make_service(tmp_path: Path) -> PathMappingService:
    init_db(f"sqlite:///{tmp_path / 'path-mapping.db'}")
    return PathMappingService(CatalogRepository(get_session()))


@pytest.fixture(autouse=True)
def reset_app_context(tmp_path: Path) -> None:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'path-mapping-api.db'}"))
    reset_context(context)


def test_path_mapping_record_defaults() -> None:
    record = PathMappingRecord(logical_path="/pool/file.txt")

    assert record.id
    assert record.file_state is NasFileState.OFFLINE_ON_TAPE
    assert record.pool_id == ""
    assert record.all_barcodes == []



def test_path_lookup_result_can_represent_missing_path() -> None:
    result = PathLookupResult(logical_path="/missing.txt", found=False)

    assert result.found is False
    assert result.primary_barcode == ""
    assert result.file_state is NasFileState.OFFLINE_ON_TAPE



def test_record_file_upserts_and_returns_record(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    record = PathMappingRecord(
        logical_path="/pool/file.txt",
        pool_id="pool-a",
        dataset_id="dataset-a",
        primary_barcode="TAPE001",
        all_barcodes=["TAPE001"],
        size=123,
    )

    saved = service.record_file(record)

    assert saved.logical_path == record.logical_path
    assert saved.primary_barcode == "TAPE001"
    assert service.lookup(record.logical_path, record.pool_id).found is True



def test_record_file_updates_existing_mapping(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.record_file(
        PathMappingRecord(
            logical_path="/pool/file.txt",
            pool_id="pool-a",
            primary_barcode="TAPE001",
            all_barcodes=["TAPE001"],
        )
    )

    saved = service.record_file(
        PathMappingRecord(
            logical_path="/pool/file.txt",
            pool_id="pool-a",
            dataset_id="dataset-b",
            primary_barcode="TAPE002",
            all_barcodes=["TAPE002", "TAPE003"],
            size=456,
        )
    )

    assert saved.dataset_id == "dataset-b"
    assert saved.primary_barcode == "TAPE002"
    assert saved.all_barcodes == ["TAPE002", "TAPE003"]
    assert service.get_stats(pool_id="pool-a")["total_files"] == 1



def test_lookup_returns_found_true_after_record_file(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.record_file(
        PathMappingRecord(
            logical_path="/archive/demo.mov",
            pool_id="pool-a",
            primary_barcode="TAPE123",
            all_barcodes=["TAPE123"],
            checksum="abc",
        )
    )

    result = service.lookup("/archive/demo.mov", "pool-a")

    assert result.found is True
    assert result.primary_barcode == "TAPE123"
    assert result.checksum == "abc"



def test_lookup_returns_found_false_for_unknown_path(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    result = service.lookup("/archive/missing.mov", "pool-a")

    assert result.found is False
    assert result.logical_path == "/archive/missing.mov"



def test_lookup_warns_for_missing_tape_state(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.record_file(
        PathMappingRecord(
            logical_path="/archive/lost.mov",
            pool_id="pool-a",
            file_state=NasFileState.MISSING_TAPE,
            primary_barcode="TAPE404",
            all_barcodes=["TAPE404"],
        )
    )

    result = service.lookup("/archive/lost.mov", "pool-a")

    assert result.found is True
    assert "missing_tape" in result.warnings



def test_lookup_warns_for_multiple_barcodes_without_strategy(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.record_file(
        PathMappingRecord(
            logical_path="/archive/redundant.mov",
            pool_id="pool-a",
            primary_barcode="TAPE100",
            all_barcodes=["TAPE100", "TAPE101"],
            restore_strategy="",
        )
    )

    result = service.lookup("/archive/redundant.mov", "pool-a")

    assert "multiple_barcodes_without_restore_strategy" in result.warnings



def test_update_file_state_changes_state_and_returns_true(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.record_file(PathMappingRecord(logical_path="/archive/file.bin", pool_id="pool-a"))

    updated = service.update_file_state("/archive/file.bin", "pool-a", NasFileState.HYDRATING)

    assert updated is True
    assert service.lookup("/archive/file.bin", "pool-a").file_state is NasFileState.HYDRATING



def test_update_file_state_returns_false_for_unknown_path(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    assert service.update_file_state("/missing.bin", "pool-a", NasFileState.HYDRATING) is False



def test_remove_deletes_mapping_and_lookup_is_missing(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.record_file(PathMappingRecord(logical_path="/archive/remove.me", pool_id="pool-a"))

    assert service.remove("/archive/remove.me", "pool-a") is True
    assert service.lookup("/archive/remove.me", "pool-a").found is False



def test_search_prefix_filter_returns_subset(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.bulk_record_files(
        PathMappingBulkUpsertRequest(
            entries=[
                PathMappingRecord(logical_path="/archive/a/file1", pool_id="pool-a"),
                PathMappingRecord(logical_path="/archive/a/file2", pool_id="pool-a"),
                PathMappingRecord(logical_path="/archive/b/file3", pool_id="pool-a"),
            ]
        )
    )

    results = service.search(PathMappingSearchRequest(prefix="/archive/a"))

    assert [record.logical_path for record in results] == ["/archive/a/file1", "/archive/a/file2"]



def test_search_barcode_filter_matches_json_round_trip(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.bulk_record_files(
        PathMappingBulkUpsertRequest(
            entries=[
                PathMappingRecord(
                    logical_path="/archive/a.mov",
                    pool_id="pool-a",
                    primary_barcode="TAPE001",
                    all_barcodes=["TAPE001", "TAPE002"],
                ),
                PathMappingRecord(
                    logical_path="/archive/b.mov",
                    pool_id="pool-a",
                    primary_barcode="TAPE003",
                    all_barcodes=["TAPE003"],
                ),
            ]
        )
    )

    results = service.search(PathMappingSearchRequest(barcode="TAPE002"))

    assert [record.logical_path for record in results] == ["/archive/a.mov"]
    assert results[0].all_barcodes == ["TAPE001", "TAPE002"]



def test_bulk_record_files_returns_correct_count(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    count = service.bulk_record_files(
        PathMappingBulkUpsertRequest(
            entries=[
                PathMappingRecord(logical_path="/archive/one", pool_id="pool-a"),
                PathMappingRecord(logical_path="/archive/two", pool_id="pool-a"),
                PathMappingRecord(logical_path="/archive/three", pool_id="pool-a"),
            ]
        )
    )

    assert count == 3
    assert service.get_stats(pool_id="pool-a")["total_files"] == 3



def test_bulk_record_files_can_skip_existing_entries(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.record_file(PathMappingRecord(logical_path="/archive/existing", pool_id="pool-a"))

    count = service.bulk_record_files(
        PathMappingBulkUpsertRequest(
            overwrite_existing=False,
            entries=[
                PathMappingRecord(logical_path="/archive/existing", pool_id="pool-a"),
                PathMappingRecord(logical_path="/archive/new", pool_id="pool-a"),
            ],
        )
    )

    assert count == 1
    assert service.get_stats(pool_id="pool-a")["total_files"] == 2



def test_list_tapes_for_pool_returns_distinct_barcodes(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.bulk_record_files(
        PathMappingBulkUpsertRequest(
            entries=[
                PathMappingRecord(
                    logical_path="/archive/a",
                    pool_id="pool-a",
                    primary_barcode="TAPE001",
                    all_barcodes=["TAPE001", "TAPE002"],
                ),
                PathMappingRecord(
                    logical_path="/archive/b",
                    pool_id="pool-a",
                    primary_barcode="TAPE002",
                    all_barcodes=["TAPE002", "TAPE003"],
                ),
            ]
        )
    )

    assert service.list_tapes_for_pool("pool-a") == ["TAPE001", "TAPE002", "TAPE003"]



def test_get_stats_returns_expected_totals(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.bulk_record_files(
        PathMappingBulkUpsertRequest(
            entries=[
                PathMappingRecord(
                    logical_path="/archive/a",
                    pool_id="pool-a",
                    dataset_id="dataset-a",
                    primary_barcode="TAPE001",
                    all_barcodes=["TAPE001"],
                    size=10,
                    file_state=NasFileState.OFFLINE_ON_TAPE,
                ),
                PathMappingRecord(
                    logical_path="/archive/b",
                    pool_id="pool-a",
                    dataset_id="dataset-a",
                    primary_barcode="TAPE002",
                    all_barcodes=["TAPE002", "TAPE003"],
                    size=20,
                    file_state=NasFileState.HYDRATING,
                ),
            ]
        )
    )

    stats = service.get_stats(pool_id="pool-a", dataset_id="dataset-a")

    assert stats["total_files"] == 2
    assert stats["total_bytes"] == 30
    assert stats["by_state"] == {"offline_on_tape": 1, "hydrating": 1}
    assert stats["tape_count"] == 3



def _login(c: TestClient) -> None:
    r = c.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert r.status_code == 200


def test_post_path_mapping_route_returns_valid_body() -> None:
    _login(client)
    payload = {
        "logical_path": "/api/demo.mov",
        "pool_id": "pool-a",
        "dataset_id": "dataset-a",
        "primary_barcode": "TAPE900",
        "all_barcodes": ["TAPE900"],
        "size": 99,
    }

    response = client.post("/nas/path-mappings", json=payload)

    assert response.status_code == 200
    assert response.json()["logical_path"] == payload["logical_path"]
    assert response.json()["primary_barcode"] == payload["primary_barcode"]


def test_post_path_mapping_route_rejects_anonymous() -> None:
    """Unauthenticated requests must be rejected (hotfix Beta)."""
    anon = TestClient(app)  # fresh client — no session cookie
    payload = {"logical_path": "/api/demo.mov", "pool_id": "pool-a"}
    response = anon.post("/nas/path-mappings", json=payload)
    assert response.status_code in (401, 403)


def test_post_path_mapping_route_rejects_empty_logical_path() -> None:
    """Empty logical_path must return 422 (hotfix Beta)."""
    _login(client)
    response = client.post("/nas/path-mappings", json={"logical_path": ""})
    assert response.status_code == 422


def test_post_path_mapping_route_rejects_traversal_path() -> None:
    """Path traversal in logical_path must return 422 (hotfix Beta)."""
    _login(client)
    response = client.post("/nas/path-mappings", json={"logical_path": "/pool/../etc/passwd"})
    assert response.status_code == 422


def test_delete_path_mapping_route_returns_404_when_not_found() -> None:
    """DELETE on unknown path must return 404 (hotfix Beta)."""
    _login(client)
    response = client.delete("/nas/path-mappings?path=/nonexistent/file.txt")
    assert response.status_code == 404


def test_remove_unknown_path_returns_false(tmp_path: Path) -> None:
    """PathMappingService.remove on unknown path returns False (Gamma edge-case)."""
    service = make_service(tmp_path)
    assert service.remove("/not/here.txt", "") is False

