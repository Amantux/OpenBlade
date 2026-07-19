from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig
from openblade.nas.sidecar import SIDECAR_FILENAME, SidecarResolver
from openblade.nas.types import (
    EffectivePolicySource,
    IngestMode,
    PolicyType,
    SidecarValidationError,
    StoragePolicy,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_app_context(tmp_path: Path) -> None:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'nas-sidecar.db'}"))
    reset_context(context)


@pytest.fixture
def resolver() -> SidecarResolver:
    return SidecarResolver()


def write_sidecar(directory: Path, content: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / SIDECAR_FILENAME).write_text(content, encoding="utf-8")


def make_policy(
    policy_id: str,
    name: str,
    *,
    policy_type: PolicyType = PolicyType.BALANCED,
    default_ingest_mode: IngestMode = IngestMode.CACHE_DRIVE,
    copies_required: int = 1,
    verify_before_archive: bool = True,
    verify_after_archive: bool = True,
) -> StoragePolicy:
    return StoragePolicy(
        id=policy_id,
        name=name,
        policy_type=policy_type,
        default_ingest_mode=default_ingest_mode,
        copies_required=copies_required,
        verify_before_archive=verify_before_archive,
        verify_after_archive=verify_after_archive,
    )


class StubNasService:
    def __init__(self, *policies: StoragePolicy) -> None:
        self._policies = {policy.id: policy for policy in policies}

    def get_policy(self, policy_id: str) -> StoragePolicy | None:
        return self._policies.get(policy_id)


def test_no_sidecar_returns_none(fs, resolver: SidecarResolver) -> None:
    fs.create_dir("/openblade/inbox/no-policy")

    assert resolver.load_sidecar("/openblade/inbox/no-policy") is None


def test_sidecar_found_and_parsed(fs, resolver: SidecarResolver) -> None:
    directory = Path("/openblade/inbox/critical-project")
    write_sidecar(
        directory,
        """
volume_group: critical-projects
pool: critical-projects
policy: critical_sequential
ingest_mode: cache_drive
cache_drive: primary-cache
retention: permanent
copies: 2
preserve_tree: true
verify_before_archive: true
verify_after_write: true
evict_cache_after_verified: false
""".strip(),
    )

    policy = resolver.load_sidecar(str(directory))

    assert policy is not None
    assert policy.volume_group == "critical-projects"
    assert policy.pool == "critical-projects"
    assert policy.policy == "critical_sequential"
    assert policy.ingest_mode == IngestMode.CACHE_DRIVE
    assert policy.cache_drive == "primary-cache"
    assert policy.retention == "permanent"
    assert policy.copies == 2
    assert policy.preserve_tree is True
    assert policy.verify_before_archive is True
    assert policy.verify_after_write is True
    assert policy.evict_cache_after_verified is False


def test_sidecar_invalid_yaml_raises(fs, resolver: SidecarResolver) -> None:
    directory = Path("/openblade/inbox/bad-policy")
    write_sidecar(directory, "copies: [1, 2")

    with pytest.raises(SidecarValidationError):
        resolver.load_sidecar(str(directory))


def test_empty_sidecar_returns_empty_policy(fs, resolver: SidecarResolver) -> None:
    directory = Path("/openblade/inbox/empty-policy")
    write_sidecar(directory, "")

    policy = resolver.load_sidecar(str(directory))

    assert policy is not None
    assert all(getattr(policy, field_name) is None for field_name in type(policy).model_fields)


def test_sidecar_copies_out_of_range_raises(fs, resolver: SidecarResolver) -> None:
    directory = Path("/openblade/inbox/too-many-copies")
    write_sidecar(directory, "copies: 5")

    with pytest.raises(SidecarValidationError) as exc_info:
        resolver.load_sidecar(str(directory))

    assert exc_info.value.field == "copies"
    assert exc_info.value.raw_value == 5


def test_copies_zero_raises_sidecar_error(fs, resolver: SidecarResolver) -> None:
    directory = Path("/openblade/inbox/zero-copies")
    write_sidecar(directory, "copies: 0")

    with pytest.raises(SidecarValidationError) as exc_info:
        resolver.load_sidecar(str(directory))

    assert exc_info.value.field == "copies"
    assert exc_info.value.raw_value == 0


def test_unknown_keys_produce_warnings(fs, resolver: SidecarResolver) -> None:
    directory = Path("/openblade/inbox/unknown-keys")
    write_sidecar(
        directory,
        """
unknown_field: foo
copies: 2
""".strip(),
    )

    effective = resolver.resolve_effective_policy(str(directory))

    assert "Unknown sidecar key ignored: unknown_field" in effective.warnings
    assert effective.copies == 2


def test_resolve_uses_sidecar_over_share_default(fs, resolver: SidecarResolver) -> None:
    directory = Path("/openblade/inbox/override-sidecar")
    write_sidecar(
        directory,
        """
policy: sidecar-policy
ingest_mode: cache_drive
pool: sidecar-pool
copies: 2
verify_before_archive: true
verify_after_write: false
preserve_tree: false
""".strip(),
    )
    share_default = make_policy(
        "share-default",
        "Share Default",
        default_ingest_mode=IngestMode.SOURCE_STREAM,
        copies_required=1,
        verify_before_archive=False,
        verify_after_archive=True,
    )

    effective = resolver.resolve_effective_policy(str(directory), share_default_policy=share_default)

    assert effective.policy_id == "sidecar-policy"
    assert effective.policy_name == "sidecar-policy"
    assert effective.ingest_mode == IngestMode.CACHE_DRIVE
    assert effective.pool == "sidecar-pool"
    assert effective.copies == 2
    assert effective.verify_before_archive is True
    assert effective.verify_after_write is False
    assert effective.preserve_tree is False
    assert "Sidecar references unknown policy: sidecar-policy" in effective.warnings


