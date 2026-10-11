"""Tape-native NAS planning models: scratch thresholds, fragmentation,
capacity reservations, VG replication, file spanning, export sets/vaults,
drive planning, and small-file aggregation.

Pure models and functions; no I/O. Planners compose these.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openblade.jobs.inventory import InventoryService
from openblade.nas.planner import ArchivePlanner
from openblade.nas.types import ArchivePlan, ArchivePlanRequest, NasFileState

if TYPE_CHECKING:
    from openblade.catalog.repository import CatalogRepository
    from openblade.domain.backends import LibraryBackend

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


logger = logging.getLogger(__name__)


class ReservationError(ValueError):
    """A reservation cannot be satisfied from the pool's free capacity."""


# No lease-TTL setting is in scope where plans are built, so a booked plan holds
# its pool bytes for one hour; expiry is the release path for plans never started.
PLAN_RESERVATION_TTL = timedelta(hours=1)


class PoolNotFoundError(LookupError):
    """A pool-budgeted plan names a pool id the catalog does not know."""


class ClientBudgetFieldsError(ValueError):
    """A pool-budgeted request carried capacity inputs only the server may supply."""


# With ``pool`` set these come from the library + catalog; a client value is refused
# (never silently overwritten) so an inflated budget cannot be smuggled in.
SERVER_BUDGET_FIELDS = (
    "available_tapes",
    "tape_capacities",
    "reserved_bytes",
    "pool_reserved_bytes",
)


def server_data_tapes(
    repository: CatalogRepository, library: LibraryBackend | None
) -> dict[str, int]:
    """Writable data tapes (barcode -> usable bytes) from library inventory + catalog.

    The library holds blank/scratch media the catalog has never seen, so slots count
    at the planner's default capacity; catalogued cartridges use their recorded
    ``capacity_bytes - used_bytes``. Cleaning cartridges and volume-group members
    (already assigned elsewhere) are excluded.
    """
    capacities: dict[str, int] = {}
    if library is not None:
        try:
            for slot in InventoryService(library).snapshot().slots:
                if slot.barcode is not None:
                    capacities[str(slot.barcode)] = ArchivePlanner.DEFAULT_TAPE_CAPACITY
        except Exception as exc:  # noqa: BLE001 - library offline: fall back to the catalog alone
            logger.warning("library inventory unavailable for pool budget", exc_info=exc)
    assigned: set[str] = set()
    for cartridge in repository.list_cartridges():
        barcode = str(cartridge.barcode or "").strip()
        if not barcode:
            continue
        if cartridge.volume_group_id is not None:
            assigned.add(barcode)
        capacities[barcode] = max(0, int(cartridge.capacity_bytes) - int(cartridge.used_bytes))
    return {
        b: capacities[b]
        for b in sorted(capacities)
        if b not in assigned and not b.upper().startswith("CLN")
    }


def count_scratch_tapes(repository: CatalogRepository, library: LibraryBackend) -> int:
    """Blank tapes in library slots: not in any volume group, not catalogued as written.

    The catalog only holds catalogued/archived cartridges, so scratch stock must be
    counted from the inventory snapshot. Cleaning cartridges are excluded.
    """
    excluded = {
        str(c.barcode).strip()
        for c in repository.list_cartridges()
        if c.barcode and (c.volume_group_id is not None or int(c.used_bytes) > 0)
    }
    barcodes = {
        str(slot.barcode)
        for slot in InventoryService(library).snapshot().slots
        if slot.barcode is not None
    }
    return sum(1 for b in barcodes if b not in excluded and not b.upper().startswith("CLN"))


def server_budget_request(
    repository: CatalogRepository, request: ArchivePlanRequest, library: LibraryBackend | None
) -> ArchivePlanRequest:
    """Replace a pool request's capacity inputs with server-derived ones.

    Pool-less requests are returned unchanged (they get no pool budget). Raises
    ``ClientBudgetFieldsError`` if the body supplied any ``SERVER_BUDGET_FIELDS`` and
    ``PoolNotFoundError`` for an unknown pool id.
    """
    if request.pool is None:
        return request
    supplied = [name for name in SERVER_BUDGET_FIELDS if name in request.model_fields_set]
    if supplied:
        raise ClientBudgetFieldsError(
            f"{', '.join(supplied)} cannot be supplied when pool is set; the server derives them"
        )
    if repository.get_nas_pool(request.pool) is None:
        raise PoolNotFoundError(f"Pool {request.pool} not found")
    tapes = server_data_tapes(repository, library)
    return request.model_copy(update={"available_tapes": list(tapes), "tape_capacities": tapes})


