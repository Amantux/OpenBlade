"""`openblade archive sharded` and `POST /archive/sharded` must agree.

The CLI is a wrapper, not a second implementation: same ShardedArchiveRequest,
same defaults, same refusals. This drives both paths over identical source trees
on identical simulated libraries and diffs the catalog rows that result --
paths, sizes, checksums, shard counts, shard profiles, block sizes, and the
per-shard tape layout. A default that drifts on one side shows up here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from openblade.bootstrap import AppContext, create_context, reset_context
from openblade.cli import main as cli_main
from openblade.config import OpenBladeConfig
from openblade.domain.policies import FormatConfirmation, SafetyToken
from openblade.jobs.scheduler import DriveScheduler
from openblade.jobs.shard import ShardMode
from openblade.jobs.sharded_archive import ShardedArchiveRequest, run_sharded_archive
from openblade.simulator.library import MockLibraryBackend
from openblade.simulator.ltfs_volume import MockLTFSBackend

runner = CliRunner()
LANES = ["MCK00001", "MCK00002"]


def seed_source(root: Path) -> None:
    (root / "nested").mkdir(parents=True)
    (root / "one.txt").write_bytes(b"one" * 100)
    (root / "nested" / "two.bin").write_bytes(bytes(range(256)) * 900)
    (root / "nested" / "three.txt").write_bytes(b"three" * 50)


def format_all(context: AppContext) -> None:
    for barcode in LANES:
        context.ltfs.format(
            barcode,
            FormatConfirmation(
                expected_barcode=barcode,
                safety_token=SafetyToken.generate("format", barcode),
            ),
        )


def build_context(db_path: Path) -> AppContext:
    """An app context on a fresh SQLite file with a seeded mock library."""
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{db_path}"))
    library = MockLibraryBackend(num_slots=6, num_drives=2, num_import_export_slots=2)
    library.seed_slots(LANES)
    ltfs = MockLTFSBackend(library)
    context.library = library
    context.ltfs = ltfs
    for service in (
        context.inventory_service,
        context.format_service,
        context.archive_service,
        context.restore_service,
    ):
        if hasattr(service, "library"):
            service.library = library
        if hasattr(service, "ltfs"):
            service.ltfs = ltfs
    reset_context(context)
    format_all(context)
    return context


def catalog_fingerprint(context: AppContext, source: Path) -> list[dict[str, Any]]:
    """Everything about the resulting catalog rows that must match.

    Absolute paths and row ids differ between the two runs by construction, so
    paths are made relative to each run's own source root and ids are dropped.
    """
    rows: list[dict[str, Any]] = []
    for record in sorted(
        context.catalog.list_file_records(str(source)), key=lambda item: item.path
    ):
        shards = context.catalog.list_shard_records(record.id)
        rows.append(
            {
                "path": str(Path(record.path).relative_to(source)),
                "size": record.size_bytes,
                "checksum": record.checksum_sha256,
                "shard_count": record.shard_count,
                "shard_profile": record.shard_profile,
                "block_size": record.block_size,
                "instances": sorted(
                    (instance.barcode, instance.state)
                    for instance in record.instances
                ),
                "shards": [
                    {
                        "index": shard.shard_index,
                        "size": shard.size_bytes,
                        "instances": sorted(
                            (instance.barcode, instance.state)
                            for instance in shard.instances
                        ),
                    }
                    for shard in shards
                ],
            }
        )
    return rows


@pytest.mark.parametrize("mode", [ShardMode.STRIPE, ShardMode.BLOCK_STRIPE])
def test_cli_and_api_paths_produce_the_same_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: ShardMode
) -> None:
    # --- the API path: run_sharded_archive exactly as routes_archive calls it
    api_source = tmp_path / "api-src"
    api_source.mkdir()
    seed_source(api_source)
    api_context = build_context(tmp_path / "api.db")
    api_job = api_context.catalog.create_job("archive", {})
    run_sharded_archive(
        ShardedArchiveRequest(
            source_path=api_source,
            volume_group_name="shards",
            lane_barcodes=LANES,
            mode=mode,
            block_size=128 * 1024 * 1024,  # the route's default block_size_mb
        ),
        api_context.library,
        api_context.ltfs,
        api_context.catalog,
        DriveScheduler(num_drives=2),
        api_job.id,
    )
    api_rows = catalog_fingerprint(api_context, api_source)

    # --- the CLI path
    cli_source = tmp_path / "cli-src"
    cli_source.mkdir()
    seed_source(cli_source)
    cli_state = tmp_path / "cli-state"
    cli_state.mkdir()
    monkeypatch.setattr(cli_main, "_STATE_DIR", cli_state)
    monkeypatch.setattr(cli_main, "_STATE_PATH", cli_state / "mock_state.json")
    monkeypatch.setattr(cli_main, "_DB_PATH", cli_state / "openblade.db")
    monkeypatch.setenv("OPENBLADE_DB_URL", f"sqlite:///{cli_state / 'openblade.db'}")
    monkeypatch.setenv("OPENBLADE_CACHE_DIR", str(cli_state / "cache"))
    monkeypatch.setenv("OPENBLADE_STAGING_DIR", str(cli_state / "staging"))
    monkeypatch.setenv("OPENBLADE_RESTORE_DIR", str(cli_state / "restore"))
    monkeypatch.delenv("OPENBLADE_BACKEND", raising=False)

    init = runner.invoke(
        cli_main.app,
        ["mock", "init", "--slots", "6", "--drives", "2", "--cartridges", "2"],
    )
    assert init.exit_code == 0, init.output
    for barcode in LANES:
        dry = runner.invoke(cli_main.app, ["format", "dry-run", "--barcode", barcode])
        token = json.loads(dry.stdout)["token"]
        confirm = runner.invoke(
            cli_main.app,
            ["format", "confirm", "--barcode", barcode, "--token", token],
        )
        assert confirm.exit_code == 0, confirm.output

    result = runner.invoke(
        cli_main.app,
        [
            "archive", "sharded", str(cli_source),
            "--volume-group", "shards",
            "--mode", mode.value,
            "--lane-barcode", LANES[0],
            "--lane-barcode", LANES[1],
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["mode"] == mode.value
    assert payload["blockSizeMb"] == 128
    assert payload["errors"] == []

    cli_context = cli_main._get_context()
    cli_rows = catalog_fingerprint(cli_context, cli_source)

    assert cli_rows == api_rows
    assert cli_rows, "the fixture must actually archive something"
    assert payload["filesArchived"] == len(cli_rows)
