"""Application bootstrap and shared runtime context."""

from __future__ import annotations

import hashlib
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import structlog

from openblade.catalog.db import get_session, init_db
from openblade.catalog.repository import CatalogRepository
from openblade.config import BackendMode, OpenBladeConfig, load_config
from openblade.domain.backends import LibraryBackend, LTFSBackend
from openblade.hardware.discovery import discover_library
from openblade.hardware.library import RealLibraryBackend
from openblade.hardware.ltfs import RealLTFSBackend
from openblade.hardware.runner import SafeRunner
from openblade.hardware.safety import require_real_hardware
from openblade.jobs.archive import ArchiveService
from openblade.jobs.format import FormatService
from openblade.jobs.inventory import InventoryService, run_inventory_job
from openblade.jobs.queue import JobQueue
from openblade.jobs.restore import RestoreService
from openblade.jobs.worker import Worker
from openblade.nas.service import NasService
from openblade.nas.types import NasDataset, NasFileRecord, NasFileState, NasPool, StoragePolicy
from openblade.simulator.i3_config import scalar_i3_data_barcodes
from openblade.simulator.scenarios import scalar_i3_default

structlog.configure()

_library: LibraryBackend | None = None
_ltfs: LTFSBackend | None = None
_catalog: CatalogRepository | None = None


def _default_policies() -> list[StoragePolicy]:
    return [
        StoragePolicy(
            id="critical_sequential",
            name="Critical Sequential",
            policy_type="critical_sequential",
            allow_spillover=False,
            max_parallelism=1,
        ),
        StoragePolicy(
            id="noncritical_sharded",
            name="Noncritical Sharded",
            policy_type="noncritical_sharded",
            allow_sharding=True,
            max_parallelism=4,
            shard_strategy="round_robin",
        ),
        StoragePolicy(
            id="balanced",
            name="Balanced",
            policy_type="balanced",
            allow_spillover=True,
            max_parallelism=2,
        ),
    ]


def _seed_nas_defaults(catalog: CatalogRepository) -> None:
    service = NasService(catalog)
    existing_policy_ids = {policy.id for policy in service.get_policies()}
    for policy in _default_policies():
        if policy.id not in existing_policy_ids:
            service.upsert_policy(policy)

    existing_share_paths = {share.path for share in service.get_nas_shares()}
    for share in service.get_default_shares():
        if share.path not in existing_share_paths:
            service.upsert_share(share)


def _configured_library_specs(config: OpenBladeConfig) -> list[dict[str, object]]:
    default_metadata = [
        ("Primary Tape Library", "OB-SCALAR-I3-001", "primary", {"primary"}),
        ("Secondary Archive", "OB-SCALAR-I3-002", "archive", {"Library 2"}),
        ("Cold Storage Vault", "OB-SCALAR-I3-003", "cold_storage", {"Library 3"}),
    ]
    specs: list[dict[str, object]] = []
    for index, emulator_url in enumerate(config.emulator_urls):
        if index < len(default_metadata):
            name, serial, role, legacy_names = default_metadata[index]
        else:
            name = f"Tape Library {index + 1}"
            serial = f"OB-SCALAR-I3-{index + 1:03d}"
            role = "archive"
            legacy_names = set()
        specs.append(
            {
                "name": name,
                "emulator_url": emulator_url,
                "serial_number": serial,
                "model": "Scalar i3",
                "role": role,
                "sort_order": index,
                "legacy_names": legacy_names,
            }
        )
    return specs


def _seed_library_defaults(catalog: CatalogRepository, config: OpenBladeConfig) -> None:
    desired = _configured_library_specs(config)

    existing_libraries = catalog.list_library_instances()
    for spec in desired:
        matching_library = next(
            (
                library
                for library in existing_libraries
                if library.serial_number == spec["serial_number"]
                or library.name == spec["name"]
                or library.name in spec["legacy_names"]
            ),
            None,
        )
        if matching_library is None:
            catalog.create_library_instance(
                name=spec["name"],
                emulator_url=spec["emulator_url"],
                serial_number=spec["serial_number"],
                model=spec["model"],
                role=spec["role"],
                sort_order=spec["sort_order"],
            )
            continue

        updates: dict[str, object] = {}
        for key in ("name", "emulator_url", "serial_number", "model", "role", "sort_order"):
            if getattr(matching_library, key) != spec[key]:
                updates[key] = spec[key]
        if not matching_library.enabled:
            updates["enabled"] = True
        if updates:
            catalog.update_library_instance(matching_library.id, **updates)


