"""Operational health and readiness checks."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import func, select

from openblade.catalog.models import Cartridge, NasDataset, NasFileRecord, PathMapping
from openblade.catalog.repository import CatalogRepository
from openblade.nas.types import (
    CatalogStatusResponse,
    ComponentHealth,
    HealthResponse,
    HealthStatus,
    LibraryStatusResponse,
    ReadyResponse,
)
from openblade.nas.version import get_version_info

logger = structlog.get_logger(__name__)
_STATUS_PRIORITY = {
    HealthStatus.OK: 0,
    HealthStatus.DEGRADED: 1,
    HealthStatus.UNHEALTHY: 2,
}


def _utcnow_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


class HealthService:
    """Performs component health checks for /healthz and /readyz."""

    def __init__(self, repo: CatalogRepository, library: Any, ltfs: Any) -> None:
        """Inject repository, library, and LTFS dependencies for operational checks."""
        self.repo = repo
        self.library = library
        self.ltfs = ltfs

    def check_health(self) -> HealthResponse:
        """Check database, library, and LTFS health and return a non-raising aggregate response."""
        checked_at = _utcnow_iso()
        components = [
            self._check_database_component(),
            self._check_library_component(),
            self._check_ltfs_component(),
        ]
        status = max(components, key=lambda component: _STATUS_PRIORITY[component.status]).status
        return HealthResponse(
            status=status,
            components=components,
            checked_at=checked_at,
            version=get_version_info()["version"],
        )

    def check_ready(self) -> ReadyResponse:
        """Return ready=True only when the database is reachable and the library is connected."""
        checked_at = _utcnow_iso()
        try:
            database = self._check_database_component()
            library = self._check_library_component()
            reasons: list[str] = []
            if database.status is not HealthStatus.OK:
                reasons.append("database unavailable")
            if library.status is not HealthStatus.OK:
                reasons.append("library unavailable")
            return ReadyResponse(ready=not reasons, reason="; ".join(reasons), checked_at=checked_at)
        except Exception:
            logger.warning("readiness check failed", exc_info=True)
            return ReadyResponse(ready=False, reason="dependency check unavailable", checked_at=checked_at)

    def get_library_status(self) -> LibraryStatusResponse:
        """Return library connection and slot/drive occupancy derived from the simulator inventory."""
        checked_at = _utcnow_iso()
        try:
            inventory = self.library.inventory()
            drives = [
                {
                    "drive_id": drive.drive_id,
                    "barcode": str(drive.barcode) if drive.barcode is not None else None,
                    "drive_state": drive.drive_state.value,
                    "mount_state": drive.mount_state.value,
                    "loaded": drive.barcode is not None,
                }
                for drive in inventory.drives
            ]
            return LibraryStatusResponse(
                library_connected=True,
                drives=drives,
                slots_total=len(inventory.slots),
                slots_occupied=sum(1 for slot in inventory.slots if slot.occupied),
                cartridges_loaded=sum(1 for drive in inventory.drives if drive.barcode is not None),
                last_updated_at=checked_at,
            )
        except Exception:
            logger.warning("library status check failed", exc_info=True)
            return LibraryStatusResponse(
                library_connected=False,
                drives=[],
                slots_total=0,
                slots_occupied=0,
                cartridges_loaded=0,
                last_updated_at=checked_at,
            )

    def get_catalog_status(self) -> CatalogStatusResponse:
        """Return database reachability, table counts, and the latest catalog rebuild summary."""
        checked_at = _utcnow_iso()
        total_datasets = -1
        total_file_records = -1
        total_path_mappings = -1
        total_cartridges = -1
        last_rebuild_run_id = None
        last_rebuild_status = None
        successful_checks = 0

        try:
            total_datasets = int(self.repo.session.execute(select(func.count()).select_from(NasDataset)).scalar_one())
            successful_checks += 1
        except Exception:
            logger.warning("catalog dataset count failed", exc_info=True)

        try:
            total_file_records = int(
                self.repo.session.execute(select(func.count()).select_from(NasFileRecord)).scalar_one()
            )
            successful_checks += 1
        except Exception:
            logger.warning("catalog file record count failed", exc_info=True)

        try:
            total_path_mappings = int(
                self.repo.session.execute(select(func.count()).select_from(PathMapping)).scalar_one()
            )
            successful_checks += 1
        except Exception:
            logger.warning("catalog path mapping count failed", exc_info=True)

        try:
            total_cartridges = int(self.repo.session.execute(select(func.count()).select_from(Cartridge)).scalar_one())
            successful_checks += 1
        except Exception:
            logger.warning("catalog cartridge count failed", exc_info=True)

        try:
            latest_rebuild = self.repo.list_rebuild_runs(limit=1)
            latest_rebuild_run = latest_rebuild[0] if latest_rebuild else None
            last_rebuild_run_id = str(latest_rebuild_run["id"]) if latest_rebuild_run is not None else None
            last_rebuild_status = str(latest_rebuild_run["status"]) if latest_rebuild_run is not None else None
            successful_checks += 1
        except Exception:
            logger.warning("catalog rebuild lookup failed", exc_info=True)

        return CatalogStatusResponse(
            db_reachable=successful_checks > 0,
            total_datasets=total_datasets,
            total_file_records=total_file_records,
            total_path_mappings=total_path_mappings,
            total_cartridges=total_cartridges,
            last_rebuild_run_id=last_rebuild_run_id,
            last_rebuild_status=last_rebuild_status,
            checked_at=checked_at,
        )

    def _check_database_component(self) -> ComponentHealth:
        """Probe representative catalog tables and downgrade health when only part of the DB is readable."""
        checked_at = _utcnow_iso()
        started_at = time.perf_counter()
        probes = {
            "datasets": lambda: self.repo.list_nas_datasets(),
            "path_mappings": lambda: self.repo.count_path_mappings(),
            "cartridges": lambda: self.repo.list_cartridges(),
            "rebuild_runs": lambda: self.repo.list_rebuild_runs(limit=1),
        }
        failed_probes: list[str] = []

        for probe_name, probe in probes.items():
            try:
                probe()
            except Exception:
                failed_probes.append(probe_name)
                logger.warning("database health probe failed", probe=probe_name, exc_info=True)

        if not failed_probes:
            status = HealthStatus.OK
            message = "Database reachable."
        elif len(failed_probes) == len(probes):
            status = HealthStatus.UNHEALTHY
            message = "Database unreachable."
        else:
            status = HealthStatus.DEGRADED
            message = f"Database partially readable; failed probes: {', '.join(failed_probes)}."

        return ComponentHealth(
            name="database",
            status=status,
            message=message,
            latency_ms=round((time.perf_counter() - started_at) * 1000, 3),
            last_checked_at=checked_at,
        )

    def _check_library_component(self) -> ComponentHealth:
        """Check whether the library can report inventory and whether any drives are currently visible."""
        checked_at = _utcnow_iso()
        started_at = time.perf_counter()
        try:
            inventory = self.library.inventory() if hasattr(self.library, "inventory") else None
            drive_count = len(inventory.drives) if inventory is not None else len(self.library.drives)
            status = HealthStatus.OK if drive_count > 0 else HealthStatus.DEGRADED
            message = "Library connected." if drive_count > 0 else "Library connected but no drives available."
            return ComponentHealth(
                name="library",
                status=status,
                message=message,
                latency_ms=round((time.perf_counter() - started_at) * 1000, 3),
                last_checked_at=checked_at,
            )
        except Exception:
            logger.warning("library health check failed", exc_info=True)
            return ComponentHealth(
                name="library",
                status=HealthStatus.UNHEALTHY,
                message="Library unavailable.",
                latency_ms=round((time.perf_counter() - started_at) * 1000, 3),
                last_checked_at=checked_at,
            )

    def _check_ltfs_component(self) -> ComponentHealth:
        """Check whether the LTFS backend is reachable or at least partially configured."""
        checked_at = _utcnow_iso()
        started_at = time.perf_counter()
        try:
            if hasattr(self.ltfs, "list_tapes"):
                self.ltfs.list_tapes()
                status = HealthStatus.OK
                message = "LTFS backend reachable."
            elif getattr(self.ltfs, "backend", None) is not None or hasattr(self.ltfs, "_tapes"):
                status = HealthStatus.OK
                message = "LTFS backend available."
            else:
                status = HealthStatus.DEGRADED
                message = "LTFS backend not fully configured."
            return ComponentHealth(
                name="ltfs",
                status=status,
                message=message,
                latency_ms=round((time.perf_counter() - started_at) * 1000, 3),
                last_checked_at=checked_at,
            )
        except Exception:
            logger.warning("ltfs health check failed", exc_info=True)
            return ComponentHealth(
                name="ltfs",
                status=HealthStatus.UNHEALTHY,
                message="LTFS unavailable.",
                latency_ms=round((time.perf_counter() - started_at) * 1000, 3),
                last_checked_at=checked_at,
            )
