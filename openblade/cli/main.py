"""Typer CLI for OpenBlade."""

from __future__ import annotations

import base64
import json
from datetime import datetime
from pathlib import Path, PurePosixPath

import typer
from rich.console import Console
from rich.table import Table

from openblade.bootstrap import AppContext, create_context, reset_context
from openblade.cli.assist import assist as assist_command
from openblade.config import OpenBladeConfig, load_config
from openblade.domain.errors import (
    CartridgeNotFoundError,
    DriveCorrelationError,
    ExportRefusedError,
    ImportExportSlotError,
    MailslotUnsupportedError,
    safe_job_error,
)
from openblade.domain.models import Barcode, DriveState, MountState
from openblade.fuse.filesystem import CatalogFilesystem
from openblade.hardware.validation import connect_quantum_i3, validate_ltfs_capabilities
from openblade.jobs.restore import RestoreRequest, run_restore_job
from openblade.jobs.scheduler import DriveScheduler
from openblade.jobs.shard import ShardMode
from openblade.jobs.sharded_archive import ShardedArchiveRequest, run_sharded_archive
from openblade.jobs.sharded_restore import ShardedRestoreRequest, run_sharded_restore
from openblade.jobs.tree_restore import (
    TreeRestoreProgress,
    TreeRestoreRequest,
    run_tree_restore,
)
from openblade.nas.mailslot import MailslotService
from openblade.nas.tape_orchestrator import TapeOperationFailedError, execute_tape_request
from openblade.nas.types import TapeOpRequest, TapeOpType
from openblade.simulator.library import MockLibraryBackend
from openblade.simulator.ltfs_volume import MockFileRecord, MockLTFSBackend, MockTapeContents

app = typer.Typer(name="openblade", help="OpenBlade tape archive controller")
mock_app = typer.Typer(help="Mock library commands")
app.add_typer(mock_app, name="mock")
format_app = typer.Typer(help="Format commands")
app.add_typer(format_app, name="format")
hardware_app = typer.Typer(help="Real hardware validation commands")
app.add_typer(hardware_app, name="hardware")
# `restore` and `archive` are groups that still work as bare commands: the old
# `openblade restore --path X --to Y` and `openblade archive --volume-group G
# --path P` invocations are what scripts/campaign/ runs, so they stay valid and
# the new subcommands hang off the same names.
restore_app = typer.Typer(help="Restore commands", invoke_without_command=True)
app.add_typer(restore_app, name="restore")
archive_app = typer.Typer(help="Archive commands", invoke_without_command=True)
app.add_typer(archive_app, name="archive")
mailslot_app = typer.Typer(help="Import/export (mailslot) commands")
app.add_typer(mailslot_app, name="mailslot")

# Read-only operator assistant. Registered from its own module so the assistant's
# dependencies stay out of this file; it is read-only by construction — see
# openblade/assistant/readonly.py for the three enforcement points.
app.command("assist")(assist_command)

console = Console()
# Progress, warnings, and anything else that is NOT the command's result go
# here. stdout carries the JSON result and nothing else, so
# `openblade ... | jq` works while the operator still sees what is happening.
err_console = Console(stderr=True)
_STATE_DIR = Path.home() / ".openblade"
_STATE_PATH = _STATE_DIR / "mock_state.json"
_DB_PATH = _STATE_DIR / "openblade.db"


def _default_config() -> OpenBladeConfig:
    """Resolve the CLI's config from the environment.

    This used to construct ``OpenBladeConfig()`` directly, which silently pinned
    the CLI to ``BackendMode.MOCK`` no matter what ``OPENBLADE_BACKEND`` said --
    so on a real library ``openblade inventory`` printed a plausible *simulated*
    inventory with no indication it was fiction. ``load_config()``'s own
    defaults for db_url/cache_dir/staging_dir/restore_dir are already the same
    ``~/.openblade`` paths this function hardcoded, so honouring the environment
    costs nothing and changes nothing for a mock-backed operator.
    """
    return load_config()


def _mock_config() -> OpenBladeConfig:
    """Config for the ``mock`` subcommands, which are simulator-only by definition."""
    from dataclasses import replace

    from openblade.config import BackendMode

    return replace(_default_config(), backend=BackendMode.MOCK, real_hardware_enabled=False)


