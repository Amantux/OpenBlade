#!/usr/bin/env python3
"""Restore campaign files from tape and byte-verify them against the manifest.

    python3 scripts/campaign/restore_and_verify.py \
        --volume-group campaign-plain \
        --dest /srv/openblade-campaign/restore/full \
        --manifest scripts/campaign/manifest.sha256.json

Three modes, all driven through the product's own ``RestoreService``:

* ``--all``            every catalogued file (the full-restore case)
* ``--sample N``       N files chosen deterministically across every tape
* ``--paths a,b,c``    named catalog paths (the selective-restore case)

Why this is a script and not one CLI call: **there is no bulk restore.**
``openblade restore`` takes one catalog path and one destination, and passing a
directory destination writes the file under its *basename only*, so restoring a
tree into one directory silently collapses same-named files from different
subdirectories. This script therefore recreates the relative path itself. See
docs/runbooks/real-data-campaign.md.

Files are restored grouped by barcode, in tape order, because each restore
mounts and unmounts LTFS. On this rig that costs ~1 s; on a real i3 a tape swap
is a minute, so the ordering is not cosmetic.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import defaultdict
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--volume-group", required=True)
    parser.add_argument("--dest", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--sample", type=int, default=0)
    parser.add_argument("--paths", default="")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)

    from openblade.bootstrap import create_context
    from openblade.config import load_config

    context = create_context(load_config())
    catalog = context.catalog

    group = catalog.get_volume_group(args.volume_group)
    if group is None:
        print(f"no such volume group: {args.volume_group}", file=sys.stderr)
        return 2

    # (catalog_path, barcode) for every archived instance in the group.
    targets: list[tuple[str, str]] = []
    for record in catalog.list_file_records(f"/{args.volume_group}"):
        path = record.path
        try:
            _, instance = catalog.get_latest_instance_for_path(path)
        except Exception:
            continue
        targets.append((path, instance.barcode))

    if args.paths:
        wanted = {p.strip() for p in args.paths.split(",") if p.strip()}
        targets = [t for t in targets if t[0] in wanted]
    elif args.sample:
        by_tape: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for item in targets:
            by_tape[item[1]].append(item)
        picked: list[tuple[str, str]] = []
        per_tape = max(1, args.sample // max(1, len(by_tape)))
        for barcode in sorted(by_tape):
            entries = sorted(by_tape[barcode])
            # Deterministic spread rather than the first N, which would only ever
            # exercise one directory.
            step = max(1, len(entries) // per_tape)
            picked.extend(entries[::step][:per_tape])
        targets = picked[: args.sample]
    elif not args.all:
        parser.error("choose one of --all, --sample N, --paths a,b")

    # Tape order matters: each restore mounts and unmounts.
    targets.sort(key=lambda item: (item[1], item[0]))

    manifest = json.loads(args.manifest.read_text())
    expected = {entry["path"]: entry for entry in manifest["entries"]}

    args.dest.mkdir(parents=True, exist_ok=True)
    prefix = f"/{args.volume_group}/"
    results: dict = {
        "ok": 0,
        "checksum_mismatch": [],
        "restore_failed": [],
        "not_in_manifest": [],
        # Not a failure -- a documented semantic difference. OpenBlade archives a
        # symlink by dereferencing it, so the restored object is a regular file
        # holding the target's bytes, not a link. Counting that as corruption
        # would bury the one number that matters (byte-identical regular files).
        "symlinks_dereferenced": [],
    }
    started = time.monotonic()
    total_bytes = 0

    for index, (catalog_path, barcode) in enumerate(targets, start=1):
        relative = catalog_path[len(prefix) :]
        destination = args.dest / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            context.restore_service.enqueue(catalog_path, destination)
        except Exception as exc:  # noqa: BLE001 -- the campaign wants every failure, typed or not
            results["restore_failed"].append(f"{catalog_path}: {type(exc).__name__}: {exc}")
            continue

        entry = expected.get(relative)
        if entry is None:
            results["not_in_manifest"].append(relative)
            continue
        actual = _sha256(destination)
        size = destination.stat().st_size
        total_bytes += size
        if entry["kind"] in {"symlink", "dangling-symlink"}:
            results["symlinks_dereferenced"].append(
                {
                    "path": relative,
                    "link_target": entry.get("target"),
                    "restored_as": "regular file",
                    "restored_size": size,
                }
            )
        elif actual != entry["sha256"] or size != entry["size"]:
            results["checksum_mismatch"].append(
                {
                    "path": relative,
                    "barcode": barcode,
                    "expected_sha256": entry["sha256"],
                    "actual_sha256": actual,
                    "expected_size": entry["size"],
                    "actual_size": size,
                }
            )
        else:
            results["ok"] += 1
        if index % 100 == 0:
            print(f"  {index}/{len(targets)} restored", file=sys.stderr)

    elapsed = time.monotonic() - started
    summary = {
        "volume_group": args.volume_group,
        "destination": str(args.dest),
        "requested": len(targets),
        "verified_ok": results["ok"],
        "symlinks_dereferenced": results["symlinks_dereferenced"],
        "checksum_mismatch": results["checksum_mismatch"],
        "restore_failed": results["restore_failed"],
        "not_in_manifest": results["not_in_manifest"],
        # The other direction: source entries the archive never catalogued at
        # all. Only meaningful for a full run; a sample legitimately misses most.
        "in_manifest_but_never_archived": (
            sorted(set(expected) - {t[0][len(prefix) :] for t in targets}) if args.all else []
        ),
        "bytes_restored": total_bytes,
        "tapes_touched": sorted({barcode for _, barcode in targets}),
        "elapsed_seconds": round(elapsed, 1),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if args.report:
        args.report.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    return 0 if not (results["checksum_mismatch"] or results["restore_failed"]) else 1


if __name__ == "__main__":
    sys.exit(main())
