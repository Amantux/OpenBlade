"""Typer CLI for OpenBlade."""

from __future__ import annotations

import base64
import json
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from openblade.bootstrap import AppContext, create_context, reset_context
from openblade.cli.assist import assist as assist_command
from openblade.config import OpenBladeConfig, load_config
from openblade.domain.errors import DriveCorrelationError
from openblade.domain.models import Barcode, DriveState, MountState
from openblade.fuse.filesystem import CatalogFilesystem
from openblade.hardware.validation import connect_quantum_i3, validate_ltfs_capabilities
from openblade.nas.tape_orchestrator import execute_tape_request
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

# Read-only operator assistant. Registered from its own module so the assistant's
# dependencies stay out of this file; it is read-only by construction — see
# openblade/assistant/readonly.py for the three enforcement points.
app.command("assist")(assist_command)

console = Console()
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
    if not _is_mock(context):
        return
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "library": {
            "library_id": context.library.library_id,
            "num_slots": len(context.library.inventory().slots),
            "num_drives": len(context.library.inventory().drives),
            "slots": {
                str(slot_id): slot.barcode.value if slot.barcode is not None else None
                for slot_id, slot in context.library._slots.items()
            },
            "drives": {
                str(drive_id): {
                    "barcode": drive.barcode.value if drive.barcode is not None else None,
                    "drive_state": drive.drive_state.value,
                    "mount_state": drive.mount_state.value,
                }
                for drive_id, drive in context.library._drives.items()
            },
            "cartridge_states": {
                barcode: state.value for barcode, state in context.library._cartridge_states.items()
            },
        },
        "ltfs": {
            "capacity_bytes": context.ltfs.capacity_bytes,
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
                for barcode, tape in context.ltfs._tapes.items()
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
        console.print(
            f"[yellow]Ignoring unreadable mock state[/yellow] ({type(exc).__name__}: {exc}); "
            f"moved to {stale} and re-initialised from defaults."
        )
        _save_state(context)
        return context
    library_state = payload["library"]
    ltfs_state = payload["ltfs"]
    library = MockLibraryBackend(
        library_id=library_state["library_id"],
        num_slots=library_state["num_slots"],
        num_drives=library_state["num_drives"],
    )
    for slot_id, barcode in library_state["slots"].items():
        if barcode is not None:
            library._slots[int(slot_id)].barcode = Barcode(barcode)
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
) -> None:
    """Initialize a mock library and save state."""
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    if _DB_PATH.exists():
        _DB_PATH.unlink()
    # Explicitly mock: `openblade mock init` must not try to talk to a real
    # changer just because OPENBLADE_BACKEND=real is exported in this shell.
    config = _mock_config()
    context = create_context(config)
    library = MockLibraryBackend(num_slots=slots, num_drives=drives)
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
        f"Initialized mock library with {slots} slots, {drives} drives, {cartridges} cartridges"
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


@app.command()
def archive(
    volume_group: str = typer.Option(...),
    path: str = typer.Option(...),
) -> None:
    """Enqueue an archive job."""
    context = _get_context()
    job = context.archive_service.enqueue(volume_group, Path(path))
    _save_state(context)
    console.print_json(data={"job_id": job.id, "status": job.state, "job_type": job.job_type})


@app.command()
def restore(
    path: str = typer.Option(..., help="Catalog path"),
    to: str = typer.Option(..., help="Local destination path"),
) -> None:
    """Restore a file from tape."""
    context = _get_context()
    job = context.restore_service.enqueue(path, Path(to))
    _save_state(context)
    console.print_json(data={"job_id": job.id, "status": job.state, "job_type": job.job_type})


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
