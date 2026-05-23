from __future__ import annotations

from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, reset_context
from openblade.catalog.repository import CatalogRepository
from openblade.config import OpenBladeConfig
from openblade.nas.error_codes import KNOWN_ERROR_CODES
from openblade.nas.health_service import HealthService
from openblade.nas.types import (
    CatalogRebuildRunRecord,
    DatasetStatus,
    HealthStatus,
    NasDataset,
    NasFileRecord,
    PathMappingRecord,
)


def make_client(tmp_path, db_name: str) -> TestClient:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / db_name}"))
    reset_context(context)
    return TestClient(app)


def login(client: TestClient) -> None:
    response = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert response.status_code == 200


def make_health_service(tmp_path, db_name: str = "health.db") -> tuple[HealthService, CatalogRepository]:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / db_name}"))
    reset_context(context)
    return HealthService(context.catalog, context.library, context.ltfs), context.catalog


class BrokenRepo:
    def list_nas_datasets(self) -> list[dict[str, object]]:
        raise RuntimeError("boom")

    def count_path_mappings(self) -> int:
        raise RuntimeError("boom")

    def list_cartridges(self) -> list[dict[str, object]]:
        raise RuntimeError("boom")

    def list_rebuild_runs(self, limit: int = 1) -> list[dict[str, object]]:
        del limit
        raise RuntimeError("boom")


class BrokenLibrary:
    def inventory(self):
        raise RuntimeError("library boom")


class BrokenLtfs:
    def list_tapes(self) -> list[str]:
        raise RuntimeError("ltfs boom")


class DegradedLtfs:
    backend = None


def test_check_health_all_ok(tmp_path) -> None:
    service, _ = make_health_service(tmp_path, "all-ok.db")

    result = service.check_health()

    assert result.status is HealthStatus.OK
    assert {component.name for component in result.components} == {"database", "library", "ltfs"}


def test_check_health_db_failure_returns_unhealthy(tmp_path) -> None:
    service, _ = make_health_service(tmp_path, "db-fail.db")
    service.repo = BrokenRepo()  # type: ignore[assignment]

    result = service.check_health()

    database = next(component for component in result.components if component.name == "database")
    assert database.status is HealthStatus.UNHEALTHY
    assert "boom" not in database.message


def test_check_health_aggregates_worst_status(tmp_path) -> None:
    service, _ = make_health_service(tmp_path, "aggregate.db")
    service.ltfs = DegradedLtfs()

    result = service.check_health()

    assert result.status is HealthStatus.DEGRADED


def test_check_ready_true_when_all_ok(tmp_path) -> None:
    service, _ = make_health_service(tmp_path, "ready-ok.db")

    result = service.check_ready()

    assert result.ready is True
    assert result.reason == ""


def test_check_ready_false_when_db_down(tmp_path) -> None:
    service, _ = make_health_service(tmp_path, "ready-db-down.db")
    service.repo = BrokenRepo()  # type: ignore[assignment]

    result = service.check_ready()

    assert result.ready is False
    assert result.reason == "database unavailable"


def test_get_library_status_returns_drive_count(tmp_path) -> None:
    service, _ = make_health_service(tmp_path, "library-status.db")

    result = service.get_library_status()

    assert result.library_connected is True
    assert len(result.drives) == len(service.library.inventory().drives)
    assert result.slots_total >= result.slots_occupied


def test_get_catalog_status_returns_counts(tmp_path) -> None:
    service, repo = make_health_service(tmp_path, "catalog-status.db")
    repo.upsert_nas_dataset(
        NasDataset(id="dataset-1", name="Dataset 1", status=DatasetStatus.ARCHIVED).model_dump(mode="json")
    )
    repo.upsert_nas_file_record(
        NasFileRecord(id="file-1", dataset_id="dataset-1", relative_path="file.txt").model_dump(mode="json")
    )
    repo.upsert_path_mapping(
        PathMappingRecord(id="mapping-1", logical_path="/pool/file.txt", dataset_id="dataset-1")
    )
    repo.create_rebuild_run(
        CatalogRebuildRunRecord(
            id="run-1",
            status="completed",
            triggered_by="test",
            barcodes_planned=[],
            barcodes_completed=[],
            barcodes_failed=[],
            barcodes_skipped=[],
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        ).model_dump(mode="json")
    )

    result = service.get_catalog_status()

    assert result.db_reachable is True
    assert result.total_datasets >= 1
    assert result.total_file_records >= 1
    assert result.total_path_mappings >= 1
    assert result.total_cartridges >= 0
    assert result.last_rebuild_run_id == "run-1"
    assert result.last_rebuild_status == "completed"


