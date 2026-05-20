from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig
from openblade.nas.planner import ArchivePlanner
from openblade.nas.types import ArchivePlanRequest, IngestMode, PolicyType, ShardStrategy

client = TestClient(app)

TB = 1_000_000_000_000
GB = 1_000_000_000


@pytest.fixture(autouse=True)
def reset_app_context(tmp_path: Path) -> None:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'nas-planner.db'}"))
    reset_context(context)


def test_critical_sequential_fits_one_tape() -> None:
    plan = ArchivePlanner().plan(
        ArchivePlanRequest(
            policy_type=PolicyType.CRITICAL_SEQUENTIAL,
            files=["dataset/c.bin", "dataset/a.bin", "dataset/b.bin"],
            file_sizes={
                "dataset/a.bin": 400 * GB,
                "dataset/b.bin": 300 * GB,
                "dataset/c.bin": 300 * GB,
            },
            available_tapes=["TAPE001"],
            tape_capacities={"TAPE001": 12 * TB},
        )
    )

    assert plan.is_safe_to_enqueue is True
    assert len(plan.tape_assignments) == 1
    assert plan.tape_assignments[0].files == ["dataset/a.bin", "dataset/b.bin", "dataset/c.bin"]
    assert plan.estimated_tape_swaps == 0


def test_critical_sequential_spillover() -> None:
    plan = ArchivePlanner().plan(
        ArchivePlanRequest(
            policy_type=PolicyType.CRITICAL_SEQUENTIAL,
            files=["dataset/a.bin", "dataset/b.bin", "dataset/c.bin"],
            file_sizes={path: 5 * TB for path in ["dataset/a.bin", "dataset/b.bin", "dataset/c.bin"]},
            available_tapes=["TAPE001", "TAPE002"],
            tape_capacities={"TAPE001": 12 * TB, "TAPE002": 12 * TB},
        )
    )

    assert plan.is_safe_to_enqueue is True
    assert len(plan.tape_assignments) == 2
    assert plan.estimated_tape_swaps == 1
    assert plan.tape_assignments[0].estimated_bytes == 10 * TB
    assert plan.tape_assignments[1].estimated_bytes == 5 * TB
    assert plan.tape_assignments[1].is_spillover is True


def test_critical_sequential_no_tapes_blocks() -> None:
    plan = ArchivePlanner().plan(
        ArchivePlanRequest(
            policy_type=PolicyType.CRITICAL_SEQUENTIAL,
            files=["dataset/a.bin"],
            file_sizes={"dataset/a.bin": 1},
        )
    )

    assert plan.is_safe_to_enqueue is False
    assert "No tapes available" in plan.enqueue_blockers


def test_critical_sequential_empty_files_blocks() -> None:
    plan = ArchivePlanner().plan(
        ArchivePlanRequest(
            policy_type=PolicyType.CRITICAL_SEQUENTIAL,
            available_tapes=["TAPE001"],
        )
    )

    assert plan.is_safe_to_enqueue is False
    assert "No files to archive" in plan.enqueue_blockers


def test_noncritical_round_robin() -> None:
    plan = ArchivePlanner().plan(
        ArchivePlanRequest(
            policy_type=PolicyType.NONCRITICAL_SHARDED,
            shard_strategy=ShardStrategy.ROUND_ROBIN,
            files=[f"dataset/file-{index}.bin" for index in range(6)],
            file_sizes={f"dataset/file-{index}.bin": GB for index in range(6)},
            available_tapes=["TAPE001", "TAPE002"],
            tape_capacities={"TAPE001": 12 * TB, "TAPE002": 12 * TB},
            max_parallelism=4,
        )
    )

    assert len(plan.tape_assignments) == 2
    assert [len(assignment.files) for assignment in plan.tape_assignments] == [3, 3]
    assert plan.estimated_parallelism == 2
    assert plan.estimated_tape_swaps == 0


