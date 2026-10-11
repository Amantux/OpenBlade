"""Pool reservations are a budget the archive planner must respect."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from openblade.catalog.repository import CatalogRepository
from openblade.nas.capacity import (
    PLAN_RESERVATION_TTL,
    ReservationError,
    book_plan_capacity,
    count_scratch_tapes,
    plan_with_pool_budget,
)
from openblade.nas.planner import POOL_BUDGET_BLOCKER, ArchivePlanner
from openblade.nas.types import ArchivePlanRequest

TAPE_CAPACITY = 1_000


def _request(
    pool_reserved_bytes: int, *, copies: int = 1, pool: str | None = "pool-1"
) -> ArchivePlanRequest:
    return ArchivePlanRequest(
        pool=pool,
        files=["/a", "/b"],
        file_sizes={"/a": 300, "/b": 300},
        available_tapes=["T00001L8", "T00002L8"],
        tape_capacities={"T00001L8": TAPE_CAPACITY, "T00002L8": TAPE_CAPACITY},
        copies=copies,
        pool_reserved_bytes=pool_reserved_bytes,
    )


def _server_request() -> ArchivePlanRequest:
    """A pool request as the share-push route builds it: no client capacity inputs."""
    return ArchivePlanRequest(
        files=["a.bin", "b.bin"], file_sizes={"a.bin": 300, "b.bin": 300}, pool="pool-1"
    )


def test_plan_fits_exactly_within_pool_budget() -> None:
    # 2 tapes x 1000 = 2000; 600 placed -> exactly 1400 may be reserved by others.
    plan = ArchivePlanner().plan(_request(1_400))
    assert POOL_BUDGET_BLOCKER not in plan.enqueue_blockers
    assert plan.is_safe_to_enqueue


def test_same_plan_refused_when_budget_shrinks_by_one_byte() -> None:
    plan = ArchivePlanner().plan(_request(1_401))
    assert POOL_BUDGET_BLOCKER in plan.enqueue_blockers
    assert not plan.is_safe_to_enqueue


def test_budget_counts_every_copy() -> None:
    # Two copies place 1200 bytes; 801 reserved leaves only 1199.
    assert POOL_BUDGET_BLOCKER in ArchivePlanner().plan(_request(801, copies=2)).enqueue_blockers
    assert (
        POOL_BUDGET_BLOCKER not in ArchivePlanner().plan(_request(800, copies=2)).enqueue_blockers
    )


def test_default_budget_is_unchanged_behaviour() -> None:
    request = _request(0)
    assert request.pool_reserved_bytes == 0
    assert ArchivePlanner().plan(request).is_safe_to_enqueue


NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


@pytest.fixture
def repo(tmp_path: Path) -> CatalogRepository:
    from tests.unit.test_nas_restore_planner import make_nas_service

    return make_nas_service(tmp_path).repository


@pytest.fixture
def two_tapes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Server-derived pool capacity of 2000 bytes for ``book_plan_capacity``."""
    monkeypatch.setattr(
        "openblade.nas.capacity.server_data_tapes",
        lambda repo, library: {"T00001L8": TAPE_CAPACITY, "T00002L8": TAPE_CAPACITY},
    )


@pytest.mark.usefixtures("two_tapes")
def test_preview_books_nothing_but_second_start_is_refused(repo: CatalogRepository) -> None:
    # Arrange: pool of 2000 bytes; another job already holds 800.
    assert repo.reserve_nas_capacity(
        "pool-1", 800, "other-job", NOW + timedelta(hours=1), capacity_bytes=2_000, now=NOW
    )

    # Act: 600- and 601-byte previews both fit (nothing booked); only one START can book.
    first = plan_with_pool_budget(repo, _request(0, pool="pool-1"), now=NOW)
    second_request = _request(0, pool="pool-1").model_copy(
        update={"file_sizes": {"/a": 300, "/b": 301}}
    )
    second = plan_with_pool_budget(repo, second_request, now=NOW)
    book_plan_capacity(repo, first, None, now=NOW)

    # Assert
    assert first.is_safe_to_enqueue and second.is_safe_to_enqueue
    with pytest.raises(ReservationError):
        book_plan_capacity(repo, second, None, now=NOW)
    assert {r["owner"] for r in repo.reservations_for_pool("pool-1", now=NOW)} == {
        "other-job",
        first.plan_id,
    }


