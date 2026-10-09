"""Tape-native NAS planning models: scratch thresholds, fragmentation,
capacity reservations, VG replication, file spanning, export sets/vaults,
drive planning, and small-file aggregation.

Pure models and functions; no I/O. Planners compose these.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openblade.nas.types import NasFileState

# --- 2. scratch thresholds -------------------------------------------------


class ScratchHealth(str, Enum):
    OK = "ok"
    WARN = "warn"
    CRITICAL = "critical"


class ScratchThresholds(BaseModel):
    model_config = ConfigDict(frozen=True)

    scratch_min: int = Field(ge=0)
    scratch_warn: int = Field(ge=0)

    @model_validator(mode="after")
    def _warn_above_min(self) -> ScratchThresholds:
        if self.scratch_warn < self.scratch_min:
            raise ValueError("scratch_warn must be >= scratch_min")
        return self


def evaluate_scratch(scratch_count: int, thresholds: ScratchThresholds) -> ScratchHealth:
    if scratch_count < thresholds.scratch_min:
        return ScratchHealth.CRITICAL
    if scratch_count < thresholds.scratch_warn:
        return ScratchHealth.WARN
    return ScratchHealth.OK


# --- 6. fragmentation ------------------------------------------------------

DEFAULT_CONSOLIDATION_RATIO = 0.5


class TapeFragmentation(BaseModel):
    model_config = ConfigDict(frozen=True)

    barcode: str = Field(min_length=1)
    live_bytes: int = Field(ge=0)
    written_bytes: int = Field(ge=0)

    @model_validator(mode="after")
    def _live_le_written(self) -> TapeFragmentation:
        if self.live_bytes > self.written_bytes:
            raise ValueError("live_bytes cannot exceed written_bytes")
        return self

    @property
    def live_ratio(self) -> float:
        return 1.0 if self.written_bytes == 0 else self.live_bytes / self.written_bytes

    def needs_consolidation(self, threshold: float = DEFAULT_CONSOLIDATION_RATIO) -> bool:
        return self.written_bytes > 0 and self.live_ratio < threshold


# --- 7. capacity reservations ---------------------------------------------


class ReservationError(ValueError):
    """A reservation cannot be satisfied from the pool's free capacity."""


class CapacityReservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    pool_id: str = Field(min_length=1)
    bytes: int = Field(gt=0)
    owner: str = Field(min_length=1)
    expires_at: datetime

    def active(self, now: datetime) -> bool:
        return now < self.expires_at


class ReservationLedger:
    """In-memory ledger of capacity reservations per pool."""

    def __init__(self) -> None:
        self._items: list[CapacityReservation] = []

    def reserved(self, pool_id: str, now: datetime) -> int:
        return sum(r.bytes for r in self._items if r.pool_id == pool_id and r.active(now))

    def available(self, pool_id: str, free_bytes: int, now: datetime) -> int:
        return max(0, free_bytes - self.reserved(pool_id, now))

    def reserve(
        self,
        pool_id: str,
        nbytes: int,
        owner: str,
        ttl: timedelta,
        *,
        free_bytes: int,
        now: datetime,
    ) -> CapacityReservation:
        if nbytes > self.available(pool_id, free_bytes, now):
            raise ReservationError(f"pool {pool_id} cannot reserve {nbytes} bytes")
        res = CapacityReservation(pool_id=pool_id, bytes=nbytes, owner=owner, expires_at=now + ttl)
        self._items.append(res)
        return res

    def release(self, pool_id: str, owner: str) -> int:
        before = len(self._items)
        self._items = [r for r in self._items if not (r.pool_id == pool_id and r.owner == owner)]
        return before - len(self._items)


# --- 5. VG replication -----------------------------------------------------


class ReplicationState(str, Enum):
    IN_SYNC = "in_sync"
    LAGGING = "lagging"
    BROKEN = "broken"


class ExportPrepError(ValueError):
    """Export preparation refused (e.g. replicas not in sync)."""


class VolumeGroupReplication(BaseModel):
    model_config = ConfigDict(frozen=True)

    vg_id: str = Field(min_length=1)
    barcodes: tuple[str, ...] = ()
    replicas_required: int = Field(ge=1)
    replicas_present: int = Field(ge=0)

    @property
    def state(self) -> ReplicationState:
        if self.replicas_present >= self.replicas_required:
            return ReplicationState.IN_SYNC
        if self.replicas_present == 0:
            return ReplicationState.BROKEN
        return ReplicationState.LAGGING

    def replica_write_completed(self) -> VolumeGroupReplication:
        return self.model_copy(update={"replicas_present": self.replicas_present + 1})


