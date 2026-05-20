from pathlib import Path

from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig
from openblade.nas.restore_planner import RestorePlanner
from openblade.nas.service import NasService
from openblade.nas.types import NasDataset, NasFileRecord, NasFileState, NasPool, RestorePlanRequest


def make_nas_service(tmp_path: Path) -> NasService:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'nas-restore-planner.db'}"))
    reset_context(context)
    return NasService(context.catalog)


def seed_pool(service: NasService, *, pool_id: str = "pool-1") -> NasPool:
    return service.upsert_pool(NasPool(id=pool_id, name="Pool One"))


def seed_dataset(service: NasService, *, pool_id: str = "pool-1", dataset_id: str = "dataset-1") -> NasDataset:
    return service.upsert_dataset(NasDataset(id=dataset_id, pool_id=pool_id, name=f"dataset-{dataset_id}"))


def seed_file(
    service: NasService,
    *,
    dataset_id: str,
    pool_id: str = "pool-1",
    relative_path: str,
    status: NasFileState = NasFileState.OFFLINE_ON_TAPE,
    tape_barcode: str | None = "VOL001L9",
    size_bytes: int = 10,
) -> NasFileRecord:
    return service.upsert_file_record(
        NasFileRecord(
            dataset_id=dataset_id,
            pool_id=pool_id,
            relative_path=relative_path,
            size_bytes=size_bytes,
            tape_barcode=tape_barcode,
            status=status,
        )
    )


def make_planner(service: NasService) -> RestorePlanner:
    return RestorePlanner(service)