def _seed_demo_catalog(catalog: CatalogRepository) -> None:
    demo_specs = [
        {
            "dataset_id": "demo-project-alpha",
            "name": "Project Alpha",
            "volume_group": "project-alpha",
            "pool_id": "critical-projects",
            "policy_id": "critical_sequential",
            "source_path": "/demo/project-alpha",
            "files": [
                ("alpha/design-spec-v3.pdf", 512_000_000, "VOL001L9"),
                ("alpha/render/shot-001.exr", 410_000_000, "VOL001L9"),
                ("alpha/render/shot-002.exr", 395_000_000, "VOL001L9"),
                ("alpha/docs/requirements.docx", 128_000_000, "VOL001L9"),
                ("alpha/logs/build-2024-01-21.txt", 605_000_000, "VOL001L9"),
            ],
        },
        {
            "dataset_id": "demo-media-archive-2024",
            "name": "Media Archive 2024",
            "volume_group": "media-archive-2024",
            "pool_id": "general-archive",
            "policy_id": "balanced",
            "source_path": "/demo/media-archive-2024",
            "files": [
                (
                    f"media/archive-2024/reel-{index:02d}.mov",
                    400_000_000,
                    "VOL002L9" if index <= 10 else "VOL003L9",
                )
                for index in range(1, 21)
            ],
        },
        {
            "dataset_id": "demo-backup-set-a",
            "name": "Backup Set A",
            "volume_group": "backup-set-a",
            "pool_id": "media-cache",
            "policy_id": "balanced",
            "source_path": "/demo/backup-set-a",
            "files": [
                ("backup/etc-hosts.tar.gz", 150_000_000, "VOL004L9"),
                ("backup/pg-base.tar.zst", 220_000_000, "VOL004L9"),
                ("backup/app-configs.tar.gz", 130_000_000, "VOL004L9"),
            ],
        },
        {
            "dataset_id": "demo-operations-snapshots",
            "name": "Operations Snapshots",
            "volume_group": "operations-snapshots",
            "pool_id": "cold-storage",
            "policy_id": "noncritical_sharded",
            "source_path": "/demo/operations-snapshots",
            "files": [
                ("ops/snapshots/2024-01-10.json", 90_000_000, "VOL010L9"),
                ("ops/snapshots/2024-01-17.json", 120_000_000, "VOL010L9"),
                ("ops/snapshots/2024-01-24.json", 110_000_000, "VOL010L9"),
                ("ops/reports/monthly.csv", 76_000_000, "VOL010L9"),
            ],
        },
    ]
    existing_dataset_ids = {dataset["id"] for dataset in catalog.list_nas_datasets()}
    if all(spec["dataset_id"] in existing_dataset_ids for spec in demo_specs):
        return

    data_barcodes = set(scalar_i3_data_barcodes())
    for spec in demo_specs:
        volume_group = catalog.create_volume_group(spec["volume_group"])
        tape_set = sorted({barcode for _, _, barcode in spec["files"]})
        shard_map = {
            barcode: [
                relative_path
                for relative_path, _, current_barcode in spec["files"]
                if current_barcode == barcode
            ]
            for barcode in tape_set
        }
        total_bytes = sum(size_bytes for _, size_bytes, _ in spec["files"])
        catalog.upsert_nas_dataset(
            NasDataset(
                id=spec["dataset_id"],
                name=spec["name"],
                pool_id=spec["pool_id"],
                policy_id=spec["policy_id"],
                source_path=spec["source_path"],
                ingest_mode="cache_drive",
                volume_group_id=volume_group.id,
                tape_set=tape_set,
                shard_map=shard_map,
                file_count=len(spec["files"]),
                total_bytes=total_bytes,
                status="archived",
                copies_completed=len(tape_set),
                manifest_path=f"{spec['source_path']}/manifest.json",
                created_at="2024-01-22T08:00:00Z",
                updated_at="2024-01-22T08:30:00Z",
            ).model_dump(mode="json")
        )
        for relative_path, size_bytes, barcode in spec["files"]:
            checksum = hashlib.sha256(
                f"{spec['dataset_id']}:{relative_path}:{size_bytes}".encode()
            ).hexdigest()
            catalog_path = f"/{spec['volume_group']}/{relative_path}"
            record = catalog.create_file_record(
                catalog_path,
                size_bytes,
                checksum,
                volume_group.id,
                shard_count=1,
                shard_index=None,
                block_size=None,
                shard_profile="demo",
                parent_id=None,
            )
            instances = [
                instance
                for instance in record.instances
                if instance.barcode == barcode and instance.tape_path == catalog_path
            ]
            if not instances:
                instance = catalog.create_file_instance(record.id, barcode, catalog_path)
                catalog.mark_instance_archived(instance.id, checksum_verified=True)
            catalog.upsert_nas_file_record(
                NasFileRecord(
                    id=f"{spec['dataset_id']}::{relative_path.replace('/', '::')}",
                    dataset_id=spec["dataset_id"],
                    pool_id=spec["pool_id"],
                    relative_path=relative_path,
                    source_path=f"{spec['source_path']}/{relative_path}",
                    size_bytes=size_bytes,
                    mtime="2024-01-22T08:00:00Z",
                    checksum_sha256=checksum,
                    tape_barcode=barcode,
                    tape_offset=0,
                    status=NasFileState.OFFLINE_ON_TAPE,
                    cache_path=None,
                    created_at="2024-01-22T08:00:00Z",
                    updated_at="2024-01-22T08:30:00Z",
                ).model_dump(mode="json")
            )
            if barcode in data_barcodes:
                cartridge = catalog.add_cartridge(barcode, volume_group.id)
                cartridge.used_bytes = max(
                    int(cartridge.used_bytes),
                    sum(size for _, size, file_barcode in spec["files"] if file_barcode == barcode),
                )
                cartridge.capacity_bytes = max(int(cartridge.capacity_bytes), 18_000_000_000)
                cartridge.formatted = True
                catalog.session.commit()