def test_health_never_raises_on_exception() -> None:
    service = HealthService(BrokenRepo(), BrokenLibrary(), BrokenLtfs())  # type: ignore[arg-type]

    result = service.check_health()

    assert result.status is HealthStatus.UNHEALTHY
    assert all(component.status is HealthStatus.UNHEALTHY for component in result.components)
    assert all("boom" not in component.message for component in result.components)


class PartiallyBrokenRepo:
    def list_nas_datasets(self) -> list[dict[str, object]]:
        return []

    def count_path_mappings(self) -> int:
        raise RuntimeError("boom")

    def list_cartridges(self) -> list[dict[str, object]]:
        return []

    def list_rebuild_runs(self, limit: int = 1) -> list[dict[str, object]]:
        del limit
        return []


class FailingSession:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, statement):
        del statement
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("boom")

        class Result:
            def scalar_one(self_inner) -> int:
                return 1

        return Result()


class PartiallyBrokenCatalogRepo:
    def __init__(self) -> None:
        self.session = FailingSession()

    def list_rebuild_runs(self, limit: int = 1) -> list[dict[str, object]]:
        del limit
        return [{"id": "run-1", "status": "completed"}]


def test_check_health_db_partial_failure_returns_degraded() -> None:
    service = HealthService(PartiallyBrokenRepo(), BrokenLibrary(), BrokenLtfs())  # type: ignore[arg-type]

    result = service.check_health()

    database = next(component for component in result.components if component.name == "database")
    assert database.status is HealthStatus.DEGRADED
    assert "partially readable" in database.message


def test_get_catalog_status_keeps_successful_counts_when_one_query_fails() -> None:
    service = HealthService(PartiallyBrokenCatalogRepo(), BrokenLibrary(), BrokenLtfs())  # type: ignore[arg-type]

    result = service.get_catalog_status()

    assert result.db_reachable is True
    assert result.total_datasets == 1
    assert result.total_file_records == -1
    assert result.total_path_mappings == 1
    assert result.total_cartridges == 1
    assert result.last_rebuild_run_id == "run-1"
    assert result.last_rebuild_status == "completed"


def test_error_codes_all_have_required_fields() -> None:
    assert KNOWN_ERROR_CODES
    for item in KNOWN_ERROR_CODES:
        assert item.code
        assert item.severity
        assert item.title
        assert item.description
        assert item.action


def test_known_error_codes_include_safety_003() -> None:
    assert any(item.code == "SAFETY_003" for item in KNOWN_ERROR_CODES)


def test_known_error_codes_include_auth_001() -> None:
    assert any(item.code == "AUTH_001" for item in KNOWN_ERROR_CODES)


def test_known_error_codes_minimum_count() -> None:
    assert len(KNOWN_ERROR_CODES) >= 9
    codes = {e.code for e in KNOWN_ERROR_CODES}
    assert "SAFETY_001" in codes
    assert "SAFETY_002" in codes
    assert "SAFETY_003" in codes
    assert "AUTH_001" in codes
    assert "NAS_001" in codes
    assert "CATALOG_001" in codes
    assert "SYS_001" in codes


def test_api_healthz_no_auth_required(tmp_path) -> None:
    client = make_client(tmp_path, "api-healthz.db")

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] in {"ok", "degraded", "unhealthy"}


def test_api_readyz_no_auth_required(tmp_path) -> None:
    client = make_client(tmp_path, "api-readyz.db")

    response = client.get("/readyz")

    assert response.status_code == 200
    assert "ready" in response.json()


def test_api_version_no_auth_required(tmp_path) -> None:
    client = make_client(tmp_path, "api-version.db")

    response = client.get("/version")

    assert response.status_code == 200
    assert response.json()["version"] == "0.2.0"


def test_api_error_codes_returns_list(tmp_path) -> None:
    client = make_client(tmp_path, "api-error-codes.db")

    response = client.get("/error-codes")

    assert response.status_code == 200
    assert len(response.json()["error_codes"]) == len(KNOWN_ERROR_CODES)


def test_api_status_library_requires_auth(tmp_path) -> None:
    client = make_client(tmp_path, "api-library-auth.db")

    response = client.get("/status/library")

    assert response.status_code == 401


def test_api_status_catalog_requires_auth(tmp_path) -> None:
    client = make_client(tmp_path, "api-catalog-auth.db")

    response = client.get("/status/catalog")

    assert response.status_code == 401


def test_api_status_library_returns_payload_when_authenticated(tmp_path) -> None:
    client = make_client(tmp_path, "api-library-ok.db")
    login(client)

    response = client.get("/status/library")

    assert response.status_code == 200
    assert response.json()["library_connected"] is True


def test_api_status_catalog_returns_payload_when_authenticated(tmp_path) -> None:
    client = make_client(tmp_path, "api-catalog-ok.db")
    login(client)

    response = client.get("/status/catalog")

    assert response.status_code == 200
    assert response.json()["db_reachable"] is True