@pytest.mark.usefixtures("two_tapes")
def test_booking_expires_after_ttl(repo: CatalogRepository) -> None:
    first = plan_with_pool_budget(repo, _request(0, pool="pool-1", copies=3), now=NOW)
    book_plan_capacity(repo, first, None, now=NOW)

    later = plan_with_pool_budget(
        repo, _request(0, pool="pool-1", copies=3), now=NOW + PLAN_RESERVATION_TTL
    )
    book_plan_capacity(repo, later, None, now=NOW + PLAN_RESERVATION_TTL)

    assert later.is_safe_to_enqueue


def test_expired_reservation_is_ignored(repo: CatalogRepository) -> None:
    assert repo.reserve_nas_capacity(
        "pool-1", 2_000, "stale-job", NOW + timedelta(seconds=1), capacity_bytes=2_000, now=NOW
    )

    plan = plan_with_pool_budget(repo, _request(0, pool="pool-1"), now=NOW + timedelta(minutes=1))

    assert plan.is_safe_to_enqueue


@pytest.mark.usefixtures("two_tapes")
def test_lost_booking_race_raises_typed_error(repo: CatalogRepository) -> None:
    # Simulate a concurrent writer landing between the budget read and the booking.
    plan = plan_with_pool_budget(repo, _request(0, pool="pool-1"), now=NOW)

    class RacingRepo:
        def reserve_nas_capacity(self, *args: object, **kwargs: object) -> bool:
            return False

    with pytest.raises(ReservationError):
        book_plan_capacity(RacingRepo(), plan, None, now=NOW)  # type: ignore[arg-type]


def _scratch_service(tmp_path: Path):  # type: ignore[no-untyped-def]
    from tests.unit.test_health_service import make_health_service

    service, _ = make_health_service(tmp_path, "scratch.db")
    return service, count_scratch_tapes(service.repo, service.library)


def test_scratch_counts_blank_inventory_minus_assigned_and_written(tmp_path: Path) -> None:
    from openblade.catalog.models import Cartridge
    from openblade.jobs.inventory import InventoryService

    # Arrange: N blank tapes from the simulator inventory; none are catalog rows yet.
    service, blank = _scratch_service(tmp_path)
    slots = InventoryService(service.library).snapshot().slots
    barcodes = sorted(
        str(s.barcode) for s in slots if s.barcode and not str(s.barcode).startswith("CLN")
    )
    assert blank == len(barcodes) >= 2

    # Act + Assert: a volume-group member is no longer scratch...
    group = service.repo.create_volume_group("vg-scratch")
    service.repo.add_barcode_to_volume_group(group.id, barcodes[0])
    assert count_scratch_tapes(service.repo, service.library) == blank - 1
    # ...nor is a tape catalogued as written.
    written = service.repo.session.query(Cartridge).filter_by(barcode=barcodes[1]).one_or_none()
    if written is None:
        service.repo.session.add(Cartridge(barcode=barcodes[1], used_bytes=10))
    else:
        written.used_bytes = 10
    service.repo.session.commit()
    assert count_scratch_tapes(service.repo, service.library) == blank - 2


def _scratch_component(tmp_path: Path, thresholds: tuple[int, int] | None):  # type: ignore[no-untyped-def]
    service, base = _scratch_service(tmp_path)
    if thresholds is not None:
        service.repo.set_scratch_thresholds(base + thresholds[0], base + thresholds[1])
    result = service.check_health()
    return result, next((c for c in result.components if c.name == "scratch"), None)


def test_scratch_component_absent_when_thresholds_unset(tmp_path: Path) -> None:
    _, component = _scratch_component(tmp_path, None)
    assert component is None


