"""Staged (STAGING/VERIFYING) instances and the job journal in the catalog.

Follow-up to docs/decisions/2026-10-09-persistent-drive-leases.md: a sharded
archive stages its instances and exposes them as ARCHIVED only in one commit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openblade.catalog.db import get_session, init_db
from openblade.catalog.models import FileInstance
from openblade.catalog.repository import CatalogRepository
from openblade.domain.errors import FileNotFoundError
from openblade.domain.models import FileInstanceState


@pytest.fixture
def repo(tmp_path: Path) -> CatalogRepository:
    init_db(f"sqlite:///{tmp_path / 'catalog.db'}")
    return CatalogRepository(get_session())


def _stage_two_shards(repo: CatalogRepository, job_id: str = "job-1") -> list[FileInstance]:
    vg = repo.create_volume_group("vg-staged")
    instances = []
    for index, barcode in enumerate(["AAA001L9", "BBB001L9"]):
        record = repo.create_file_record(
            f"/data/big.bin.shard{index}", 10, "0" * 64, vg.id, shard_index=index
        )
        instances.append(
            repo.create_staged_instance(job_id, record.id, barcode, f"/big.bin.shard{index}")
        )
    return instances


def _states(repo: CatalogRepository, instances: list[FileInstance]) -> list[str]:
    repo.session.expire_all()
    return [repo.session.get(FileInstance, i.id).state for i in instances]  # type: ignore[union-attr]


def test_journal_round_trip_is_ordered_and_scoped_to_the_job(repo: CatalogRepository) -> None:
    repo.journal("job-1", "lease_acquired", {"drive_id": 0, "barcode": "AAA001L9"})
    repo.journal("job-2", "lease_acquired")
    repo.journal("job-1", "failure", {"error": "LaneWriteError"})

    entries = repo.job_journal("job-1")

    assert [e.event for e in entries] == ["lease_acquired", "failure"]
    assert entries[0].detail == {"drive_id": 0, "barcode": "AAA001L9"}
    assert entries[1].detail == {"error": "LaneWriteError"}
    assert repo.job_journal("job-2")[0].detail == {}
    assert repo.job_journal("nope") == []


def test_create_staged_instance_journals_and_lists_by_job(repo: CatalogRepository) -> None:
    instances = _stage_two_shards(repo)
    other_job_instance = repo.create_staged_instance(
        "job-2", instances[0].file_record_id, "CCC001L9", "/other"
    )

    staged = repo.list_staged_instances("job-1")

    assert [(s.barcode, s.tape_path, s.shard_index, s.state) for s in staged] == [
        ("AAA001L9", "/big.bin.shard0", 0, "staging"),
        ("BBB001L9", "/big.bin.shard1", 1, "staging"),
    ]
    assert [s.instance_id for s in repo.list_staged_instances("job-2")] == [other_job_instance.id]
    assert [e.event for e in repo.job_journal("job-1")] == ["shard_staged", "shard_staged"]
    assert repo.list_staged_instances("no-such-job") == []


def test_create_staged_instance_refuses_a_committed_state(repo: CatalogRepository) -> None:
    vg = repo.create_volume_group("vg-x")
    record = repo.create_file_record("/x", 1, "0" * 64, vg.id)
    with pytest.raises(ValueError):
        repo.create_staged_instance(
            "job-1", record.id, "AAA001L9", "/x", state=FileInstanceState.ARCHIVED
        )
    assert repo.list_instances_for_barcode("AAA001L9") == []


def test_bulk_commit_archives_every_instance_together(repo: CatalogRepository) -> None:
    instances = _stage_two_shards(repo)
    ids = [i.id for i in instances]

    repo.mark_instances_verifying(ids)
    assert _states(repo, instances) == ["verifying", "verifying"]
    assert [s.state for s in repo.list_staged_instances("job-1")] == ["verifying"] * 2

    repo.mark_instances_archived(ids)

    assert _states(repo, instances) == ["archived", "archived"]
    assert all(repo.session.get(FileInstance, i).archived_at is not None for i in ids)  # type: ignore[union-attr]
    assert repo.list_staged_instances("job-1") == []


def test_bulk_commit_is_all_or_nothing(repo: CatalogRepository) -> None:
    instances = _stage_two_shards(repo)
    repo.mark_instances_verifying([instances[0].id])

    # One shard never reached VERIFYING: nothing may become ARCHIVED.
    with pytest.raises(ValueError):
        repo.mark_instances_archived([i.id for i in instances])
    assert _states(repo, instances) == ["verifying", "staging"]

    with pytest.raises(FileNotFoundError):
        repo.mark_instances_archived([instances[0].id, "missing-id"])
    assert _states(repo, instances) == ["verifying", "staging"]


def test_staged_instances_are_never_listed_as_archived_or_restorable(
    repo: CatalogRepository,
) -> None:
    instances = _stage_two_shards(repo)
    repo.mark_instances_verifying([instances[1].id])  # one STAGING, one VERIFYING

    assert repo.list_catalog_tape_barcodes() == []
    assert repo.list_ltfs_entries() == []
    for index in (0, 1):
        with pytest.raises(FileNotFoundError):
            repo.get_latest_instance_for_path(f"/data/big.bin.shard{index}")

    # Failure cleanup must not drop the records staged shards hang off.
    repo.delete_file_record_if_unarchived("/data/big.bin.shard0")
    assert repo.get_file_record("/data/big.bin.shard0") is not None

    repo.mark_instances_verifying([i.id for i in instances])
    repo.mark_instances_archived([i.id for i in instances])
    assert repo.list_catalog_tape_barcodes() == ["AAA001L9", "BBB001L9"]