def _is_mock(context: AppContext) -> bool:
    """True when this context is simulator-backed.

    ``mock_state.json`` is a snapshot of ``MockLibraryBackend``/``MockLTFSBackend``
    internals. Writing it from a real-hardware context would serialise attributes
    those backends do not have; *reading* it back over a real context replaces the
    live library with a simulation, which is the worse direction. Both are gated
    on this.
    """
    return isinstance(context.library, MockLibraryBackend) and isinstance(
        context.ltfs, MockLTFSBackend
    )


def _save_state(context: AppContext) -> None:
    # Narrowed here rather than through `_is_mock(context)` so the simulator-only
    # attributes below (`_slots`, `_ie_slots`, `_tapes`, ...) are statically known
    # to exist. Same gate, same outcome; see `_is_mock` for why it exists.
    library = context.library
    ltfs = context.ltfs
    if not isinstance(library, MockLibraryBackend) or not isinstance(ltfs, MockLTFSBackend):
        return
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "library": {
            "library_id": library.library_id,
            "num_slots": len(library.inventory().slots),
            "num_drives": len(library.inventory().drives),
            "slots": {
                str(slot_id): slot.barcode.value if slot.barcode is not None else None
                for slot_id, slot in library._slots.items()
            },
            "import_export_slots": {
                str(slot_id): slot.barcode.value if slot.barcode is not None else None
                for slot_id, slot in library._ie_slots.items()
            },
            "drives": {
                str(drive_id): {
                    "barcode": drive.barcode.value if drive.barcode is not None else None,
                    "drive_state": drive.drive_state.value,
                    "mount_state": drive.mount_state.value,
                }
                for drive_id, drive in library._drives.items()
            },
            "cartridge_states": {
                barcode: state.value for barcode, state in library._cartridge_states.items()
            },
        },
        "ltfs": {
            "capacity_bytes": ltfs.capacity_bytes,
            "tapes": {
                barcode: {
                    "used_bytes": tape.used_bytes,
                    "formatted": tape.formatted,
                    "mount_state": tape.mount_state.value,
                    "files": {
                        tape_path: {
                            "size_bytes": record.size_bytes,
                            "checksum_sha256": record.checksum_sha256,
                            "content": base64.b64encode(record.content).decode("ascii"),
                            "modified_at": record.modified_at.isoformat(),
                        }
                        for tape_path, record in tape.files.items()
                    },
                }
                for barcode, tape in ltfs._tapes.items()
            },
        },
    }
    _STATE_PATH.write_text(json.dumps(payload, indent=2))


