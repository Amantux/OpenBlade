"""Catalog rebuild worker for lost-database recovery flows."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

import structlog

from openblade.catalog.repository import CatalogRepository
from openblade.nas.catalog_rebuild import CatalogRebuildPlanner
from openblade.nas.types import CatalogRebuildRunRecord, RebuildPlanRequest, RebuildRunStatus

logger = structlog.get_logger(__name__)
SAFE_REBUILD_PREFLIGHT_ERROR = "catalog rebuild preflight failed"


def _utcnow_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


class CatalogRebuildWorker:
    """Coordinates rebuild planning and execution for catalog recovery."""

    def __init__(self, repo: CatalogRepository, planner: CatalogRebuildPlanner) -> None:
        """Store immutable worker dependencies."""
        self.repo = repo
        self.planner = planner

    def auto_plan_and_execute(
        self,
        barcodes: list[str],
        triggered_by: str,
        dry_run_first: bool = True,
    ) -> CatalogRebuildRunRecord:
        """Plan and immediately execute a catalog rebuild for the provided barcodes."""
        normalized_barcodes = self._normalize_barcodes(barcodes)
        logger.info(
            "catalog_rebuild_worker_started",
            triggered_by=triggered_by,
            barcode_count=len(normalized_barcodes),
            dry_run_first=dry_run_first,
        )

        if not normalized_barcodes:
            empty_run = self._create_empty_run(triggered_by)
            logger.info(
                "catalog_rebuild_worker_empty_run",
                run_id=empty_run.id,
                triggered_by=triggered_by,
            )
            return empty_run

        if dry_run_first:
            dry_run_result = self.planner.plan_rebuild(
                RebuildPlanRequest(
                    barcodes=normalized_barcodes,
                    triggered_by=triggered_by,
                    dry_run=True,
                )
            )
            logger.info(
                "catalog_rebuild_worker_dry_run_completed",
                triggered_by=triggered_by,
                barcode_count=len(normalized_barcodes),
                safe_to_enqueue=dry_run_result.safe_to_enqueue,
                estimated_files=dry_run_result.estimated_files,
                estimated_datasets=dry_run_result.estimated_datasets,
                estimated_path_mappings=dry_run_result.estimated_path_mappings,
            )
            if not dry_run_result.safe_to_enqueue:
                raise ValueError(SAFE_REBUILD_PREFLIGHT_ERROR)

        plan = self.planner.plan_rebuild(
            RebuildPlanRequest(
                barcodes=normalized_barcodes,
                triggered_by=triggered_by,
                dry_run=False,
            )
        )
        logger.info(
            "catalog_rebuild_worker_planned",
            run_id=plan.run_id,
            triggered_by=triggered_by,
            planned_barcodes=len(plan.barcodes_to_scan),
            safe_to_enqueue=plan.safe_to_enqueue,
        )

        final_run = self.planner.execute_rebuild_run(plan.run_id)
        logger.info(
            "catalog_rebuild_worker_completed",
            run_id=final_run.id,
            triggered_by=triggered_by,
            status=final_run.status.value,
            files_recovered=final_run.files_recovered,
            datasets_recovered=final_run.datasets_recovered,
            path_mappings_recovered=final_run.path_mappings_recovered,
            completed_barcodes=len(final_run.barcodes_completed),
            failed_barcodes=len(final_run.barcodes_failed),
        )
        return final_run

    def recover_from_loaded_tapes(self, triggered_by: str) -> CatalogRebuildRunRecord:
        """Recover the catalog from all barcodes returned by the library inventory repository."""
        loaded_barcodes = self._extract_barcodes(self.repo.list_cartridges())
        logger.info(
            "catalog_rebuild_worker_loaded_tapes_discovered",
            triggered_by=triggered_by,
            barcode_count=len(loaded_barcodes),
        )
        return self.auto_plan_and_execute(loaded_barcodes, triggered_by=triggered_by)

    def rebuild_status(self, run_id: str) -> CatalogRebuildRunRecord | None:
        """Return the stored rebuild run record for ``run_id`` when it exists."""
        run = self.repo.get_rebuild_run(run_id)
        if run is None:
            return None
        return CatalogRebuildRunRecord.model_validate(run)

    @staticmethod
    def _normalize_barcodes(barcodes: list[str]) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        for barcode in barcodes:
            value = str(barcode).strip()
            if not value or value in seen:
                continue
            seen.add(value)
            normalized.append(value)
        return normalized

    def _create_empty_run(self, triggered_by: str) -> CatalogRebuildRunRecord:
        now = _utcnow_iso()
        record = CatalogRebuildRunRecord(
            id=str(uuid4()),
            status=RebuildRunStatus.COMPLETED,
            triggered_by=triggered_by,
            barcodes_planned=[],
            barcodes_completed=[],
            barcodes_failed=[],
            barcodes_skipped=[],
            files_recovered=0,
            datasets_recovered=0,
            path_mappings_recovered=0,
            error_summary=[],
            created_at=now,
            updated_at=now,
            completed_at=now,
        )
        self.repo.create_rebuild_run(record.model_dump(mode="json"))
        return record

    def _extract_barcodes(self, entries: list[Any]) -> list[str]:
        barcodes: list[str] = []
        for entry in entries:
            barcode = self._extract_barcode(entry)
            if barcode:
                barcodes.append(barcode)
        return self._normalize_barcodes(barcodes)

    @staticmethod
    def _extract_barcode(entry: Any) -> str:
        if isinstance(entry, dict):
            value = entry.get("barcode", "")
        else:
            value = getattr(entry, "barcode", "")
        return str(value).strip()
