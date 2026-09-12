from __future__ import annotations

"""CLI commands for the read-only FUSE mount and for drive health (TapeAlert).

Kept in its own module so ``openblade/cli/main.py`` only gains a registration
call. Nothing here is imported at ``main`` import time beyond this module.
"""

import logging
from collections.abc import Callable
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from openblade.config import load_config
from openblade.domain.errors import RealHardwareDisabledError
from openblade.fuse.filesystem import CatalogFilesystem
from openblade.fuse.mount import FuseUnavailableError, mount_catalog
from openblade.hardware.discovery import discover_library
from openblade.hardware.runner import SafeRunner
from openblade.hardware.safety import require_real_hardware
from openblade.hardware.sg import sg_inq
from openblade.hardware.tapealert import TapeAlertSeverity, read_tape_alerts

console = Console()

fuse_app = typer.Typer(help="Read-only FUSE mount over the catalog namespace")

_SEVERITY_STYLE = {
    TapeAlertSeverity.CRITICAL: "red",
    TapeAlertSeverity.WARNING: "yellow",
    TapeAlertSeverity.INFORMATION: "cyan",
    TapeAlertSeverity.UNKNOWN: "magenta",
}


@fuse_app.command("mount")
def fuse_mount(
    mountpoint: str = typer.Argument(..., help="Existing empty directory to mount on"),
    hydrate: bool = typer.Option(
        False,
        "--hydrate",
        help=(
            "Fetch uncached files from tape during read(). OFF by default: a read "
            "then blocks for a full load/mount/restore cycle (tens of seconds on "
            "real hardware) and any process that touches the file -- including "
            "`ls` previewers and indexers -- can trigger one."
        ),
    ),
    allow_other: bool = typer.Option(
        False,
        "--allow-other",
        help="Expose the mount to other users on the host (needs user_allow_other in /etc/fuse.conf)",
    ),
    verbose: bool = typer.Option(False, "--verbose", help="Log every FUSE decision to stderr"),
) -> None:
    """Mount the catalog namespace read-only. Runs in the foreground.

    Unmount from another shell with ``fusermount -u <mountpoint>``, or stop this
    process with Ctrl-C.
    """
    from openblade.cli.main import _get_context  # local: main imports this module

    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO)
    context = _get_context()
    filesystem = CatalogFilesystem(context.catalog, cache_dir=context.config.cache_dir)

    def _hydrate_through_restore(catalog_path: str) -> bytes:
        """Restore through the existing restore service, then cache the bytes."""
        record = context.catalog.get_file_record(catalog_path)
        if record is None:
            raise FileNotFoundError(catalog_path)
        staging = Path(context.config.restore_dir) / "fuse-hydrate" / record.id
        staging.parent.mkdir(parents=True, exist_ok=True)
        context.restore_service.enqueue(catalog_path, staging)
        data = staging.read_bytes()
        filesystem.cache.store(record.checksum_sha256, data)
        return data

    hydrator: Callable[[str], bytes] | None = _hydrate_through_restore if hydrate else None

    console.print(
        f"[green]Mounting[/green] catalog at {mountpoint} "
        f"(read-only, hydrate={'on' if hydrate else 'off'}, allow_other={allow_other})"
    )
    try:
        mount_catalog(
            filesystem,
            mountpoint,
            hydrator=hydrator,
            allow_other=allow_other,
        )
    except (FuseUnavailableError, NotADirectoryError) as exc:
        # markup=False: the install hint contains `openblade[fuse]`, which rich
        # would otherwise eat as a style tag -- leaving the operator with the
        # instruction's most important word missing.
        console.print(str(exc), style="red", markup=False)
        raise typer.Exit(code=1) from None


def drive_health(
    device: str | None = typer.Option(
        None,
        "--device",
        help="Tape device to inspect, e.g. /dev/nst0. Default: every discovered drive.",
    ),
) -> None:
    """Report drive inquiry data and TapeAlert flags.

    Requires real hardware to be enabled. A drive that does not implement the
    TapeAlert log page is reported as such and is not an error.
    """
    config = load_config()
    try:
        guard = require_real_hardware(config)
    except RealHardwareDisabledError as exc:
        console.print(f"[red]Drive health unavailable:[/red] {exc}")
        raise typer.Exit(code=1) from None

    runner = SafeRunner(dry_run=config.hardware_dry_run)
    if device is not None:
        devices = [device]
    else:
        discovery = discover_library(runner, guard)
        devices = [
            node
            for node in (drive.sg_device or drive.block_device for drive in discovery.drives)
            if node is not None
        ]
        if not devices:
            console.print("[yellow]No tape drives discovered.[/yellow]")
            return

    for target in devices:
        inquiry = sg_inq(target, runner, guard)
        console.print(
            f"\n[bold]{target}[/bold]  {inquiry.vendor} {inquiry.product} "
            f"rev {inquiry.revision} serial {inquiry.serial or '(none reported)'}"
        )
        report = read_tape_alerts(target, runner, guard)
        if not report.supported:
            console.print(f"  TapeAlert: not supported by this drive ({report.reason})")
            continue
        active = report.active
        if not active:
            console.print(f"  TapeAlert: no flags set ({len(report.flags)} flags read)")
            continue
        table = Table(title=f"TapeAlert flags set on {target}")
        table.add_column("Flag")
        table.add_column("Name")
        table.add_column("Severity")
        for flag in active:
            style = _SEVERITY_STYLE.get(flag.severity, "")
            table.add_row(
                "?" if flag.number is None else str(flag.number),
                flag.name,
                f"[{style}]{flag.severity}[/{style}]" if style else str(flag.severity),
            )
        console.print(table)


def register(app: typer.Typer, hardware_app: typer.Typer) -> None:
    """Attach this module's commands to the root app and the hardware sub-app."""
    app.add_typer(fuse_app, name="fuse")
    hardware_app.command("drive-health")(drive_health)