def test_partial_sidecar_merge(fs, resolver: SidecarResolver) -> None:
    directory = Path("/openblade/inbox/partial-sidecar")
    write_sidecar(directory, "ingest_mode: source_stream")
    share_default = make_policy(
        "share-default",
        "Share Default",
        default_ingest_mode=IngestMode.CACHE_DRIVE,
        copies_required=2,
    )

    effective = resolver.resolve_effective_policy(str(directory), share_default_policy=share_default)

    assert effective.ingest_mode == IngestMode.SOURCE_STREAM
    assert effective.copies == 2


def test_policy_name_resolves_storage_policy_fields(fs) -> None:
    directory = Path("/openblade/inbox/resolved-policy")
    write_sidecar(directory, "policy: resolved-policy")
    share_default = make_policy(
        "share-default",
        "Share Default",
        default_ingest_mode=IngestMode.CACHE_DRIVE,
        copies_required=1,
        verify_before_archive=False,
        verify_after_archive=False,
    )
    resolved_policy = make_policy(
        "resolved-policy",
        "Resolved Policy",
        default_ingest_mode=IngestMode.SOURCE_STREAM,
        copies_required=3,
        verify_before_archive=True,
        verify_after_archive=True,
    )
    resolver = SidecarResolver(nas_service=StubNasService(resolved_policy))

    effective = resolver.resolve_effective_policy(str(directory), share_default_policy=share_default)

    assert effective.policy_id == "resolved-policy"
    assert effective.policy_name == "Resolved Policy"
    assert effective.ingest_mode == IngestMode.SOURCE_STREAM
    assert effective.copies == 3
    assert effective.verify_before_archive is True
    assert effective.verify_after_write is True
    assert effective.warnings == []


def test_resolve_uses_share_default_when_no_sidecar(fs, resolver: SidecarResolver) -> None:
    fs.create_dir("/openblade/inbox/share-default-only")
    system_default = make_policy("system-default", "System Default", copies_required=4)
    share_default = make_policy(
        "share-default",
        "Share Default",
        default_ingest_mode=IngestMode.SOURCE_STREAM,
        copies_required=2,
        verify_before_archive=False,
        verify_after_archive=False,
    )

    effective = resolver.resolve_effective_policy(
        "/openblade/inbox/share-default-only",
        share_default_policy=share_default,
        system_default_policy=system_default,
    )

    assert effective.policy_id == "share-default"
    assert effective.policy_name == "Share Default"
    assert effective.ingest_mode == IngestMode.SOURCE_STREAM
    assert effective.copies == 2
    assert effective.verify_before_archive is False
    assert effective.verify_after_write is False
    assert effective.source == EffectivePolicySource.SHARE_DEFAULT


def test_resolve_uses_system_default_when_nothing_else(fs, resolver: SidecarResolver) -> None:
    fs.create_dir("/openblade/inbox/system-default-only")
    system_default = make_policy(
        "system-default",
        "System Default",
        policy_type=PolicyType.CRITICAL_SEQUENTIAL,
        copies_required=3,
    )

    effective = resolver.resolve_effective_policy(
        "/openblade/inbox/system-default-only",
        system_default_policy=system_default,
    )

    assert effective.policy_id == "system-default"
    assert effective.policy_name == "System Default"
    assert effective.copies == 3
    assert effective.source == EffectivePolicySource.SYSTEM_DEFAULT


def test_resolve_effective_policy_source_tracking(fs, resolver: SidecarResolver) -> None:
    directory = Path("/openblade/inbox/source-tracking")
    write_sidecar(directory, "policy: tracked-sidecar")
    share_default = make_policy("share-default", "Share Default")
    system_default = make_policy("system-default", "System Default")

    effective = resolver.resolve_effective_policy(
        str(directory),
        share_default_policy=share_default,
        system_default_policy=system_default,
    )

    assert effective.source == EffectivePolicySource.SIDECAR
    assert effective.sidecar_path == str(directory / SIDECAR_FILENAME)


def test_policy_name_resolution_warning_when_no_service(fs, resolver: SidecarResolver) -> None:
    directory = Path("/openblade/inbox/no-service-policy")
    write_sidecar(directory, "policy: unknown-policy")

    effective = resolver.resolve_effective_policy(str(directory))

    assert "Sidecar references unknown policy: unknown-policy" in effective.warnings
    assert effective.policy_id == "unknown-policy"
    assert effective.policy_name == "unknown-policy"


def test_resolve_policy_endpoint(fs) -> None:
    directory = Path("/openblade/inbox/endpoint-project")
    write_sidecar(
        directory,
        """
policy: endpoint-sidecar
ingest_mode: cache_drive
copies: 2
verify_after_write: false
""".strip(),
    )

    response = client.post(
        "/nas/resolve-policy",
        json={"directory": str(directory), "share_id": "/openblade/inbox"},
    )

    assert response.status_code == 200
    assert response.json()["policy_id"] == "endpoint-sidecar"
    assert response.json()["policy_name"] == "endpoint-sidecar"
    assert response.json()["ingest_mode"] == "cache_drive"
    assert response.json()["copies"] == 2
    assert response.json()["verify_after_write"] is False
    assert response.json()["source"] == "sidecar"
    assert response.json()["sidecar_path"] == str(directory / SIDECAR_FILENAME)