def plan_with_pool_budget(
    repository: CatalogRepository,
    request: ArchivePlanRequest,
    *,
    now: datetime,
    planner: ArchivePlanner | None = None,
) -> ArchivePlan:
    """Plan against the pool's reservation budget without booking anything.

    Unexpired ``NasReservation`` rows for ``request.pool`` are the source of truth:
    their sum becomes ``pool_reserved_bytes``. Previews never consume budget; the
    plan's bytes are booked only when ingest starts (``book_plan_capacity``).
    Requests without a pool are planned unchanged.
    """
    planner = planner or ArchivePlanner()
    pool_id = request.pool
    if pool_id is None:
        return planner.plan(request)
    reserved = sum(
        int(cast(int, row["bytes"])) for row in repository.reservations_for_pool(pool_id, now=now)
    )
    return planner.plan(request.model_copy(update={"pool_reserved_bytes": reserved}))


def book_plan_capacity(
    repository: CatalogRepository,
    plan: ArchivePlan,
    library: LibraryBackend | None,
    *,
    now: datetime,
) -> None:
    """Book a pool plan's bytes at ingest start (owner=plan_id, TTL 1h).

    The capacity ceiling is server-derived (``server_data_tapes``), never taken from
    the stored request. Pool-less plans book nothing. Raises ``ReservationError``
    when the atomic booking is refused, so the caller must not create the job.
    """
    pool_id = plan.pool
    if pool_id is None:
        return
    booked = repository.reserve_nas_capacity(
        pool_id,
        plan.total_bytes * plan.copies_required,
        plan.plan_id,
        now + PLAN_RESERVATION_TTL,
        capacity_bytes=sum(server_data_tapes(repository, library).values()),
        now=now,
    )
    if not booked:
        raise ReservationError(f"pool {pool_id} cannot reserve capacity for plan {plan.plan_id}")


class CapacityReservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    pool_id: str = Field(min_length=1)
    bytes: int = Field(gt=0)
    owner: str = Field(min_length=1)
    expires_at: datetime

    def active(self, now: datetime) -> bool:
        return now < self.expires_at


class ReservationLedger:
    """Ledger of capacity reservations per pool.

    Pure in-memory by default; constructed with ``catalog`` it reads and writes
    through the persisted ``nas_reservations`` table so reservations survive restarts.
    """

    def __init__(self, catalog: CatalogRepository | None = None) -> None:
        self._items: list[CapacityReservation] = []
        self._catalog = catalog

    def _active(self, pool_id: str, now: datetime) -> list[CapacityReservation]:
        if self._catalog is None:
            return [r for r in self._items if r.pool_id == pool_id and r.active(now)]
        return [
            CapacityReservation.model_validate(item)
            for item in self._catalog.reservations_for_pool(pool_id, now=now)
        ]

    def reserved(self, pool_id: str, now: datetime) -> int:
        return sum(r.bytes for r in self._active(pool_id, now))

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
        res = CapacityReservation(pool_id=pool_id, bytes=nbytes, owner=owner, expires_at=now + ttl)
        if self._catalog is not None:
            # Check-and-insert is one BEGIN IMMEDIATE transaction in the catalog,
            # so concurrent processes cannot both claim the last bytes.
            if not self._catalog.reserve_nas_capacity(
                pool_id, nbytes, owner, res.expires_at, capacity_bytes=free_bytes, now=now
            ):
                raise ReservationError(f"pool {pool_id} cannot reserve {nbytes} bytes")
            return res
        if nbytes > self.available(pool_id, free_bytes, now):
            raise ReservationError(f"pool {pool_id} cannot reserve {nbytes} bytes")
        self._items.append(res)
        return res

    def release(self, pool_id: str, owner: str) -> int:
        if self._catalog is not None:
            removed = 0
            for item in self._catalog.list_nas_reservations(pool_id):
                if item["owner"] == owner and self._catalog.delete_nas_reservation(str(item["id"])):
                    removed += 1
            return removed
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