def _load_state(context: AppContext) -> AppContext:
    if not _is_mock(context):
        # Real backend: the library itself is the state. Never shadow it.
        return context
    if not _STATE_PATH.exists():
        _save_state(context)
        return context
    try:
        payload = json.loads(_STATE_PATH.read_text())
        library_state = payload["library"]
        ltfs_state = payload["ltfs"]
        int(library_state["num_slots"])
        int(library_state["num_drives"])
    except (OSError, ValueError, TypeError, KeyError) as exc:
        # A state file written by an older release has a different shape. Losing
        # simulator scratch state is annoying; a traceback on every single command
        # with no documented recovery is worse. Say what happened and re-seed.
        stale = _STATE_PATH.with_suffix(".json.stale")
        _STATE_PATH.replace(stale)
        # stderr: this is a warning, not a result, and it must not land in the
        # middle of a JSON document a caller is piping into jq.
        err_console.print(
            f"[yellow]Ignoring unreadable mock state[/yellow] ({type(exc).__name__}: {exc}); "
            f"moved to {stale} and re-initialised from defaults."
        )
        _save_state(context)
        return context
    library_state = payload["library"]
    ltfs_state = payload["ltfs"]
    # Absent from state files written before the simulator had an I/E station:
    # default to no mailslot rather than refusing to load an older snapshot.
    ie_state: dict[str, str | None] = library_state.get("import_export_slots", {})
    library = MockLibraryBackend(
        library_id=library_state["library_id"],
        num_slots=library_state["num_slots"],
        num_drives=library_state["num_drives"],
        num_import_export_slots=len(ie_state),
    )
    for slot_id, barcode in library_state["slots"].items():
        if barcode is not None:
            library._slots[int(slot_id)].barcode = Barcode(barcode)
    # Element numbers round-trip verbatim; do not renumber them off num_slots.
    library.seed_import_export_slots(
        {int(slot_id): barcode for slot_id, barcode in ie_state.items()}
    )
    for drive_id, state in library_state["drives"].items():
        drive = library._drives[int(drive_id)]
        barcode = state["barcode"]
        drive.barcode = None if barcode is None else Barcode(barcode)
        drive.drive_state = DriveState(state["drive_state"])
        drive.mount_state = MountState(state["mount_state"])
    from openblade.domain.models import CartridgeState

    library._cartridge_states = {
        barcode: CartridgeState(state)
        for barcode, state in library_state["cartridge_states"].items()
    }
    ltfs = MockLTFSBackend(library, capacity_bytes=ltfs_state["capacity_bytes"])
    ltfs._tapes = {}
    from openblade.domain.models import MountState as TapeMountState

    for barcode, tape_state in ltfs_state["tapes"].items():
        tape = MockTapeContents(
            barcode=barcode,
            capacity_bytes=ltfs.capacity_bytes,
            used_bytes=tape_state["used_bytes"],
            formatted=tape_state["formatted"],
            mount_state=TapeMountState(tape_state["mount_state"]),
        )
        tape.files = {
            tape_path: MockFileRecord(
                tape_path=tape_path,
                size_bytes=record["size_bytes"],
                checksum_sha256=record["checksum_sha256"],
                content=base64.b64decode(record["content"]),
                modified_at=datetime.fromisoformat(record["modified_at"]),
            )
            for tape_path, record in tape_state["files"].items()
        }
        ltfs._tapes[barcode] = tape
    context.library = library
    context.ltfs = ltfs
    context.inventory_service.library = library
    context.format_service.library = library
    context.format_service.ltfs = ltfs
    context.archive_service.library = library
    context.archive_service.ltfs = ltfs
    context.restore_service.library = library
    context.restore_service.ltfs = ltfs
    return context


def _get_context(*, force_mock: bool = False) -> AppContext:
    """Build the CLI's app context.

    ``force_mock`` is for the ``mock`` subcommand group, which is simulator-only
    by definition. Without it, `openblade mock load --slot 3 --drive 0` in a
    shell that exports OPENBLADE_BACKEND=real issues a real `mtx load` against a
    real library -- a command whose name promises the opposite.
    """
    context = create_context(_mock_config() if force_mock else _default_config())
    context = _load_state(context)
    reset_context(context)
    return context


def _get_mock_context() -> AppContext:
    return _get_context(force_mock=True)


def _print_inventory(context: AppContext) -> None:
    inventory = context.library.inventory()
    slots = Table(title="Slots")
    slots.add_column("Slot")
    slots.add_column("Occupied")
    slots.add_column("Barcode")
    for slot in inventory.slots:
        slots.add_row(
            str(slot.slot_id), str(slot.occupied), str(slot.barcode) if slot.barcode else ""
        )
    drives = Table(title="Drives")
    drives.add_column("Drive")
    drives.add_column("Loaded")
    drives.add_column("Barcode")
    drives.add_column("Drive State")
    drives.add_column("Mount State")
    for drive in inventory.drives:
        drives.add_row(
            str(drive.drive_id),
            str(drive.barcode is not None),
            str(drive.barcode) if drive.barcode else "",
            drive.drive_state.value,
            drive.mount_state.value,
        )
    console.print(slots)
    console.print(drives)


@app.command()
def inventory() -> None:
    """Show current library inventory."""
    _print_inventory(_get_context())


