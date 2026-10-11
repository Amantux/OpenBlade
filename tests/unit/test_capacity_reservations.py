"""Pool reservations are a budget the archive planner must respect."""

from __future__ import annotations

from openblade.nas.planner import POOL_BUDGET_BLOCKER, ArchivePlanner
from openblade.nas.types import ArchivePlanRequest

TAPE_CAPACITY = 1_000


def _request(pool_reserved_bytes: int, *, copies: int = 1) -> ArchivePlanRequest:
    return ArchivePlanRequest(
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
