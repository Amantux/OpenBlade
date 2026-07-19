"""Restore dry-run planner for NAS restore jobs."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from math import ceil
from pathlib import PurePosixPath

from openblade.nas.service import NasService
from openblade.nas.types import NasFileRecord, NasFileState, PolicyType, RestorePlanRequest


@dataclass
class RestorePlan:
    job_id: str
    pool_id: str | None
    requested_paths: list[str]
    destination: str
    priority: int
    allow_parallel: bool
    max_drives: int
    required_tapes: list[str] = field(default_factory=list)
    missing_tapes: list[str] = field(default_factory=list)
    exported_tapes: list[str] = field(default_factory=list)
    tape_load_order: list[str] = field(default_factory=list)
    batches_by_tape: dict[str, list[str]] = field(default_factory=dict)
    parallel_restore_groups: list[list[str]] = field(default_factory=list)
    estimated_tape_swaps: int = 0
    estimated_bytes: int = 0
    unavailable_files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    is_safe_to_enqueue: bool = True


class RestorePlanner:
    def __init__(self, service: NasService):
        self.service = service

    def plan(self, request: RestorePlanRequest) -> RestorePlan:
        """Build a dry-run restore plan for the given request."""
        if request.pool_id is None:
            raise ValueError("pool_id is required")
        if self.service.get_pool(request.pool_id) is None:
            raise KeyError(f"Pool {request.pool_id} not found")

        requested_paths = [self._normalize_path(path) for path in request.paths]
        records = self._resolve_records(request.pool_id, requested_paths)

        batches_by_tape: dict[str, list[str]] = defaultdict(list)
        required_counts: dict[str, int] = defaultdict(int)
        missing_tapes: set[str] = set()
        exported_tapes: set[str] = set()
        unavailable_files: list[str] = []
        estimated_bytes = 0

        unavailable_states = {
            NasFileState.MISSING_TAPE,
            NasFileState.FAILED,
            NasFileState.CORRUPT,
            NasFileState.EXPORTED,
        }

        for record in records:
            logical_path = self._normalize_path(record.relative_path)
            state = self.service.derive_file_state(record)
            if state in unavailable_states:
                unavailable_files.append(logical_path)
            else:
                estimated_bytes += record.size_bytes

            if state in {NasFileState.OFFLINE_ON_TAPE, NasFileState.ONLINE_CACHED} and record.tape_barcode:
                required_counts[record.tape_barcode] += 1
                batches_by_tape[record.tape_barcode].append(logical_path)
            elif state is NasFileState.MISSING_TAPE:
                missing_tapes.add(record.tape_barcode or "<unknown>")
            elif state is NasFileState.EXPORTED and record.tape_barcode:
                exported_tapes.add(record.tape_barcode)

        tape_load_order = self._build_tape_load_order(request.pool_id, records, required_counts)
        required_tapes = list(tape_load_order)

        if request.allow_parallel and request.max_drives > 1:
            parallel_restore_groups = [
                tape_load_order[index : index + request.max_drives]
                for index in range(0, len(tape_load_order), request.max_drives)
            ]
            drive_count = request.max_drives
        else:
            parallel_restore_groups = [[barcode] for barcode in tape_load_order]
            drive_count = 1

        estimated_tape_swaps = max(ceil(len(required_tapes) / drive_count) - 1, 0)

        warnings: list[str] = []
        missing_list = sorted(missing_tapes)
        exported_list = sorted(exported_tapes)
        unavailable_list = sorted(set(unavailable_files))
        if missing_list:
            warnings.append(
                f"{len(missing_list)} tape(s) required but not available: {', '.join(missing_list)}"
            )
        if exported_list:
            warnings.append(f"{len(exported_list)} tape(s) have been exported: {', '.join(exported_list)}")
        if unavailable_list:
            warnings.append(f"{len(unavailable_list)} file(s) cannot be restored due to unavailable tapes")
        if estimated_tape_swaps > 3:
            warnings.append(
                f"This restore requires {estimated_tape_swaps} tape swaps. Consider restoring in batches."
            )

        return RestorePlan(
            job_id="",
            pool_id=request.pool_id,
            requested_paths=requested_paths,
            destination=request.destination,
            priority=request.priority,
            allow_parallel=request.allow_parallel,
            max_drives=request.max_drives,
            required_tapes=required_tapes,
            missing_tapes=missing_list,
            exported_tapes=exported_list,
            tape_load_order=tape_load_order,
            batches_by_tape=dict(batches_by_tape),
            parallel_restore_groups=parallel_restore_groups,
            estimated_tape_swaps=estimated_tape_swaps,
            estimated_bytes=estimated_bytes,
            unavailable_files=unavailable_list,
            warnings=warnings,
            is_safe_to_enqueue=not missing_list and not exported_list and not unavailable_list,
        )

    def _resolve_records(self, pool_id: str, requested_paths: list[str]) -> list[NasFileRecord]:
        records = self.service.list_pool_file_records(pool_id)
        if not requested_paths:
            return records
        requested_set = set(requested_paths)
        return [
            record
            for record in records
            if self._normalize_path(record.relative_path) in requested_set
        ]

    def _build_tape_load_order(
        self,
        pool_id: str,
        records: list[NasFileRecord],
        required_counts: dict[str, int],
    ) -> list[str]:
        if not required_counts:
            return []

        pool = self.service.get_pool(pool_id)
        if pool is not None and pool.default_policy_id:
            policy = self.service.get_policy(pool.default_policy_id)
            if policy is not None and policy.policy_type is PolicyType.CRITICAL_SEQUENTIAL:
                dataset_ids = {record.dataset_id for record in records if record.tape_barcode in required_counts}
                if len(dataset_ids) == 1:
                    dataset = self.service.get_dataset(next(iter(dataset_ids)))
                    if dataset is not None and dataset.tape_set:
                        ordered = [barcode for barcode in dataset.tape_set if barcode in required_counts]
                        remaining = sorted(barcode for barcode in required_counts if barcode not in ordered)
                        return [*ordered, *remaining]
                return sorted(required_counts)

        return sorted(required_counts, key=lambda barcode: (-required_counts[barcode], barcode))

    def _normalize_path(self, path: str) -> str:
        normalized = str(PurePosixPath("/" + str(path or "").lstrip("/"))).lstrip("/")
        return "" if normalized == "." else normalized