@mock_app.command("init")
def mock_init(
    slots: int = typer.Option(20, help="Number of slots"),
    drives: int = typer.Option(1, help="Number of drives"),
    cartridges: int = typer.Option(5, help="Number of cartridges"),
    ie_slots: int = typer.Option(
        4,
        "--ie-slots",
        min=0,
        help="Number of import/export (mailslot) elements; 4 matches the Scalar i3",
    ),
) -> None:
    """Initialize a mock library and save state."""
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    if _DB_PATH.exists():
        _DB_PATH.unlink()
    # Explicitly mock: `openblade mock init` must not try to talk to a real
    # changer just because OPENBLADE_BACKEND=real is exported in this shell.
    config = _mock_config()
    context = create_context(config)
    library = MockLibraryBackend(
        num_slots=slots, num_drives=drives, num_import_export_slots=ie_slots
    )
    library.seed_slots([f"MCK{i:05d}" for i in range(1, cartridges + 1)])
    ltfs = MockLTFSBackend(library)
    context.library = library
    context.ltfs = ltfs
    context.inventory_service.library = library
    context.format_service.library = library
    context.format_service.ltfs = ltfs
    context.archive_service.library = library
    context.archive_service.ltfs = ltfs
    context.restore_service.library = library
    context.restore_service.ltfs = ltfs
    reset_context(context)
    _save_state(context)
    console.print(
        f"Initialized mock library with {slots} slots, {drives} drives, "
        f"{cartridges} cartridges, {ie_slots} import/export slots"
    )


@mock_app.command("inventory")
def mock_inventory() -> None:
    """Show mock library inventory."""
    _print_inventory(_get_mock_context())


@mock_app.command("load")
def mock_load(slot: int = typer.Option(...), drive: int = typer.Option(0)) -> None:
    """Load cartridge from slot into drive."""
    context = _get_mock_context()
    inventory = context.library.inventory()
    barcode = next(
        (
            slot_state.barcode.value
            for slot_state in inventory.slots
            if slot_state.slot_id == slot and slot_state.barcode is not None
        ),
        "",
    )
    record = execute_tape_request(
        context.catalog,
        context.library,
        context.ltfs,
        TapeOpRequest(
            op_type=TapeOpType.LOAD,
            barcode=barcode,
            drive_id=drive,
            slot_id=slot,
            requested_by="cli",
        ),
        raise_on_failed=True,
    )
    _save_state(context)
    console.print(record.result.get("message", "Loaded cartridge"))


@mock_app.command("unload")
def mock_unload(drive: int = typer.Option(0), slot: int = typer.Option(...)) -> None:
    """Unload cartridge from drive to slot."""
    context = _get_mock_context()
    inventory = context.library.inventory()
    barcode = next(
        (
            drive_state.barcode.value
            for drive_state in inventory.drives
            if drive_state.drive_id == drive and drive_state.barcode is not None
        ),
        "",
    )
    record = execute_tape_request(
        context.catalog,
        context.library,
        context.ltfs,
        TapeOpRequest(
            op_type=TapeOpType.UNLOAD,
            barcode=barcode,
            drive_id=drive,
            slot_id=slot,
            requested_by="cli",
        ),
        raise_on_failed=True,
    )
    _save_state(context)
    console.print(record.result.get("message", "Unloaded cartridge"))


@app.command("volume-group")
def volume_group_create(name: str) -> None:
    """Create a volume group."""
    context = _get_context()
    group = context.catalog.create_volume_group(name)
    console.print_json(data={"id": group.id, "name": group.name, "barcodes": group.barcodes})


@format_app.command("dry-run")
def format_dry_run(barcode: str = typer.Option(...)) -> None:
    """Show what format would do without doing it."""
    context = _get_context()
    plan, token = context.format_service.dry_run(barcode)
    _save_state(context)
    console.print_json(
        data={
            "operation": plan.operation,
            "target": plan.target,
            "affected_barcodes": plan.affected_barcodes,
            "warnings": plan.warnings,
            "is_destructive": plan.is_destructive,
            "token": token.token,
        }
    )


@format_app.command("confirm")
def format_confirm(
    barcode: str = typer.Option(...),
    token: str = typer.Option(...),
) -> None:
    """Format a tape with safety confirmation."""
    context = _get_context()
    result = context.format_service.confirm(barcode, token)
    _save_state(context)
    console.print_json(
        data={"success": result.success, "message": result.message, "details": result.details}
    )


@archive_app.callback(invoke_without_command=True)
def archive(
    ctx: typer.Context,
    volume_group: str | None = typer.Option(None),
    path: str | None = typer.Option(None),
) -> None:
    """Enqueue an archive job."""
    if ctx.invoked_subcommand is not None:
        return
    if volume_group is None or path is None:
        raise typer.BadParameter(
            "openblade archive needs --volume-group and --path "
            "(or a subcommand such as `openblade archive sharded`)"
        )
    context = _get_context()
    job = context.archive_service.enqueue(volume_group, Path(path))
    _save_state(context)
    console.print_json(data={"job_id": job.id, "status": job.state, "job_type": job.job_type})


