"""Pool reservations are a budget the archive planner must respect."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from openblade.catalog.repository import CatalogRepository
from openblade.nas.capacity import (
    PLAN_RESERVATION_TTL,
    ReservationError,
    plan_with_pool_reservation,
)
from openblade.nas.planner import POOL_BUDGET_BLOCKER, ArchivePlanner
from openblade.nas.types import ArchivePlanRequest

TAPE_CAPACITY = 1_000


def _request(
    pool_reserved_bytes: int, *, copies: int = 1, pool: str | None = None
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


def test_booked_plan_consumes_pool_so_second_plan_is_refused(repo: CatalogRepository) -> None:
    # Arrange: pool of 2000 bytes; another job already holds 800.
    assert repo.reserve_nas_capacity(
        "pool-1", 800, "other-job", NOW + timedelta(hours=1), capacity_bytes=2_000, now=NOW
    )

    # Act: first plan places 600 and books it (1400 held); second places 601 > 600 left.
    first = plan_with_pool_reservation(repo, _request(0, pool="pool-1"), now=NOW)
    second_request = _request(0, pool="pool-1").model_copy(
        update={"file_sizes": {"/a": 300, "/b": 301}}
    )
    second = plan_with_pool_reservation(repo, second_request, now=NOW)

    # Assert
    assert first.is_safe_to_enqueue
    assert {r["owner"] for r in repo.reservations_for_pool("pool-1", now=NOW)} == {
        "other-job",
        first.plan_id,
    }
    assert POOL_BUDGET_BLOCKER in second.enqueue_blockers


def test_booking_expires_after_ttl(repo: CatalogRepository) -> None:
    plan_with_pool_reservation(repo, _request(0, pool="pool-1", copies=3), now=NOW)

    later = plan_with_pool_reservation(
        repo, _request(0, pool="pool-1", copies=3), now=NOW + PLAN_RESERVATION_TTL
    )

    assert later.is_safe_to_enqueue


def test_expired_reservation_is_ignored(repo: CatalogRepository) -> None:
    assert repo.reserve_nas_capacity(
        "pool-1", 2_000, "stale-job", NOW + timedelta(seconds=1), capacity_bytes=2_000, now=NOW
    )

    plan = plan_with_pool_reservation(
        repo, _request(0, pool="pool-1"), now=NOW + timedelta(minutes=1)
    )

    assert plan.is_safe_to_enqueue


def test_lost_booking_race_raises_typed_error(repo: CatalogRepository) -> None:
    # Simulate a concurrent writer landing between the budget read and the booking.
    class RacingRepo:
        def reservations_for_pool(self, pool_id: str, *, now: datetime) -> list[dict[str, object]]:
            return []

        def reserve_nas_capacity(self, *args: object, **kwargs: object) -> bool:
            return False

    with pytest.raises(ReservationError):
        plan_with_pool_reservation(RacingRepo(), _request(0, pool="pool-1"), now=NOW)  # type: ignore[arg-type]


def _seed_cartridges(repo: CatalogRepository, scratch: int) -> int:
    """Add cartridges on top of the simulator's seeded stock; return that baseline."""
    baseline = repo.count_scratch_cartridges()
    from openblade.catalog.models import Cartridge

    for i in range(scratch):
        repo.session.add(Cartridge(barcode=f"S{i:05d}L8", state="in_slot"))
    repo.session.add(Cartridge(barcode="LOADED1L8", state="loaded"))
    repo.session.add(Cartridge(barcode="ASSIGN1L8", state="in_slot", volume_group_id="vg-1"))
    repo.session.commit()
    return baseline


def test_count_scratch_excludes_loaded_and_assigned(repo: CatalogRepository) -> None:
    baseline = _seed_cartridges(repo, scratch=2)

    assert repo.count_scratch_cartridges() == baseline + 2


def _scratch_component(tmp_path: Path, scratch: int, thresholds: tuple[int, int] | None):  # type: ignore[no-untyped-def]
    from tests.unit.test_health_service import make_health_service

    service, _ = make_health_service(tmp_path, "scratch.db")
    base = _seed_cartridges(service.repo, scratch)
    if thresholds is not None:
        service.repo.set_scratch_thresholds(base + thresholds[0], base + thresholds[1])
    result = service.check_health()
    return result, next((c for c in result.components if c.name == "scratch"), None)


def test_scratch_component_absent_when_thresholds_unset(tmp_path: Path) -> None:
    _, component = _scratch_component(tmp_path, 0, None)
    assert component is None


def test_scratch_warn_is_ok_with_message(tmp_path: Path) -> None:
    _, component = _scratch_component(tmp_path, 3, (2, 5))
    assert component is not None
    assert component.status.value == "ok"
    assert component.message.startswith("scratch count ")
    assert " below warn " in component.message


def test_scratch_critical_is_degraded_not_unhealthy(tmp_path: Path) -> None:
    from openblade.nas.types import HealthStatus

    _, component = _scratch_component(tmp_path, 1, (2, 5))
    assert component is not None
    assert component.status is HealthStatus.DEGRADED


def test_upload_share_push_plan_refused_when_pool_budget_consumed(
    repo: CatalogRepository,
) -> None:
    from fastapi import HTTPException

    from openblade.api.routes_upload import _plan_share_push

    # Arrange: another job holds 1500 of the pool's 2000 bytes; the push needs 600.
    assert repo.reserve_nas_capacity(
        "pool-1", 1_500, "other-job", NOW + timedelta(hours=1), capacity_bytes=2_000, now=NOW
    )

    # Act
    with pytest.raises(HTTPException) as exc_info:
        _plan_share_push(repo, _request(0, pool="pool-1"))

    # Assert: refused on the budget, and nothing new was booked.
    assert exc_info.value.status_code == 400
    assert POOL_BUDGET_BLOCKER in str(exc_info.value.detail)
    assert [r["owner"] for r in repo.reservations_for_pool("pool-1", now=NOW)] == ["other-job"]


def test_upload_share_push_plan_books_its_reservation(repo: CatalogRepository) -> None:
    from openblade.api.routes_upload import _plan_share_push

    plan = _plan_share_push(repo, _request(0, pool="pool-1"))

    assert {r["owner"] for r in repo.reservations_for_pool("pool-1", now=datetime.now(UTC))} == {
        plan.plan_id
    }
