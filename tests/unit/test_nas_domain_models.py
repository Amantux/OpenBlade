from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from openblade.nas.capacity import (
    AggregationPolicy,
    ExportPrepError,
    ExportSet,
    ExportSetState,
    FileSpan,
    ReplicationState,
    ReservationError,
    ReservationLedger,
    ScratchHealth,
    ScratchThresholds,
    SpanSegment,
    TapeFragmentation,
    Vault,
    VolumeGroupReplication,
    evaluate_scratch,
    group_for_write,
    mark_export_ready,
    merge_volume_groups,
    plan_drives,
    planning_available,
    require_export_ready,
    required_tapes,
    span_state,
)
from openblade.nas.types import NasFileState

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


# --- scratch thresholds
def test_scratch_thresholds_evaluate_ok_warn_critical() -> None:
    t = ScratchThresholds(scratch_min=2, scratch_warn=5)
    assert evaluate_scratch(1, t) is ScratchHealth.CRITICAL
    assert evaluate_scratch(2, t) is ScratchHealth.WARN
    assert evaluate_scratch(5, t) is ScratchHealth.OK


def test_scratch_thresholds_warn_below_min_rejected() -> None:
    with pytest.raises(ValidationError):
        ScratchThresholds(scratch_min=5, scratch_warn=2)


# --- fragmentation
def test_fragmentation_below_threshold_needs_consolidation() -> None:
    assert TapeFragmentation(barcode="T1", live_bytes=40, written_bytes=100).needs_consolidation()
    assert not TapeFragmentation(
        barcode="T1", live_bytes=60, written_bytes=100
    ).needs_consolidation()
    assert not TapeFragmentation(barcode="T1", live_bytes=0, written_bytes=0).needs_consolidation()


def test_fragmentation_live_above_written_rejected() -> None:
    with pytest.raises(ValidationError):
        TapeFragmentation(barcode="T1", live_bytes=2, written_bytes=1)


# --- reservations
def test_reservation_counts_against_free_capacity() -> None:
    ledger = ReservationLedger()
    ledger.reserve("p1", 60, "job-a", timedelta(minutes=5), free_bytes=100, now=NOW)
    assert ledger.available("p1", 100, NOW) == 40
    assert ledger.available("p2", 100, NOW) == 100


def test_reservation_over_reserve_fails_typed() -> None:
    ledger = ReservationLedger()
    ledger.reserve("p1", 60, "job-a", timedelta(minutes=5), free_bytes=100, now=NOW)
    with pytest.raises(ReservationError):
        ledger.reserve("p1", 41, "job-b", timedelta(minutes=5), free_bytes=100, now=NOW)


def test_reservation_expired_is_ignored() -> None:
    ledger = ReservationLedger()
    ledger.reserve("p1", 100, "job-a", timedelta(minutes=5), free_bytes=100, now=NOW)
    later = NOW + timedelta(minutes=6)
    assert ledger.available("p1", 100, later) == 100
    ledger.reserve("p1", 100, "job-b", timedelta(minutes=5), free_bytes=100, now=later)


def test_reservation_release_frees_capacity() -> None:
    ledger = ReservationLedger()
    ledger.reserve("p1", 100, "job-a", timedelta(minutes=5), free_bytes=100, now=NOW)
    assert ledger.release("p1", "job-a") == 1
    assert ledger.available("p1", 100, NOW) == 100


# --- VG replication
def _vg(vg_id: str, req: int, present: int, *barcodes: str) -> VolumeGroupReplication:
    return VolumeGroupReplication(
        vg_id=vg_id, barcodes=barcodes, replicas_required=req, replicas_present=present
    )


def test_replication_state_assignment() -> None:
    assert _vg("a", 2, 2).state is ReplicationState.IN_SYNC
    assert _vg("a", 2, 1).state is ReplicationState.LAGGING
    assert _vg("a", 2, 0).state is ReplicationState.BROKEN


def test_replication_merge_re_evaluates_state() -> None:
    merged = merge_volume_groups(_vg("a", 1, 1, "T1"), _vg("b", 2, 2, "T2"), "ab")
    assert merged.replicas_required == 2
    assert merged.state is ReplicationState.LAGGING
    assert merged.barcodes == ("T1", "T2")


def test_replication_lagging_vg_cannot_export_prep() -> None:
    with pytest.raises(ExportPrepError):
        require_export_ready(_vg("a", 2, 1))


def test_replication_broken_repairs_only_after_replica_write() -> None:
    vg = _vg("a", 1, 0)
    assert vg.state is ReplicationState.BROKEN
    assert vg.replica_write_completed().state is ReplicationState.IN_SYNC


# --- spanning
def _span(path: str, *barcodes: str) -> FileSpan:
    segs = tuple(SpanSegment(barcode=b, offset=0, length=10) for b in barcodes)
    return FileSpan(path=path, segments=segs)


def test_span_required_tapes_deduplicated_in_order() -> None:
    assert required_tapes([_span("/a", "T1", "T2"), _span("/b", "T2", "T3")]) == ["T1", "T2", "T3"]


def test_span_on_missing_tape_is_missing_tape() -> None:
    span = _span("/a", "T1", "T2")
    assert span_state(span, {"T1", "T2"}) is NasFileState.OFFLINE_ON_TAPE
    assert span_state(span, {"T1"}) is NasFileState.MISSING_TAPE


def test_span_requires_segments() -> None:
    with pytest.raises(ValidationError):
        FileSpan(path="/a", segments=())


# --- export sets + vault
def test_export_set_ready_requires_all_members_in_sync() -> None:
    es = ExportSet(id="e1", barcodes=("T1", "T2"))
    groups = [_vg("a", 1, 1, "T1"), _vg("b", 2, 1, "T2")]
    with pytest.raises(ExportPrepError):
        mark_export_ready(es, groups)
    groups[1] = groups[1].replica_write_completed()
    assert mark_export_ready(es, groups).state is ExportSetState.READY


def test_export_set_unknown_member_refused() -> None:
    with pytest.raises(ExportPrepError):
        mark_export_ready(ExportSet(id="e1", barcodes=("T9",)), [_vg("a", 1, 1, "T1")])


def test_vaulted_barcode_is_offline_for_planning() -> None:
    vault = Vault(id="v1", barcodes=frozenset({"T2"}), location="offsite")
    avail = planning_available(["T1", "T2"], [vault])
    assert avail == {"T1"}
    assert span_state(_span("/a", "T2"), avail) is NasFileState.MISSING_TAPE


# --- drive planning
def test_plan_drives_one_drive_three_tapes() -> None:
    plan = plan_drives([_span("/a", "T1", "T2"), _span("/b", "T3")], available_drives=1)
    assert plan.required_drives == 1
    assert plan.estimated_swaps == 2


def test_plan_drives_enough_drives_no_swaps() -> None:
    plan = plan_drives([_span("/a", "T1", "T2", "T3")], available_drives=4)
    assert (plan.required_drives, plan.estimated_swaps) == (3, 0)


# --- aggregation
def test_aggregation_groups_small_files_into_containers() -> None:
    policy = AggregationPolicy(aggregate_below_bytes=10, max_container_bytes=12)
    files = [("/s1", 5), ("/big", 50), ("/s2", 5), ("/s3", 5)]
    assert group_for_write(files, policy) == [
        [("/big", 50)],
        [("/s1", 5), ("/s2", 5)],
        [("/s3", 5)],
    ]