@restore_app.callback(invoke_without_command=True)
def restore(
    ctx: typer.Context,
    path: str | None = typer.Option(None, help="Catalog path"),
    to: str | None = typer.Option(None, help="Local destination path"),
) -> None:
    """Restore a file from tape."""
    if ctx.invoked_subcommand is not None:
        return
    if path is None or to is None:
        raise typer.BadParameter(
            "openblade restore needs --path and --to "
            "(or a subcommand such as `openblade restore tree`)"
        )
    context = _get_context()
    job = context.restore_service.enqueue(path, Path(to))
    _save_state(context)
    console.print_json(data={"job_id": job.id, "status": job.state, "job_type": job.job_type})


@restore_app.command("file")
def restore_file(
    catalog_path: str = typer.Argument(..., help="Catalog path of the file to restore"),
    dest: Path = typer.Option(..., "--dest", help="Destination directory or file path"),
) -> None:
    """Restore one cataloged file, spanning tapes when it is sharded.

    Named form of the bare `openblade restore --path ... --to ...`, with one
    difference that matters: when `--dest` is a directory the file keeps its
    catalog basename inside it, and a sharded file is reassembled through
    `run_sharded_restore` rather than read as a single instance.
    """
    context = _get_context()
    scheduler = DriveScheduler(num_drives=len(context.library.inventory().drives))
    dest_path = dest / PurePosixPath(catalog_path).name if dest.is_dir() else dest
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    job = context.catalog.create_job(
        "restore", {"catalog_path": catalog_path, "dest_path": str(dest_path)}
    )
    record = context.catalog.get_file_record(catalog_path)
    if record is None:
        raise typer.BadParameter(f"{catalog_path} is not in the catalog")
    sharded = bool(context.catalog.list_shard_records(record.id)) or (record.shard_count or 1) > 1
    err_console.print(
        f"Restoring {catalog_path} -> {dest_path} "
        f"({'sharded' if sharded else 'single-instance'})"
    )
    try:
        if sharded:
            result = run_sharded_restore(
                ShardedRestoreRequest(catalog_path=catalog_path, dest_path=dest_path),
                context.library,
                context.ltfs,
                context.catalog,
                scheduler,
                job.id,
            )
            payload = {
                "jobId": job.id,
                "catalogPath": catalog_path,
                "destPath": str(dest_path),
                "sourceBarcodes": result.source_barcodes,
                "checksumVerified": result.checksum_verified,
                "bytesRestored": result.bytes_restored,
                "status": "failed" if result.error else "completed",
            }
            failed = result.error is not None
        else:
            plain = run_restore_job(
                RestoreRequest(catalog_path=catalog_path, dest_path=dest_path),
                context.library,
                context.ltfs,
                context.catalog,
                job.id,
            )
            payload = {
                "jobId": job.id,
                "catalogPath": catalog_path,
                "destPath": str(dest_path),
                "sourceBarcodes": [plain.source_barcode],
                "checksumVerified": plain.checksum_verified,
                "bytesRestored": record.size_bytes,
                "status": "completed",
            }
            failed = False
    except Exception as exc:
        message = safe_job_error(exc)
        context.catalog.update_job_state(job.id, "failed", error=message)
        _save_state(context)
        err_console.print(f"[red]Restore failed:[/red] {message}")
        raise typer.Exit(code=1) from None
    _save_state(context)
    console.print_json(data=payload)
    if failed:
        raise typer.Exit(code=1)


