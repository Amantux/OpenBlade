"""Before/after library-state snapshots for the destructive hardware lane.

    python -m tools.hardware.snapshot capture --out before.json
    python -m tools.hardware.snapshot diff before.json after.json --out diff.json

``capture`` reads inventory + drive state through ``InventoryService`` on the
library the configured backend builds (``OPENBLADE_BACKEND``; the simulator by
default, real only when ``OPENBLADE_REAL_HARDWARE_ENABLED=true`` too). It is
read-only, and refuses (exit 2, ``snapshot: REFUSED:`` on stderr, no file
written) when the configured backend is real but ``OPENBLADE_HARDWARE_DRY_RUN``
is true: dry-run never touches the changer, so the capture would not describe
the library the lane is about to change. ``diff`` compares two captures and writes the diff file, then exits
0 when identical (library_id included), 1 when they differ, 2 on unreadable
input.
"""

from __future__ import annotations

import argparse
import dataclasses
import enum
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openblade.config import BackendMode, OpenBladeConfig
from openblade.domain.backends import LibraryBackend

EXIT_OK = 0
EXIT_CHANGED = 1
EXIT_BAD_INPUT = 2
EXIT_REFUSED = 2


class SnapshotError(Exception):
    """A snapshot file is missing or malformed."""


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, enum.Enum):
        return _jsonable(value.value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return str(value)


def capture_snapshot(library: LibraryBackend) -> dict[str, Any]:
    """Return a JSON-able snapshot of ``library``'s inventory and drive state."""
    from openblade.jobs.inventory import InventoryService

    inventory = InventoryService(library).snapshot()
    return {
        "captured_at": datetime.now(UTC).isoformat(),
        "library_id": inventory.library_id,
        "slots": [_jsonable(s) for s in inventory.slots],
        "drives": [_jsonable(d) for d in inventory.drives],
        "changer_state": _jsonable(inventory.changer_state),
    }


def _index(items: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    return {str(item.get(key, i)): item for i, item in enumerate(items)}


def _diff_section(
    before: list[dict[str, Any]], after: list[dict[str, Any]], key: str
) -> list[dict[str, Any]]:
    b, a = _index(before, key), _index(after, key)
    changes: list[dict[str, Any]] = []
    for ident in sorted(b.keys() | a.keys()):
        if b.get(ident) != a.get(ident):
            changes.append({key: ident, "before": b.get(ident), "after": a.get(ident)})
    return changes


def diff_snapshots(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Compare two captures element-by-element (slots by slot_id, drives by drive_id)."""
    slots = _diff_section(before.get("slots", []), after.get("slots", []), "slot_id")
    drives = _diff_section(before.get("drives", []), after.get("drives", []), "drive_id")
    library_changed = before.get("library_id") != after.get("library_id")
    changer = (
        []
        if before.get("changer_state") == after.get("changer_state")
        else [{"before": before.get("changer_state"), "after": after.get("changer_state")}]
    )
    return {
        "library_id": {"before": before.get("library_id"), "after": after.get("library_id")},
        "slots": slots,
        "drives": drives,
        "changer_state": changer,
        "changed": bool(library_changed or slots or drives or changer),
    }


def _load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise SnapshotError(f"cannot read snapshot {path}: {exc.__class__.__name__}") from exc
    if not isinstance(data, dict):
        raise SnapshotError(f"snapshot {path} is not a JSON object")
    return data


def dry_run_refusal(config: OpenBladeConfig) -> str | None:
    """Return why ``capture`` must refuse under ``config``, or None when it may run."""
    if config.backend is BackendMode.REAL and config.hardware_dry_run:
        return (
            "OPENBLADE_HARDWARE_DRY_RUN=true with OPENBLADE_BACKEND=real: a dry-run "
            "capture does not reflect the physical library."
        )
    return None


def _build_library(config: OpenBladeConfig) -> LibraryBackend:
    from openblade.bootstrap import create_context

    return create_context(config).library


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.hardware.snapshot")
    sub = parser.add_subparsers(dest="command", required=True)
    cap = sub.add_parser("capture", help="Capture inventory + drive state to JSON.")
    cap.add_argument("--out", required=True, type=Path)
    dif = sub.add_parser("diff", help="Diff two captures.")
    dif.add_argument("before", type=Path)
    dif.add_argument("after", type=Path)
    dif.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    if args.command == "capture":
        from openblade.config import load_config

        config = load_config()
        reason = dry_run_refusal(config)
        if reason is not None:
            print(f"snapshot: REFUSED: {reason}", file=sys.stderr)
            return EXIT_REFUSED
        snap = capture_snapshot(_build_library(config))
        args.out.write_text(json.dumps(snap, indent=2, sort_keys=True))
        print(f"snapshot: captured {len(snap['slots'])} slots, {len(snap['drives'])} drives")
        return EXIT_OK
    try:
        result = diff_snapshots(_load(args.before), _load(args.after))
    except SnapshotError as exc:
        print(f"snapshot: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True))
    print(
        f"snapshot diff: {len(result['slots'])} slot change(s), "
        f"{len(result['drives'])} drive change(s), "
        f"changer {'changed' if result['changer_state'] else 'unchanged'}"
    )
    return EXIT_CHANGED if result["changed"] else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
