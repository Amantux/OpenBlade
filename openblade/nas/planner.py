"""Archive dry-run planner. Produces ArchivePlan without touching tapes."""

from __future__ import annotations

import posixpath
from datetime import datetime, timezone
from typing import Optional

from openblade.nas.types import (
    ArchivePlan,
    ArchivePlanRequest,
    ArchivePlanWarning,
    IngestMode,
    PolicyType,
    ShardStrategy,
    TapeAssignment,
)


class ArchivePlanner:
    DEFAULT_TAPE_CAPACITY = 12_000_000_000_000
    RESTORE_THROUGHPUT_BYTES_PER_SECOND = 300_000_000
    RESTORE_TIME_SHARD_THRESHOLD_SECONDS = 8 * 60 * 60
    balanced_shard_file_threshold: int = 50_000

    def plan(self, request: ArchivePlanRequest) -> ArchivePlan:
        """Compute a dry-run archive plan for the given request."""
        files = sorted(request.files)
        plan = ArchivePlan(
            policy_name=request.policy_id,
            policy_type=request.policy_type,
            ingest_mode=request.ingest_mode,
            source_path=request.source_path,
            pool=request.pool,
            volume_group=request.volume_group,
            files=files,
            total_files=len(files),
            total_bytes=sum(self._file_size(request, path) for path in files),
            copies_required=request.copies,
            verify_before_archive=request.verify_before_archive,
            verify_after_archive=request.verify_after_archive,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        self._apply_common_checks(request, plan)
        if not plan.is_safe_to_enqueue:
            return plan

        policy_type = request.policy_type or PolicyType.BALANCED
        if policy_type is PolicyType.CRITICAL_SEQUENTIAL:
            self._plan_critical_sequential(request, plan)
        elif policy_type is PolicyType.NONCRITICAL_SHARDED:
            self._plan_noncritical_sharded(request, plan)
        else:
            self._plan_balanced(request, plan)

        return plan

    def _plan_critical_sequential(self, request: ArchivePlanRequest, plan: ArchivePlan) -> None:
        tapes = self._ordered_tapes(request)
        single_tape_capacity = self._single_tape_capacity(request)

        if plan.total_bytes > int(single_tape_capacity * 0.8):
            self._add_capacity_warning(
                plan,
                "Dataset exceeds 80% of a single tape capacity",
                field="total_bytes",
            )

        if request.ingest_mode is IngestMode.SOURCE_STREAM:
            self._add_safety_warning(
                plan,
                "Source-stream mode requires source files to remain stable and online. For critical datasets, cache-drive mode is safer unless the source supports snapshots.",
                field="ingest_mode",
            )

        fitting_tape = next((barcode for barcode in tapes if self._tape_capacity(request, barcode) >= plan.total_bytes), None)
        if fitting_tape is not None:
            plan.tape_assignments = [
                TapeAssignment(
                    barcode=fitting_tape,
                    files=self._relative_files(plan.files, request.source_path),
                    estimated_bytes=plan.total_bytes,
                )
            ]
            if self._is_scratch_tape(fitting_tape):
                self._add_capacity_warning(
                    plan,
                    f"Using scratch tape {fitting_tape} because no preferred tape had enough capacity",
                    field="available_tapes",
                )
            plan.estimated_tape_swaps = 0
            plan.is_safe_to_enqueue = len(plan.enqueue_blockers) == 0
            return

        groups = self._directory_groups(plan.files)
        assignments: list[TapeAssignment] = []
        tape_index = 0

        for group in groups:
            group_bytes = sum(self._file_size(request, path) for path in group)
            while True:
                if tape_index >= len(tapes):
                    self._block_plan(plan, "Insufficient tape capacity for all files")
                    plan.tape_assignments = assignments
                    return

                barcode = tapes[tape_index]
                capacity = self._tape_capacity(request, barcode)
                assignment = assignments[tape_index] if tape_index < len(assignments) else None
                used_bytes = assignment.estimated_bytes if assignment is not None else 0
                remaining_bytes = capacity - used_bytes

                if assignment is not None and group_bytes > remaining_bytes and group_bytes <= capacity:
                    tape_index += 1
                    continue

                if assignment is None:
                    assignment = TapeAssignment(barcode=barcode, files=[], estimated_bytes=0)
                    assignments.append(assignment)
                    if self._is_scratch_tape(barcode):
                        self._add_capacity_warning(
                            plan,
                            f"Using scratch tape {barcode} because no preferred tape was available",
                            field="available_tapes",
                        )

                if group_bytes <= remaining_bytes:
                    assignment.files.extend(self._relative_files(group, request.source_path))
                    assignment.estimated_bytes += group_bytes
                    break

                split_index = self._first_split_index(request, group, remaining_bytes)
                if split_index == 0:
                    if assignment.files:
                        tape_index += 1
                        continue
                    self._block_plan(plan, f"File {group[0]} exceeds available tape capacity")
                    plan.tape_assignments = assignments
                    return

                fitting_files = group[:split_index]
                assignment.files.extend(self._relative_files(fitting_files, request.source_path))
                assignment.estimated_bytes += sum(self._file_size(request, path) for path in fitting_files)
                group = group[split_index:]
                group_bytes = sum(self._file_size(request, path) for path in group)
                tape_index += 1

        for index, assignment in enumerate(assignments):
            assignment.is_spillover = index > 0
        plan.tape_assignments = assignments
        plan.estimated_tape_swaps = max(len(assignments) - 1, 0)
        plan.is_safe_to_enqueue = len(plan.enqueue_blockers) == 0

    def _plan_noncritical_sharded(self, request: ArchivePlanRequest, plan: ArchivePlan) -> None:
        strategy = request.shard_strategy or ShardStrategy.ROUND_ROBIN
        tapes = self._ordered_tapes(request)
        remaining = {barcode: self._tape_capacity(request, barcode) for barcode in tapes}
        assignments: dict[str, TapeAssignment] = {}
        tape_positions = {barcode: index for index, barcode in enumerate(tapes)}

        def get_assignment(barcode: str) -> TapeAssignment:
            assignment = assignments.get(barcode)
            if assignment is None:
                assignment = TapeAssignment(
                    barcode=barcode,
                    files=[],
                    estimated_bytes=0,
                    shard_index=tape_positions[barcode],
                )
                assignments[barcode] = assignment
            return assignment

        def assign_file(preferred_tape: str, file_path: str) -> bool:
            size = self._file_size(request, file_path)
            ordered_choices = [preferred_tape, *[tape for tape in tapes if tape != preferred_tape]]
            for barcode in ordered_choices:
                if remaining[barcode] < size:
                    continue
                assignment = get_assignment(barcode)
                assignment.files.append(self._make_relative(file_path, request.source_path))
                assignment.estimated_bytes += size
                remaining[barcode] -= size
                return True
            self._block_plan(plan, f"Insufficient tape capacity for file {file_path}")
            return False

        files = plan.files
        if strategy is ShardStrategy.ROUND_ROBIN:
            for index, file_path in enumerate(files):
                if not assign_file(tapes[index % len(tapes)], file_path):
                    break
        elif strategy is ShardStrategy.CAPACITY_WEIGHTED:
            for file_path in files:
                preferred_tape = max(tapes, key=lambda barcode: (remaining[barcode], -tape_positions[barcode]))
                if not assign_file(preferred_tape, file_path):
                    break
        elif strategy is ShardStrategy.DIRECTORY_BATCH:
            for group in self._directory_groups(files):
                group_bytes = sum(self._file_size(request, path) for path in group)
                preferred_tape = max(tapes, key=lambda barcode: (remaining[barcode], -tape_positions[barcode]))
                if remaining[preferred_tape] >= group_bytes:
                    for file_path in group:
                        if not assign_file(preferred_tape, file_path):
                            break
                else:
                    for file_path in group:
                        preferred_tape = max(tapes, key=lambda barcode: (remaining[barcode], -tape_positions[barcode]))
                        if not assign_file(preferred_tape, file_path):
                            break
                    if plan.enqueue_blockers:
                        break
        elif strategy is ShardStrategy.HASH_PREFIX:
            for file_path in files:
                filename = posixpath.basename(file_path) or file_path
                prefix_value = ord(filename[0].lower()) if filename else 0
                if not assign_file(tapes[prefix_value % len(tapes)], file_path):
                    break
        else:
            tape_loads = {barcode: 0 for barcode in tapes}
            sized_files = sorted(
                files,
                key=lambda path: (self._file_size(request, path), path),
                reverse=True,
            )
            for file_path in sized_files:
                preferred_tape = min(
                    tapes,
                    key=lambda barcode: (tape_loads[barcode], -remaining[barcode], tape_positions[barcode]),
                )
                if not assign_file(preferred_tape, file_path):
                    break
                relative_path = self._make_relative(file_path, request.source_path)
                placed_tape = next(barcode for barcode, assignment in assignments.items() if relative_path in assignment.files)
                tape_loads[placed_tape] += self._file_size(request, file_path)

        ordered_assignments = [assignments[barcode] for barcode in tapes if barcode in assignments]
        plan.tape_assignments = ordered_assignments
        plan.estimated_parallelism = min(len(ordered_assignments), request.max_parallelism)
        plan.estimated_tape_swaps = 0
        plan.is_safe_to_enqueue = len(plan.enqueue_blockers) == 0

    def _plan_balanced(self, request: ArchivePlanRequest, plan: ArchivePlan) -> None:
        single_tape_capacity = self._single_tape_capacity(request)
        estimated_restore_time = plan.total_bytes / self.RESTORE_THROUGHPUT_BYTES_PER_SECOND
        should_shard = False
        reasons: list[str] = []
        size_fit_threshold = int(single_tape_capacity * 0.85)

        if plan.total_bytes > size_fit_threshold:
            should_shard = True
            reasons.append("dataset size exceeds 85% of single tape capacity")
            if plan.total_files > self.balanced_shard_file_threshold:
                reasons.append(f"file count exceeds {self.balanced_shard_file_threshold}")
            if estimated_restore_time > self.RESTORE_TIME_SHARD_THRESHOLD_SECONDS:
                reasons.append("estimated single-tape restore time exceeds threshold")
            if plan.total_bytes > single_tape_capacity:
                reasons.append("dataset does not fit on one tape")

        if should_shard:
            for reason in reasons:
                self._add_capacity_warning(plan, f"Balanced planning switched to sharded mode: {reason}")
            balanced_request = request.model_copy(
                update={
                    "policy_type": PolicyType.NONCRITICAL_SHARDED,
                    "shard_strategy": request.shard_strategy or ShardStrategy.DIRECTORY_BATCH,
                }
            )
            self._plan_noncritical_sharded(balanced_request, plan)
            return

        critical_request = request.model_copy(update={"policy_type": PolicyType.CRITICAL_SEQUENTIAL})
        self._plan_critical_sequential(critical_request, plan)

    def _apply_common_checks(self, request: ArchivePlanRequest, plan: ArchivePlan) -> None:
        if not request.files:
            self._block_plan(plan, "No files to archive")
        if not request.available_tapes:
            self._block_plan(plan, "No tapes available")
        if plan.copies_required > len(request.available_tapes):
            self._add_capacity_warning(
                plan,
                "Requested copies exceed number of available tapes",
                field="copies",
            )

    def _ordered_tapes(self, request: ArchivePlanRequest) -> list[str]:
        preferred = [barcode for barcode in request.available_tapes if not self._is_scratch_tape(barcode)]
        scratch = [barcode for barcode in request.available_tapes if self._is_scratch_tape(barcode)]
        return preferred + scratch

    def _single_tape_capacity(self, request: ArchivePlanRequest) -> int:
        if request.available_tapes:
            return max(self._tape_capacity(request, barcode) for barcode in request.available_tapes)
        return self.DEFAULT_TAPE_CAPACITY

    def _tape_capacity(self, request: ArchivePlanRequest, barcode: str) -> int:
        return request.tape_capacities.get(barcode, self.DEFAULT_TAPE_CAPACITY)

    def _file_size(self, request: ArchivePlanRequest, path: str) -> int:
        return request.file_sizes.get(path, 0)

    def _directory_groups(self, files: list[str]) -> list[list[str]]:
        groups: list[list[str]] = []
        current_directory: str | None = None
        current_group: list[str] = []
        for file_path in files:
            directory = posixpath.dirname(file_path)
            if current_directory is None or directory == current_directory:
                current_group.append(file_path)
                current_directory = directory
                continue
            groups.append(current_group)
            current_group = [file_path]
            current_directory = directory
        if current_group:
            groups.append(current_group)
        return groups

    def _first_split_index(self, request: ArchivePlanRequest, files: list[str], capacity: int) -> int:
        consumed = 0
        for index, file_path in enumerate(files):
            size = self._file_size(request, file_path)
            if consumed + size > capacity:
                return index
            consumed += size
        return len(files)

    def _make_relative(self, filepath: str, source_path: Optional[str]) -> str:
        if source_path and filepath.startswith(source_path):
            rel = filepath[len(source_path):]
            return rel.lstrip("/")
        return filepath.lstrip("/")

    def _relative_files(self, files: list[str], source_path: Optional[str]) -> list[str]:
        return [self._make_relative(file_path, source_path) for file_path in files]

    def _is_scratch_tape(self, barcode: str) -> bool:
        normalized = barcode.upper()
        return normalized.startswith("SCR") or "SCRATCH" in normalized

    def _add_capacity_warning(self, plan: ArchivePlan, message: str, field: str | None = None) -> None:
        plan.capacity_warnings.append(ArchivePlanWarning(level="warning", message=message, field=field))

    def _add_safety_warning(self, plan: ArchivePlan, message: str, field: str | None = None) -> None:
        plan.safety_warnings.append(ArchivePlanWarning(level="warning", message=message, field=field))

    def _block_plan(self, plan: ArchivePlan, message: str) -> None:
        if message not in plan.enqueue_blockers:
            plan.enqueue_blockers.append(message)
        plan.is_safe_to_enqueue = False