def merge_volume_groups(
    a: VolumeGroupReplication, b: VolumeGroupReplication, new_id: str
) -> VolumeGroupReplication:
    """Merged VG needs the stricter requirement and has only the replicas both share."""
    return VolumeGroupReplication(
        vg_id=new_id,
        barcodes=a.barcodes + tuple(x for x in b.barcodes if x not in a.barcodes),
        replicas_required=max(a.replicas_required, b.replicas_required),
        replicas_present=min(a.replicas_present, b.replicas_present),
    )


def require_export_ready(vg: VolumeGroupReplication) -> None:
    if vg.state is not ReplicationState.IN_SYNC:
        raise ExportPrepError(f"volume group {vg.vg_id} is {vg.state.value}; cannot export-prep")


# --- 8. file spanning ------------------------------------------------------


class SpanSegment(BaseModel):
    model_config = ConfigDict(frozen=True)

    barcode: str = Field(min_length=1)
    offset: int = Field(ge=0)
    length: int = Field(gt=0)


class FileSpan(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str = Field(min_length=1)
    segments: tuple[SpanSegment, ...] = Field(min_length=1)

    @property
    def barcodes(self) -> list[str]:
        return list(dict.fromkeys(s.barcode for s in self.segments))


def span_state(span: FileSpan, available: set[str]) -> NasFileState:
    if all(b in available for b in span.barcodes):
        return NasFileState.OFFLINE_ON_TAPE
    return NasFileState.MISSING_TAPE


def required_tapes(spans: Iterable[FileSpan]) -> list[str]:
    return list(dict.fromkeys(b for s in spans for b in s.barcodes))


# --- 9. export sets + vault ------------------------------------------------


class ExportSetState(str, Enum):
    PREPARING = "preparing"
    READY = "ready"
    EXPORTED = "exported"
    IMPORTED = "imported"


class ExportSet(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    barcodes: tuple[str, ...] = Field(min_length=1)
    state: ExportSetState = ExportSetState.PREPARING


class Vault(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    barcodes: frozenset[str] = frozenset()
    location: str = Field(min_length=1)


def mark_export_ready(export_set: ExportSet, groups: Sequence[VolumeGroupReplication]) -> ExportSet:
    """PREPARING → READY only if every member tape's VG replication is IN_SYNC."""
    if export_set.state is not ExportSetState.PREPARING:
        raise ExportPrepError(f"export set {export_set.id} is {export_set.state.value}")
    for barcode in export_set.barcodes:
        owners = [g for g in groups if barcode in g.barcodes]
        if not owners:
            raise ExportPrepError(f"tape {barcode} has no known replication state")
        for g in owners:
            require_export_ready(g)
    return export_set.model_copy(update={"state": ExportSetState.READY})


def planning_available(library_barcodes: Iterable[str], vaults: Iterable[Vault]) -> set[str]:
    """Barcodes usable for planning: anything in a vault is OFFLINE."""
    vaulted = {b for v in vaults for b in v.barcodes}
    return {b for b in library_barcodes if b not in vaulted}


# --- 10. required drives / estimated swaps --------------------------------


class DrivePlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    tapes: tuple[str, ...]
    required_drives: int
    estimated_swaps: int


def plan_drives(spans: Iterable[FileSpan], available_drives: int) -> DrivePlan:
    if available_drives < 1:
        raise ValueError("available_drives must be >= 1")
    tapes = required_tapes(spans)
    used = min(len(tapes), available_drives)
    return DrivePlan(
        tapes=tuple(tapes),
        required_drives=used,
        estimated_swaps=max(0, len(tapes) - available_drives),
    )


# --- 11. small-file aggregation -------------------------------------------


class AggregationPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    aggregate_below_bytes: int = Field(ge=0)
    max_container_bytes: int = Field(gt=0)


def group_for_write(
    files: Sequence[tuple[str, int]], policy: AggregationPolicy
) -> list[list[tuple[str, int]]]:
    """Group files into write units: small files share containers, large stand alone."""
    units: list[list[tuple[str, int]]] = []
    current: list[tuple[str, int]] = []
    current_size = 0
    for path, size in files:
        if size >= policy.aggregate_below_bytes:
            units.append([(path, size)])
            continue
        if current and current_size + size > policy.max_container_bytes:
            units.append(current)
            current, current_size = [], 0
        current.append((path, size))
        current_size += size
    if current:
        units.append(current)
    return units