def _seed_nas_pools(catalog: CatalogRepository) -> None:
    service = NasService(catalog)
    desired_pools = [
        NasPool(
            id="critical-projects",
            name="Critical Projects",
            description="Tier for high-priority archive sets.",
            volume_group_ids=[catalog.create_volume_group("project-alpha").id],
            default_policy_id="critical_sequential",
            mount_path="/openblade/pools/critical-projects",
            hydration_behavior="queue",
            restore_target_path="/openblade/restore/critical-projects",
            access_mode="read_only",
        ),
        NasPool(
            id="general-archive",
            name="General Archive",
            description="Primary NAS virtual pool for balanced archive sets.",
            volume_group_ids=[catalog.create_volume_group("media-archive-2024").id],
            default_policy_id="balanced",
            mount_path="/openblade/pools/general-archive",
            hydration_behavior="queue",
            restore_target_path="/openblade/restore/general-archive",
            access_mode="read_only",
        ),
        NasPool(
            id="media-cache",
            name="Media Cache",
            description="Short-term landing pool for staged restore and archive jobs.",
            volume_group_ids=[catalog.create_volume_group("backup-set-a").id],
            default_policy_id="balanced",
            mount_path="/openblade/pools/media-cache",
            hydration_behavior="auto",
            restore_target_path="/openblade/restore/media-cache",
            access_mode="read_write",
        ),
    ]
    existing_pool_ids = {pool.id for pool in service.list_pools()}
    for pool in desired_pools:
        if pool.id not in existing_pool_ids:
            service.upsert_pool(pool)


def seed_demo_environment(
    catalog: CatalogRepository, config: OpenBladeConfig | None = None
) -> None:
    active_config = config or load_config()
    _seed_library_defaults(catalog, active_config)
    _seed_demo_catalog(catalog)
    _seed_nas_pools(catalog)


@dataclass
class AppContext:
    config: OpenBladeConfig
    library: LibraryBackend
    ltfs: LTFSBackend
    catalog: CatalogRepository
    queue: JobQueue
    worker: Worker
    inventory_service: InventoryService
    format_service: FormatService
    archive_service: ArchiveService
    restore_service: RestoreService


def _create_real_backends(config: OpenBladeConfig) -> tuple[RealLibraryBackend, RealLTFSBackend]:
    guard = require_real_hardware(config)
    runner = SafeRunner(dry_run=config.hardware_dry_run)
    discovery = discover_library(runner, guard)
    library = RealLibraryBackend(config=config, runner=runner, discovery=discovery)
    ltfs = RealLTFSBackend(
        library=library,
        guard=guard,
        runner=runner,
        mount_root=Path(config.ltfs_mount_root),
    )
    return library, ltfs