def test_noncritical_directory_batch() -> None:
    files = [
        "photos/a/001.jpg",
        "photos/a/002.jpg",
        "photos/b/001.jpg",
        "photos/b/002.jpg",
    ]
    plan = ArchivePlanner().plan(
        ArchivePlanRequest(
            policy_type=PolicyType.NONCRITICAL_SHARDED,
            shard_strategy=ShardStrategy.DIRECTORY_BATCH,
            files=files,
            file_sizes={path: GB for path in files},
            available_tapes=["TAPE001", "TAPE002"],
            tape_capacities={"TAPE001": 12 * TB, "TAPE002": 12 * TB},
            max_parallelism=2,
        )
    )

    assignment_dirs = [{Path(file_path).parent.as_posix() for file_path in assignment.files} for assignment in plan.tape_assignments]

    assert len(plan.tape_assignments) == 2
    assert assignment_dirs == [{"photos/a"}, {"photos/b"}]


def test_noncritical_restore_parallelism_optimized() -> None:
    files = ["a.bin", "b.bin", "c.bin", "d.bin"]
    sizes = {"a.bin": 8 * GB, "b.bin": 7 * GB, "c.bin": 4 * GB, "d.bin": 1 * GB}
    plan = ArchivePlanner().plan(
        ArchivePlanRequest(
            policy_type=PolicyType.NONCRITICAL_SHARDED,
            shard_strategy=ShardStrategy.RESTORE_PARALLELISM_OPTIMIZED,
            files=files,
            file_sizes=sizes,
            available_tapes=["TAPE001", "TAPE002"],
            tape_capacities={"TAPE001": 12 * TB, "TAPE002": 12 * TB},
            max_parallelism=2,
        )
    )

    assigned_sizes = [assignment.estimated_bytes for assignment in plan.tape_assignments]

    assert len(plan.tape_assignments) == 2
    assert abs(assigned_sizes[0] - assigned_sizes[1]) <= 2 * GB


def test_balanced_small_dataset_uses_one_tape() -> None:
    plan = ArchivePlanner().plan(
        ArchivePlanRequest(
            policy_type=PolicyType.BALANCED,
            files=["dataset/a.bin"],
            file_sizes={"dataset/a.bin": 100 * 1_000_000},
            available_tapes=["TAPE001", "TAPE002"],
            tape_capacities={"TAPE001": 12 * TB, "TAPE002": 12 * TB},
        )
    )

    assert len(plan.tape_assignments) == 1
    assert plan.estimated_tape_swaps == 0
    assert plan.estimated_parallelism == 1


def test_balanced_large_dataset_shards() -> None:
    files = [
        "dir-a/a.bin",
        "dir-a/b.bin",
        "dir-b/c.bin",
        "dir-b/d.bin",
    ]
    plan = ArchivePlanner().plan(
        ArchivePlanRequest(
            policy_type=PolicyType.BALANCED,
            files=files,
            file_sizes={path: 5 * TB for path in files},
            available_tapes=["TAPE001", "TAPE002"],
            tape_capacities={"TAPE001": 12 * TB, "TAPE002": 12 * TB},
            max_parallelism=2,
        )
    )

    assert len(plan.tape_assignments) == 2
    assert plan.estimated_parallelism == 2
    assert any("sharded mode" in warning.message for warning in plan.capacity_warnings)


def test_plan_endpoint_returns_archive_plan() -> None:
    response = client.post(
        "/nas/archive-plan",
        json={
            "policy_id": "balanced",
            "files": ["dataset/a.bin"],
            "file_sizes": {"dataset/a.bin": 1024},
            "available_tapes": ["TAPE001"],
        },
    )

    body = response.json()

    assert response.status_code == 200
    assert body["policy_name"] == "Balanced"
    assert body["policy_type"] == "balanced"
    assert body["total_files"] == 1
    assert len(body["tape_assignments"]) == 1
    assert body["plan_id"]


