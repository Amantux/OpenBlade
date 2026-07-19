from pathlib import Path

import pytest
from pydantic import ValidationError

from openblade.bootstrap import create_context
from openblade.config import OpenBladeConfig
from openblade.nas.service import NasService
from openblade.nas.types import (
    CacheDriveConfig,
    EvictionPolicy,
    NasPool,
    NasShareDefinition,
    PolicyType,
    ShardStrategy,
    SourceStreamConfig,
    StoragePolicy,
)


def make_nas_service(tmp_path: Path) -> NasService:
    context = create_context(OpenBladeConfig(db_url=f"sqlite+aiosqlite:///{tmp_path / 'openblade.db'}"))
    return NasService(context.catalog)


def test_seeded_policies_exist_after_init(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)

    assert {policy.id for policy in service.get_policies()} >= {
        "balanced",
        "critical_sequential",
        "noncritical_sharded",
    }


def test_policy_crud_round_trip(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    policy = StoragePolicy(
        id="custom-policy",
        name="Custom Policy",
        policy_type=PolicyType.BALANCED,
        copies_required=2,
        allow_sharding=True,
        max_parallelism=3,
        shard_strategy=ShardStrategy.CAPACITY_WEIGHTED,
    )

    saved = service.upsert_policy(policy)
    fetched = service.get_policy(policy.id)

    assert saved == policy
    assert fetched == policy
    assert service.delete_policy(policy.id) is True
    assert service.get_policy(policy.id) is None


def test_cache_drive_crud_round_trip(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    drive = CacheDriveConfig(
        id="cache-a",
        name="Cache A",
        root_path="/srv/cache-a",
        max_bytes=1_000_000,
        min_free_bytes=100_000,
        eviction_policy=EvictionPolicy.AFTER_VERIFIED,
        support_reflink_or_hardlink=True,
    )

    saved = service.upsert_cache_drive(drive)
    assert saved == drive
    assert service.get_cache_drive(drive.id) == drive
    assert service.get_cache_drives() == [drive]
    assert service.delete_cache_drive(drive.id) is True
    assert service.get_cache_drive(drive.id) is None
    assert service.get_cache_drives() == []


def test_source_stream_config_crud(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)

    assert service.get_source_stream_config() == SourceStreamConfig()
    assert service.list_source_stream_configs() == []

    updated = SourceStreamConfig(
        checksum_mode="streaming",
        max_retries=5,
        allow_partial_dataset_success=True,
    )

    assert service.update_source_stream_config(updated) == updated
    assert service.get_source_stream_config() == updated
    assert service.list_source_stream_configs() == [updated]
    assert service.delete_source_stream_config() is True
    assert service.delete_source_stream_config() is False
    assert service.get_source_stream_config() == SourceStreamConfig()
    assert service.list_source_stream_configs() == []


def test_share_listing_returns_seeded_defaults(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)

    shares = {share.path: share for share in service.get_nas_shares()}

    assert set(shares) >= {
        "/openblade/inbox",
        "/openblade/inbox-critical",
        "/openblade/inbox-sharded",
        "/openblade/restore",
        "/openblade/catalog",
        "/openblade/virtual",
    }
    assert shares["/openblade/inbox"].default_policy_id == "balanced"
    assert shares["/openblade/inbox"].writable is True


def test_share_crud_round_trip(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    service.upsert_pool(NasPool(id="pool-a", name="Pool A"))
    service.upsert_pool(NasPool(id="pool-b", name="Pool B"))
    share = service.upsert_share(
        NasShareDefinition(
            path="/openblade/custom-share",
            name="Custom Share",
            share_type="pool",
            default_policy_id="balanced",
            pool_ids=["pool-a", "pool-b"],
            folder_mappings=[
                {"folder_path": "/custom/reports", "pool_id": "pool-a", "access_mode": "read_only"},
                {"folder_path": "/custom/ops", "pool_id": "pool-b", "access_mode": "read_write"},
            ],
            writable=True,
            description="Custom NAS pool share.",
        )
    )

    assert service.get_share(share.path) == share
    assert share.pool_ids == ["pool-a", "pool-b"]
    assert len(share.folder_mappings) == 2
    assert share in service.get_nas_shares()
    assert service.delete_share(share.path) is True
    assert service.get_share(share.path) is None


def test_share_rejects_nonexistent_policy(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)

    with pytest.raises(ValueError, match="missing-policy"):
        service.upsert_share(
            NasShareDefinition(
                path="/openblade/bad-share",
                name="Bad Share",
                share_type="pool",
                default_policy_id="missing-policy",
            )
        )


def test_share_rejects_nonexistent_pool(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)

    with pytest.raises(ValueError, match="missing-pool"):
        service.upsert_share(
            NasShareDefinition(
                path="/openblade/bad-pool-share",
                name="Bad Pool Share",
                share_type="pool",
                pool_ids=["missing-pool"],
            )
        )



def test_delete_policy_blocked_when_referenced(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    policy = service.upsert_policy(
        StoragePolicy(
            id="referenced-policy",
            name="Referenced Policy",
            policy_type="balanced",
        )
    )
    service.upsert_share(
        NasShareDefinition(
            path="/openblade/referenced-share",
            name="Referenced Share",
            share_type="pool",
            default_policy_id=policy.id,
        )
    )

    with pytest.raises(ValueError, match=policy.id):
        service.delete_policy(policy.id)

    assert service.get_policy(policy.id) == policy


@pytest.mark.parametrize("copies_required", [0, 5])
def test_policy_validates_copies_range(copies_required: int) -> None:
    with pytest.raises(ValidationError):
        StoragePolicy(
            id=f"policy-{copies_required}",
            name="Invalid Policy",
            policy_type=PolicyType.BALANCED,
            copies_required=copies_required,
        )


@pytest.mark.parametrize("max_parallelism", [0, 17])
def test_policy_validates_max_parallelism_range(max_parallelism: int) -> None:
    with pytest.raises(ValidationError):
        StoragePolicy(
            id=f"policy-{max_parallelism}",
            name="Invalid Policy",
            policy_type=PolicyType.BALANCED,
            max_parallelism=max_parallelism,
        )


@pytest.mark.parametrize("cache_max_bytes", [0, -1])
def test_cache_drive_validates_max_bytes(cache_max_bytes: int) -> None:
    with pytest.raises(ValidationError):
        CacheDriveConfig(
            id=f"cache-{cache_max_bytes}",
            name="Invalid Cache",
            root_path="/srv/cache",
            max_bytes=cache_max_bytes,
            min_free_bytes=0,
        )


def test_cache_drive_validates_min_free_bytes() -> None:
    with pytest.raises(ValidationError):
        CacheDriveConfig(
            id="cache-min-free",
            name="Invalid Cache",
            root_path="/srv/cache",
            max_bytes=1,
            min_free_bytes=-1,
        )


def test_cache_drive_validates_retention_days() -> None:
    with pytest.raises(ValidationError):
        CacheDriveConfig(
            id="cache-retention",
            name="Invalid Cache",
            root_path="/srv/cache",
            max_bytes=1,
            min_free_bytes=0,
            retention_days=-1,
        )



def test_share_path_must_start_with_slash() -> None:
    with pytest.raises(ValidationError):
        NasShareDefinition(
            path="openblade/no-leading-slash",
            name="Bad Share",
            share_type="pool",
        )


@pytest.mark.parametrize(
    ("method_name", "identifier", "bad_data"),
    [
        ("upsert_nas_policy", "invalid-policy", {}),
        ("upsert_nas_policy", "invalid-policy", []),
        ("upsert_nas_cache_drive", "invalid-drive", {}),
        ("upsert_nas_cache_drive", "invalid-drive", []),
        ("upsert_nas_share", "/invalid-share", {}),
        ("upsert_nas_share", "/invalid-share", []),
    ],
)
def test_repository_nas_upserts_require_non_empty_json_object(
    tmp_path: Path, method_name: str, identifier: str, bad_data: object
) -> None:
    service = make_nas_service(tmp_path)

    with pytest.raises(ValueError, match="config_json"):
        getattr(service.repository, method_name)(identifier, bad_data)


@pytest.mark.parametrize(
    ("method_name", "identifier", "bad_data"),
    [
        (
            "upsert_nas_policy",
            "invalid-policy",
            {"name": "   ", "policy_type": "balanced"},
        ),
        (
            "upsert_nas_cache_drive",
            "invalid-drive",
            {
                "name": "Invalid Drive",
                "root_path": "relative/path",
                "max_bytes": 1,
                "min_free_bytes": 0,
            },
        ),
        (
            "upsert_nas_share",
            "/invalid-share",
            {"name": "   ", "share_type": "pool"},
        ),
    ],
)
def test_repository_nas_upserts_validate_pydantic_models(
    tmp_path: Path, method_name: str, identifier: str, bad_data: dict[str, object]
) -> None:
    service = make_nas_service(tmp_path)

    with pytest.raises(ValidationError):
        getattr(service.repository, method_name)(identifier, bad_data)