def test_plan_with_no_files_returns_empty_safe_plan(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = seed_pool(service)

    plan = make_planner(service).plan(RestorePlanRequest(pool_id=pool.id))

    assert plan.required_tapes == []
    assert plan.batches_by_tape == {}
    assert plan.parallel_restore_groups == []
    assert plan.estimated_bytes == 0
    assert plan.is_safe_to_enqueue is True


def test_plan_with_single_file_on_one_tape(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = seed_pool(service)
    dataset = seed_dataset(service, pool_id=pool.id)
    seed_file(service, dataset_id=dataset.id, pool_id=pool.id, relative_path="photos/a.jpg", tape_barcode="VOL001L9")

    plan = make_planner(service).plan(RestorePlanRequest(pool_id=pool.id))

    assert plan.required_tapes == ["VOL001L9"]
    assert plan.batches_by_tape == {"VOL001L9": ["photos/a.jpg"]}


def test_plan_with_multiple_tapes_sorts_by_file_count_desc(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = seed_pool(service)
    dataset = seed_dataset(service, pool_id=pool.id)
    seed_file(service, dataset_id=dataset.id, pool_id=pool.id, relative_path="a.txt", tape_barcode="VOL002L9")
    seed_file(service, dataset_id=dataset.id, pool_id=pool.id, relative_path="b.txt", tape_barcode="VOL001L9")
    seed_file(service, dataset_id=dataset.id, pool_id=pool.id, relative_path="c.txt", tape_barcode="VOL001L9")

    plan = make_planner(service).plan(RestorePlanRequest(pool_id=pool.id))

    assert plan.tape_load_order == ["VOL001L9", "VOL002L9"]


def test_plan_with_missing_tape_marks_plan_unsafe(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = seed_pool(service)
    dataset = seed_dataset(service, pool_id=pool.id)
    seed_file(
        service,
        dataset_id=dataset.id,
        pool_id=pool.id,
        relative_path="missing.bin",
        tape_barcode=None,
        status=NasFileState.OFFLINE_ON_TAPE,
    )

    plan = make_planner(service).plan(RestorePlanRequest(pool_id=pool.id))

    assert plan.missing_tapes == ["<unknown>"]
    assert plan.unavailable_files == ["missing.bin"]
    assert plan.is_safe_to_enqueue is False


def test_plan_with_exported_file_tracks_exported_tapes(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = seed_pool(service)
    dataset = seed_dataset(service, pool_id=pool.id)
    seed_file(
        service,
        dataset_id=dataset.id,
        pool_id=pool.id,
        relative_path="exported.bin",
        tape_barcode="VOL009L9",
        status=NasFileState.EXPORTED,
    )

    plan = make_planner(service).plan(RestorePlanRequest(pool_id=pool.id))

    assert plan.exported_tapes == ["VOL009L9"]
    assert plan.unavailable_files == ["exported.bin"]


def test_plan_with_specific_paths_only_includes_requested_subset(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = seed_pool(service)
    dataset = seed_dataset(service, pool_id=pool.id)
    seed_file(service, dataset_id=dataset.id, pool_id=pool.id, relative_path="photos/a.jpg", tape_barcode="VOL001L9")
    seed_file(service, dataset_id=dataset.id, pool_id=pool.id, relative_path="photos/b.jpg", tape_barcode="VOL002L9")

    plan = make_planner(service).plan(RestorePlanRequest(pool_id=pool.id, paths=["photos/b.jpg"]))

    assert plan.requested_paths == ["photos/b.jpg"]
    assert plan.required_tapes == ["VOL002L9"]
    assert plan.batches_by_tape == {"VOL002L9": ["photos/b.jpg"]}


def test_parallel_groups_chunk_tapes_when_parallel_enabled(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = seed_pool(service)
    dataset = seed_dataset(service, pool_id=pool.id)
    for index in range(4):
        seed_file(
            service,
            dataset_id=dataset.id,
            pool_id=pool.id,
            relative_path=f"file-{index}.bin",
            tape_barcode=f"VOL00{index}L9",
        )

    plan = make_planner(service).plan(RestorePlanRequest(pool_id=pool.id, max_drives=2, allow_parallel=True))

    assert plan.parallel_restore_groups == [["VOL000L9", "VOL001L9"], ["VOL002L9", "VOL003L9"]]


def test_parallel_groups_are_singletons_when_parallel_disabled(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = seed_pool(service)
    dataset = seed_dataset(service, pool_id=pool.id)
    for index in range(3):
        seed_file(
            service,
            dataset_id=dataset.id,
            pool_id=pool.id,
            relative_path=f"file-{index}.bin",
            tape_barcode=f"VOL00{index}L9",
        )

    plan = make_planner(service).plan(RestorePlanRequest(pool_id=pool.id, max_drives=2, allow_parallel=False))

    assert plan.parallel_restore_groups == [["VOL000L9"], ["VOL001L9"], ["VOL002L9"]]


def test_estimated_tape_swaps_uses_drive_count(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = seed_pool(service)
    dataset = seed_dataset(service, pool_id=pool.id)
    for index in range(4):
        seed_file(
            service,
            dataset_id=dataset.id,
            pool_id=pool.id,
            relative_path=f"swap-{index}.bin",
            tape_barcode=f"VOL10{index}L9",
        )

    plan = make_planner(service).plan(RestorePlanRequest(pool_id=pool.id, max_drives=2))

    assert plan.estimated_tape_swaps == 1


def test_estimated_bytes_sums_resolvable_files(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = seed_pool(service)
    dataset = seed_dataset(service, pool_id=pool.id)
    seed_file(service, dataset_id=dataset.id, pool_id=pool.id, relative_path="a.bin", size_bytes=11)
    seed_file(service, dataset_id=dataset.id, pool_id=pool.id, relative_path="b.bin", size_bytes=22)
    seed_file(
        service,
        dataset_id=dataset.id,
        pool_id=pool.id,
        relative_path="c.bin",
        size_bytes=99,
        tape_barcode=None,
    )

    plan = make_planner(service).plan(RestorePlanRequest(pool_id=pool.id))

    assert plan.estimated_bytes == 33


def test_missing_tape_warning_is_present(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = seed_pool(service)
    dataset = seed_dataset(service, pool_id=pool.id)
    seed_file(service, dataset_id=dataset.id, pool_id=pool.id, relative_path="missing.bin", tape_barcode=None)

    plan = make_planner(service).plan(RestorePlanRequest(pool_id=pool.id))

    assert any("tape(s) required but not available" in warning for warning in plan.warnings)


def test_exported_tape_warning_is_present(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = seed_pool(service)
    dataset = seed_dataset(service, pool_id=pool.id)
    seed_file(
        service,
        dataset_id=dataset.id,
        pool_id=pool.id,
        relative_path="exported.bin",
        tape_barcode="VOL009L9",
        status=NasFileState.EXPORTED,
    )

    plan = make_planner(service).plan(RestorePlanRequest(pool_id=pool.id))

    assert any("tape(s) have been exported" in warning for warning in plan.warnings)


def test_high_swap_warning_is_present(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = seed_pool(service)
    dataset = seed_dataset(service, pool_id=pool.id)
    for index in range(10):
        seed_file(
            service,
            dataset_id=dataset.id,
            pool_id=pool.id,
            relative_path=f"big-{index}.bin",
            tape_barcode=f"VOL2{index:02d}L9",
        )

    plan = make_planner(service).plan(RestorePlanRequest(pool_id=pool.id, max_drives=2))

    assert plan.estimated_tape_swaps == 4
    assert any("Consider restoring in batches" in warning for warning in plan.warnings)


def test_is_safe_to_enqueue_true_when_all_tapes_available(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = seed_pool(service)
    dataset = seed_dataset(service, pool_id=pool.id)
    seed_file(service, dataset_id=dataset.id, pool_id=pool.id, relative_path="safe.bin", tape_barcode="VOL001L9")

    plan = make_planner(service).plan(RestorePlanRequest(pool_id=pool.id))

    assert plan.exported_tapes == []
    assert plan.missing_tapes == []
    assert plan.unavailable_files == []
    assert plan.is_safe_to_enqueue is True