def test_source_stream_critical_adds_safety_warning() -> None:
    plan = ArchivePlanner().plan(
        ArchivePlanRequest(
            policy_type=PolicyType.CRITICAL_SEQUENTIAL,
            ingest_mode=IngestMode.SOURCE_STREAM,
            copies=2,
            files=["dataset/a.bin"],
            file_sizes={"dataset/a.bin": GB},
            available_tapes=["TAPE001", "TAPE002"],
            tape_capacities={"TAPE001": 12 * TB, "TAPE002": 12 * TB},
        )
    )

    assert any("Source-stream mode requires source files to remain stable and online" in warning.message for warning in plan.safety_warnings)


def test_source_stream_critical_always_warns() -> None:
    plan = ArchivePlanner().plan(
        ArchivePlanRequest(
            policy_type=PolicyType.CRITICAL_SEQUENTIAL,
            ingest_mode=IngestMode.SOURCE_STREAM,
            copies=1,
            files=["dataset/a.bin"],
            file_sizes={"dataset/a.bin": GB},
            available_tapes=["TAPE001"],
            tape_capacities={"TAPE001": 12 * TB},
        )
    )

    assert any("Source-stream mode requires source files to remain stable and online" in warning.message for warning in plan.safety_warnings)


def test_relative_paths_in_assignments() -> None:
    plan = ArchivePlanner().plan(
        ArchivePlanRequest(
            policy_type=PolicyType.CRITICAL_SEQUENTIAL,
            source_path="/data/project",
            files=["/data/project/a/b.txt", "/data/project/c.txt"],
            file_sizes={"/data/project/a/b.txt": GB, "/data/project/c.txt": GB},
            available_tapes=["TAPE001"],
            tape_capacities={"TAPE001": 12 * TB},
        )
    )

    assert plan.tape_assignments[0].files == ["a/b.txt", "c.txt"]


def test_is_safe_only_blocked_by_blockers() -> None:
    plan = ArchivePlanner().plan(
        ArchivePlanRequest(
            policy_type=PolicyType.CRITICAL_SEQUENTIAL,
            copies=2,
            files=["dataset/a.bin"],
            file_sizes={"dataset/a.bin": GB},
            available_tapes=["TAPE001"],
            tape_capacities={"TAPE001": 12 * TB},
        )
    )

    assert plan.capacity_warnings
    assert plan.enqueue_blockers == []
    assert plan.is_safe_to_enqueue is True


def test_balanced_shards_on_size_not_file_count() -> None:
    files = [f"dataset/file-{index:03}.bin" for index in range(100)]
    file_size = 200 * GB
    plan = ArchivePlanner().plan(
        ArchivePlanRequest(
            policy_type=PolicyType.BALANCED,
            files=files,
            file_sizes={path: file_size for path in files},
            available_tapes=["TAPE001", "TAPE002"],
            tape_capacities={"TAPE001": 12 * TB, "TAPE002": 12 * TB},
            max_parallelism=2,
        )
    )

    assert len(plan.tape_assignments) == 2
    assert plan.estimated_parallelism == 2
    assert any("dataset size exceeds 85%" in warning.message for warning in plan.capacity_warnings)


def test_balanced_small_many_files_no_shard() -> None:
    files = [f"dataset/file-{index:04}.bin" for index in range(1000)]
    file_size = 100_000
    plan = ArchivePlanner().plan(
        ArchivePlanRequest(
            policy_type=PolicyType.BALANCED,
            files=files,
            file_sizes={path: file_size for path in files},
            available_tapes=["TAPE001", "TAPE002"],
            tape_capacities={"TAPE001": 12 * TB, "TAPE002": 12 * TB},
            max_parallelism=2,
        )
    )

    assert len(plan.tape_assignments) == 1
    assert plan.estimated_parallelism == 1
    assert not any("sharded mode" in warning.message for warning in plan.capacity_warnings)