def test_scratch_warn_is_ok_with_message(tmp_path: Path) -> None:
    _, component = _scratch_component(tmp_path, (-1, 2))
    assert component is not None
    assert component.status.value == "ok"
    assert component.message.startswith("scratch count ")
    assert " below warn " in component.message


def test_scratch_critical_is_degraded_not_unhealthy(tmp_path: Path) -> None:
    from openblade.nas.types import HealthStatus

    _, component = _scratch_component(tmp_path, (1, 3))
    assert component is not None
    assert component.status is HealthStatus.DEGRADED


def test_upload_share_push_plan_refused_when_pool_budget_consumed(
    repo: CatalogRepository,
) -> None:
    from fastapi import HTTPException

    from openblade.api.routes_upload import _plan_share_push
    from openblade.bootstrap import get_context
    from openblade.nas.capacity import server_data_tapes

    # Arrange: another job holds all but 100 bytes of the SERVER-derived capacity.
    repo.upsert_nas_pool({"id": "pool-1", "name": "P1"})
    capacity = sum(server_data_tapes(repo, get_context().library).values())
    now = datetime.now(UTC)
    assert repo.reserve_nas_capacity(
        "pool-1",
        capacity - 100,
        "other-job",
        now + timedelta(hours=1),
        capacity_bytes=capacity,
        now=now,
    )

    # Act: a body-free push (the route never sends capacity inputs) needing 600 bytes.
    with pytest.raises(HTTPException) as exc_info:
        _plan_share_push(repo, _server_request())

    # Assert: refused on the budget, and nothing new was booked.
    assert exc_info.value.status_code == 400
    assert POOL_BUDGET_BLOCKER in str(exc_info.value.detail)
    assert [r["owner"] for r in repo.reservations_for_pool("pool-1", now=now)] == ["other-job"]


def test_upload_share_push_plan_books_nothing_until_start(repo: CatalogRepository) -> None:
    from openblade.api.routes_upload import _plan_share_push

    repo.upsert_nas_pool({"id": "pool-1", "name": "P1"})
    plan = _plan_share_push(repo, _server_request())

    assert plan.is_safe_to_enqueue
    assert repo.reservations_for_pool("pool-1", now=datetime.now(UTC)) == []


def test_finished_job_releases_its_plan_reservation(tmp_path: Path) -> None:
    from openblade.nas.ingest import register_archive_plan, start_ingest_job
    from tests.unit.test_nas_ingest import _make_plan, _run_job, _setup_service

    # Arrange: the plan's own booking fills the 2000-byte pool; another job holds none.
    service, cache_root = _setup_service(tmp_path)
    repo = service.repository
    now = datetime.now(UTC)
    plan = register_archive_plan(_make_plan(cache_root))
    assert repo.reserve_nas_capacity(
        "pool-1", 2_000, plan.plan_id, now + PLAN_RESERVATION_TTL, capacity_bytes=2_000, now=now
    )
    assert POOL_BUDGET_BLOCKER in (
        plan_with_pool_budget(repo, _request(0, pool="pool-1"), now=now).enqueue_blockers
    )
    job = start_ingest_job(
        plan=plan,
        dataset_name="dataset-a",
        pool_id="pool-1",
        nas_service=service,
        cache_drive_id="cache-1",
    )

    # Act
    finished = _run_job(service, job.job_id)

    # Assert: terminal job freed the booking, so the same plan now fits.
    assert finished.status.value in {"archived", "failed", "cancelled"}
    assert repo.reservations_for_pool("pool-1", now=now) == []
    assert plan_with_pool_budget(repo, _request(0, pool="pool-1"), now=now).is_safe_to_enqueue


def test_delete_reservations_for_owner_keeps_other_owners(repo: CatalogRepository) -> None:
    for owner in ("plan-a", "plan-a", "plan-b"):
        assert repo.reserve_nas_capacity(
            "pool-1", 10, owner, NOW + timedelta(hours=1), capacity_bytes=2_000, now=NOW
        )

    assert repo.delete_nas_reservations_for_owner("plan-a") == 2
    assert [r["owner"] for r in repo.reservations_for_pool("pool-1", now=NOW)] == ["plan-b"]