def get_library() -> LibraryBackend:
    global _library, _ltfs
    if _library is None:
        cfg = load_config()
        if cfg.backend == BackendMode.MOCK:
            _library, _ltfs = scalar_i3_default()
        else:
            _library, _ltfs = _create_real_backends(cfg)
    return _library


def get_ltfs() -> LTFSBackend:
    global _ltfs
    if _ltfs is None:
        get_library()
    assert _ltfs is not None
    return _ltfs


def get_catalog() -> CatalogRepository:
    global _catalog
    cfg = load_config()
    init_db(cfg.db_url)
    if _catalog is None:
        _catalog = CatalogRepository(get_session())
        _seed_nas_defaults(_catalog)
        if cfg.backend == BackendMode.MOCK:
            seed_demo_environment(_catalog, cfg)
    return _catalog


def _seed_demo_ltfs(catalog: CatalogRepository, ltfs) -> None:
    """Populate MockLTFSBackend with small example files based on catalog NAS records.

    This helps emulator-backed tests that expect LTFS content to exist.
    """
    try:
        from openblade.simulator.ltfs_volume import MockFileRecord
    except Exception:
        return

    datasets = catalog.list_nas_datasets()
    for ds in datasets:
        dataset_id = ds.get("id")
        try:
            files = catalog.list_nas_file_records(dataset_id)
        except Exception:
            files = []
        for f in files:
            barcode = f.get("tape_barcode")
            path = f.get("relative_path")
            if not barcode or not path:
                continue
            try:
                tape = ltfs.ensure_tape(barcode)
            except Exception:
                continue
            # create small deterministic content to avoid large memory usage
            content = (f"{dataset_id}:{path}").encode()[:1024]
            checksum = hashlib.sha256(content).hexdigest()
            if path not in tape.files:
                tape.files[path] = MockFileRecord(
                    tape_path=path,
                    size_bytes=len(content),
                    checksum_sha256=checksum,
                    content=content,
                )
                tape.used_bytes = max(tape.used_bytes, tape.used_bytes + len(content))
                tape.formatted = True


def create_context(config: OpenBladeConfig | None = None) -> AppContext:
    active_config = config or load_config()
    init_db(active_config.db_url)
    if active_config.backend == BackendMode.MOCK:
        library, ltfs = scalar_i3_default()
    else:
        library, ltfs = _create_real_backends(active_config)
    catalog = CatalogRepository(get_session())
    _seed_nas_defaults(catalog)
    if active_config.backend == BackendMode.MOCK:
        _seed_library_defaults(catalog, active_config)
        if config is None:
            _seed_demo_catalog(catalog)
            _seed_nas_pools(catalog)
            # Also populate the simulated LTFS backend with small example files so
            # emulator operations that rely on LTFS content behave realistically.
            with suppress(Exception):
                _seed_demo_ltfs(catalog, ltfs)
    run_inventory_job(library, catalog)
    queue = JobQueue()
    worker = Worker(queue)
    return AppContext(
        config=active_config,
        library=library,
        ltfs=ltfs,
        catalog=catalog,
        queue=queue,
        worker=worker,
        inventory_service=InventoryService(library),
        format_service=FormatService(catalog, library, ltfs),
        archive_service=ArchiveService(library, ltfs, catalog, queue),
        restore_service=RestoreService(library, ltfs, catalog, queue),
    )


_CONTEXT: AppContext | None = None


def get_context() -> AppContext:
    global _CONTEXT
    if _CONTEXT is None:
        _CONTEXT = create_context()
    return _CONTEXT


def reset_context(context: AppContext | None = None) -> AppContext:
    global _CONTEXT, _library, _ltfs, _catalog
    _CONTEXT = context or create_context()
    _library = _CONTEXT.library
    _ltfs = _CONTEXT.ltfs
    _catalog = _CONTEXT.catalog

    from openblade.api import aml_state

    aml_state.ensure_initialized(
        _CONTEXT.config.db_url,
        force_reset=True,
        emulator_latency_profile=_CONTEXT.config.emulator_latency_profile,
        emulator_latency_enabled=_CONTEXT.config.emulator_latency_enabled,
    )
    return _CONTEXT