@restore_app.command("tree")
def restore_tree(
    catalog_prefix: str = typer.Argument(..., help="Catalog path prefix, e.g. /photo-archive"),
    dest: Path = typer.Option(..., "--dest", help="Destination directory"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan only; move no media"),
) -> None:
    """Restore every archived file under a catalog prefix, spanning tapes.

    The source tree is preserved under `--dest`: restoring `/vg` writes
    `/vg/a/x.txt` to `<dest>/a/x.txt`. Progress goes to stderr, the JSON summary
    to stdout, and any failure exits non-zero with the curated error.
    """
    context = _get_context()
    scheduler = DriveScheduler(num_drives=len(context.library.inventory().drives))
    job = context.catalog.create_job(
        "restore",
        {"catalog_prefix": catalog_prefix, "dest_dir": str(dest), "bulk": True},
    )

    def _progress(tick: TreeRestoreProgress) -> None:
        marker = "[red]FAIL[/red]" if tick.failed else "ok"
        err_console.print(
            f"[{tick.files_done}/{tick.files_total}] {marker} "
            f"{tick.barcode} {tick.last_path} ({tick.bytes_done} bytes so far)"
        )

    try:
        result = run_tree_restore(
            TreeRestoreRequest(
                catalog_prefix=catalog_prefix, dest_dir=dest, dry_run=dry_run
            ),
            context.library,
            context.ltfs,
            context.catalog,
            scheduler,
            job.id,
            progress=_progress,
        )
    except Exception as exc:
        message = safe_job_error(exc)
        context.catalog.update_job_state(job.id, "failed", error=message)
        _save_state(context)
        err_console.print(f"[red]Tree restore failed:[/red] {message}")
        raise typer.Exit(code=1) from None
    _save_state(context)
    console.print_json(data=result.to_dict())
    if not result.ok:
        err_console.print(
            f"[red]{result.files_failed} of "
            f"{result.files_restored + result.files_failed} file(s) failed[/red]"
        )
        raise typer.Exit(code=1)


def _resolve_lane_barcodes(
    context: AppContext,
    volume_group: str,
    lanes: int | None,
    explicit: list[str] | None,
) -> list[str]:
    """Lane barcodes for a sharded archive.

    `--lane-barcode` is authoritative when given. Otherwise the lanes come from
    the volume group's own cartridges, skipping any the catalog says is
    `exported` -- the same filter `jobs/archive.py` applies when it picks a
    target tape, so the CLI cannot select media that is out of the library.
    """
    if explicit:
        return list(explicit)
    group = context.catalog.get_volume_group(volume_group)
    if group is None:
        raise typer.BadParameter(f"Volume group {volume_group} does not exist")
    available = [
        cartridge.barcode
        for cartridge in sorted(group.cartridges, key=lambda item: item.barcode)
        if cartridge.state != "exported"
    ]
    if not available:
        # Common on a first sharded archive: run_sharded_archive only links a
        # lane cartridge to the volume group when the cartridge row does not
        # already exist, so media the catalog already knows about never joins
        # the group and there is nothing here to pick from. Name the fix.
        raise typer.BadParameter(
            f"No cartridge is assigned to volume group {volume_group}; "
            "pass the lanes explicitly with --lane-barcode "
            "(repeat the option once per lane)"
        )
    if lanes is None:
        return available
    if lanes > len(available):
        raise typer.BadParameter(
            f"--lanes {lanes} but volume group {volume_group} has only "
            f"{len(available)} usable cartridge(s): {', '.join(available)}"
        )
    return available[:lanes]


def _parse_shard_mode(mode: str) -> ShardMode:
    normalized = mode.strip().lower().replace("-", "_")
    try:
        return ShardMode(normalized)
    except ValueError:
        raise typer.BadParameter(
            f"unknown mode {mode!r}; expected one of: "
            + ", ".join(member.value for member in ShardMode)
        ) from None


@archive_app.command("sharded")
def archive_sharded(
    source: Path = typer.Argument(..., help="Source file or directory to archive"),
    volume_group: str = typer.Option(..., "--volume-group", help="Target volume group"),
    mode: str = typer.Option("stripe", "--mode", help="stripe | block-stripe"),
    lanes: int | None = typer.Option(
        None, "--lanes", min=1, help="Number of lane cartridges to use (default: all in the group)"
    ),
    lane_barcode: list[str] | None = typer.Option(
        None, "--lane-barcode", help="Explicit lane barcode; repeatable, overrides --lanes"
    ),
    block_size_mb: int = typer.Option(128, "--block-size-mb", min=1),
) -> None:
    """Archive across several drives in parallel (the CLI half of POST /archive/sharded).

    Wraps `run_sharded_archive` with the same request model and the same
    defaults the API route uses, including its refusal of block_stripe with
    fewer than two lanes.
    """
    if not source.exists():
        raise typer.BadParameter(f"Source path {source} not found")
    shard_mode = _parse_shard_mode(mode)
    context = _get_context()
    lane_barcodes = _resolve_lane_barcodes(context, volume_group, lanes, lane_barcode)
    if shard_mode is ShardMode.BLOCK_STRIPE and len(lane_barcodes) < 2:
        # Same refusal as the API route: a block-striped file is split ACROSS
        # lanes, so one lane is not a sharded archive, it is a silent rename.
        raise typer.BadParameter("block_stripe mode requires at least 2 lane barcodes")

    job = context.catalog.create_job(
        "archive",
        {
            "source_path": str(source),
            "volume_group": volume_group,
            "lane_barcodes": lane_barcodes,
            "mode": shard_mode.value,
            "block_size_mb": block_size_mb,
        },
    )
    scheduler = DriveScheduler(num_drives=len(context.library.inventory().drives))
    err_console.print(
        f"Sharded archive {source} -> {volume_group} "
        f"mode={shard_mode.value} lanes={','.join(lane_barcodes)}"
    )
    try:
        result = run_sharded_archive(
            ShardedArchiveRequest(
                source_path=source,
                volume_group_name=volume_group,
                lane_barcodes=lane_barcodes,
                mode=shard_mode,
                block_size=block_size_mb * 1024 * 1024,
            ),
            context.library,
            context.ltfs,
            context.catalog,
            scheduler,
            job.id,
        )
    except Exception as exc:
        message = safe_job_error(exc)
        context.catalog.update_job_state(job.id, "failed", error=message)
        _save_state(context)
        err_console.print(f"[red]Sharded archive failed:[/red] {message}")
        raise typer.Exit(code=1) from None
    _save_state(context)
    refreshed = context.catalog.get_job(job.id)
    console.print_json(
        data={
            "jobId": result.job_id,
            "status": refreshed.state if refreshed is not None else "unknown",
            "sourcePath": str(source),
            "volumeGroup": volume_group,
            "mode": shard_mode.value,
            "laneBarcodes": lane_barcodes,
            "blockSizeMb": block_size_mb,
            "filesArchived": result.files_archived,
            "bytesArchived": result.bytes_archived,
            "tapesUsed": result.tapes_used,
            "shardGroupIds": result.shard_group_ids,
            "errors": result.errors,
        }
    )
    if result.errors:
        raise typer.Exit(code=1)


def _mailslot_service() -> tuple[AppContext, MailslotService]:
    context = _get_context()
    return context, MailslotService(context.catalog, context.library, context.ltfs)


def _mailslot_exit(exc: Exception) -> typer.Exit:
    """Curated, typed failure -> exit 1. Never a traceback at this boundary."""
    err_console.print(f"[red]{type(exc).__name__}:[/red] {exc}")
    return typer.Exit(code=1)


@mailslot_app.command("list")
def mailslot_list() -> None:
    """Show the import/export (I/E) station: which slots hold which barcodes."""
    context, service = _mailslot_service()
    try:
        listing = service.list_slots()
    except (MailslotUnsupportedError, ImportExportSlotError) as exc:
        raise _mailslot_exit(exc) from None
    _save_state(context)
    console.print_json(data=listing.to_dict())


@mailslot_app.command("import")
def mailslot_import(
    ie_slot: int = typer.Argument(..., help="Import/export element holding the cartridge"),
    to_slot: int | None = typer.Option(
        None, "--to-slot", help="Storage slot to import into (default: first empty)"
    ),
) -> None:
    """Move a cartridge from an I/E slot into library storage."""
    context, service = _mailslot_service()
    try:
        result = service.import_cartridge(ie_slot, to_slot)
    except (
        MailslotUnsupportedError,
        ImportExportSlotError,
        CartridgeNotFoundError,
        TapeOperationFailedError,
    ) as exc:
        raise _mailslot_exit(exc) from None
    _save_state(context)
    err_console.print(
        f"Imported {result.barcode} from I/E slot {result.source_slot} into storage slot "
        f"{result.destination_slot}"
        + (" (chosen automatically)" if result.slot_was_chosen else "")
    )
    console.print_json(data=result.to_dict())


@mailslot_app.command("export")
def mailslot_export(
    barcode: str = typer.Argument(..., help="Barcode of the cartridge to export"),
    ie_slot: int | None = typer.Option(
        None, "--ie-slot", help="Import/export element to use (default: first empty)"
    ),
    force: bool = typer.Option(
        False, "--force", help="Export even though the cartridge carries archived data"
    ),
) -> None:
    """Move a cartridge out of storage into the I/E station.

    This is how data walks out of the library: once exported, every file on that
    cartridge is unrestorable until it is imported back. It refuses by default
    when the cartridge (or its volume group) still holds archived files, and
    names what is on it.
    """
    context, service = _mailslot_service()
    try:
        result = service.export_cartridge(barcode, ie_slot=ie_slot, force=force)
    except ExportRefusedError as exc:
        err_console.print(f"[red]Export refused:[/red] {exc}")
        raise typer.Exit(code=1) from None
    except (
        MailslotUnsupportedError,
        ImportExportSlotError,
        CartridgeNotFoundError,
        TapeOperationFailedError,
    ) as exc:
        raise _mailslot_exit(exc) from None
    _save_state(context)
    err_console.print(
        f"Exported {result.barcode} from storage slot {result.source_slot} to I/E slot "
        f"{result.destination_slot}"
        + (" (chosen automatically)" if result.slot_was_chosen else "")
    )
    console.print_json(data=result.to_dict())


@app.command()
def jobs(job_id: str | None = typer.Argument(None)) -> None:
    """Show job status."""
    context = _get_context()
    if job_id is not None:
        job = context.catalog.get_job(job_id)
        if job is None:
            raise typer.BadParameter(f"Unknown job {job_id}")
        console.print_json(
            data={
                "id": job.id,
                "state": job.state,
                "job_type": job.job_type,
                "error": job.error,
                "metadata": job.metadata_dict,
            }
        )
        return
    table = Table(title="Jobs")
    table.add_column("ID")
    table.add_column("Type")
    table.add_column("State")
    table.add_column("Error")
    for job in context.catalog.list_jobs():
        table.add_row(job.id, job.job_type, job.state, job.error or "")
    console.print(table)


@app.command("catalog")
def catalog_ls(path: str = typer.Argument("/")) -> None:
    """List files in the catalog."""
    context = _get_context()
    filesystem = CatalogFilesystem(context.catalog, cache_dir=context.config.cache_dir)
    entries = filesystem.listdir(path)
    table = Table(title=f"Catalog {path}")
    table.add_column("Name")
    table.add_column("Type")
    table.add_column("Size")
    table.add_column("Path")
    for entry in entries:
        table.add_row(
            entry.name, "dir" if entry.is_dir else "file", str(entry.size_bytes), str(entry.path)
        )
    console.print(table)


@hardware_app.command("connect-i3")
def hardware_connect_i3() -> None:
    """Validate guarded Quantum i3 discovery and inventory wiring."""
    try:
        report = connect_quantum_i3(load_config())
    except DriveCorrelationError as exc:
        # This is the operator-facing diagnostic: show the curated message, not a
        # traceback. The message names the variable to fix.
        console.print(f"[red]Drive correlation failed:[/red] {exc}")
        raise typer.Exit(code=1) from None
    console.print_json(data=report.to_dict())


@hardware_app.command("validate-ltfs")
def hardware_validate_ltfs(
    device: str = typer.Option(
        ..., help="No-rewind tape device path such as /dev/nst0 (never the rewinding /dev/stN)"
    ),
    barcode: str = typer.Option(..., help="Barcode used for LTFS format planning"),
    mount_point: str | None = typer.Option(None, help="Mount point for optional mount checks"),
    exercise_mounts: bool = typer.Option(
        False,
        help="Attempt readonly and readwrite mount/unmount checks in addition to device discovery",
    ),
) -> None:
    """Validate LTFS discovery, planning, and optional mount capability."""
    report = validate_ltfs_capabilities(
        load_config(),
        device=device,
        barcode=barcode,
        mount_point=None if mount_point is None else Path(mount_point),
        exercise_mounts=exercise_mounts,
    )
    console.print_json(data=report.to_dict())


if __name__ == "__main__":  # pragma: no cover — the console script calls app() via the entry point
    # Without this, `python -m openblade.cli.main …` imports the module,
    # prints nothing, and exits 0 — which reads as success to a script.
    app()
