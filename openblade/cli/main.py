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
from openblade.config import OpenBladeConfig
from openblade.domain.models import Barcode, DriveState, MountState
from openblade.fuse.filesystem import CatalogFilesystem
from openblade.simulator.library import MockLibraryBackend
from openblade.simulator.ltfs_volume import MockFileRecord, MockLTFSBackend, MockTapeContents

app = typer.Typer(name="openblade", help="OpenBlade tape archive controller")
mock_app = typer.Typer(help="Mock library commands")
app.add_typer(mock_app, name="mock")
format_app = typer.Typer(help="Format commands")
app.add_typer(format_app, name="format")

console = Console()
_STATE_DIR = Path.home() / ".openblade"
_STATE_PATH = _STATE_DIR / "mock_state.json"
_DB_PATH = _STATE_DIR / "openblade.db"


def _default_config() -> OpenBladeConfig:
    return OpenBladeConfig(
        db_url=f"sqlite:///{_DB_PATH}",
        cache_dir=str(_STATE_DIR / "cache"),
        restore_dir=str(_STATE_DIR / "restore"),
        staging_dir=str(_STATE_DIR / "staging"),
    )


def _save_state(context: AppContext) -> None:
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
    if not _STATE_PATH.exists():
        _save_state(context)
        return context
    payload = json.loads(_STATE_PATH.read_text())
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


def _get_context() -> AppContext:
    context = create_context(_default_config())
    context = _load_state(context)
    reset_context(context)
    return context


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
    config = _default_config()
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
    _print_inventory(_get_context())


@mock_app.command("load")
def mock_load(slot: int = typer.Option(...), drive: int = typer.Option(0)) -> None:
    """Load cartridge from slot into drive."""
    context = _get_context()
    result = context.library.load(slot, drive)
    _save_state(context)
    console.print(result.message)


@mock_app.command("unload")
def mock_unload(drive: int = typer.Option(0), slot: int = typer.Option(...)) -> None:
    """Unload cartridge from drive to slot."""
    context = _get_context()
    result = context.library.unload(drive, slot)
    _save_state(context)
    console.print(result.message)


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
