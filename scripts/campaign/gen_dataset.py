#!/usr/bin/env python3
"""Generate the deterministic real-data campaign dataset.

The same seed always produces byte-identical files, so the manifest written
here is reproducible on any host -- including the real Quantum i3 later. Run:

    python3 scripts/campaign/gen_dataset.py --root /srv/openblade-campaign/data \
        --manifest scripts/campaign/manifest.sha256.json

Shape (see ``--help`` for knobs):

* ~1,150 files, ~430 MB total
* nested directories, 5 levels deep
* many small text files, several 10-50 MB binaries, one 120 MB file
* unicode and spaces in names, empty files, one symlink

Symlinks and empty files are recorded in the manifest with their own kinds so
that a restore comparison can assert on them explicitly rather than skipping
them silently.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

SEED = 20260911
CHUNK = 1 << 20

# Directory names deliberately include a space and non-ASCII characters: LTFS
# stores UTF-8 names and the campaign is the first thing to prove the whole
# path round-trips through mtx/LTFS/catalog/FUSE without mangling.
DIRS = [
    "documents",
    "documents/reports 2026",
    "documents/reports 2026/q1",
    "documents/reports 2026/q2",
    "documents/rapports-français",
    "media",
    "media/images",
    "media/images/raw",
    "media/video",
    "datasets",
    "datasets/日本語データ",
    "datasets/nested/deep/deeper/deepest",
    "logs",
    "empty-dir",
]

LOREM = (
    "the quick brown fox jumps over the lazy dog while the tape drive streams "
    "at three hundred megabytes per second and the robot arm waits its turn "
)


@dataclass
class Entry:
    path: str
    kind: str  # file | empty | symlink
    size: int
    sha256: str
    target: str | None = None


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            block = handle.read(CHUNK)
            if not block:
                break
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def _write_binary(path: Path, size: int, rng: random.Random) -> None:
    """Write ``size`` pseudo-random bytes deterministically and in bounded memory.

    ``random.Random.randbytes`` is seeded per file from the master rng, so file
    content depends only on the seed and the file's ordinal -- not on the order
    in which the caller happens to flush them.
    """
    local = random.Random(rng.getrandbits(64))
    remaining = size
    with path.open("wb") as handle:
        while remaining > 0:
            block = min(CHUNK, remaining)
            handle.write(local.randbytes(block))
            remaining -= block


def _write_text(path: Path, approx_size: int, rng: random.Random) -> None:
    local = random.Random(rng.getrandbits(64))
    parts: list[str] = []
    total = 0
    line = 0
    while total < approx_size:
        line += 1
        words = LOREM.split()
        local.shuffle(words)
        text = f"{line:06d} {' '.join(words[: local.randint(4, len(words))])}\n"
        parts.append(text)
        total += len(text)
    path.write_text("".join(parts), encoding="utf-8")


def generate(root: Path, *, seed: int = SEED, clean: bool = True) -> list[Entry]:
    if clean and root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    for name in DIRS:
        (root / name).mkdir(parents=True, exist_ok=True)

    rng = random.Random(seed)
    created: list[Path] = []

    # 1. Bulk of the file count: ~1,000 small text files spread over the tree.
    text_dirs = [d for d in DIRS if d != "empty-dir"]
    for index in range(1000):
        parent = root / text_dirs[index % len(text_dirs)]
        name = f"note-{index:04d}.txt"
        if index % 37 == 0:
            name = f"rapport été {index:04d}.txt"
        elif index % 23 == 0:
            name = f"記録 {index:04d}.txt"
        target = parent / name
        _write_text(target, rng.randint(512, 64 * 1024), rng)
        created.append(target)

    # 2. Mid-size binaries: 100 KB - 4 MB.
    for index in range(60):
        target = root / "media/images/raw" / f"frame-{index:04d}.bin"
        _write_binary(target, rng.randint(100 * 1024, 4 * 1024 * 1024), rng)
        created.append(target)

    # 3. Several 10-50 MB binaries, the ones that make lane balance visible.
    for index in range(6):
        target = root / "media/video" / f"clip-{index:02d}.mov"
        _write_binary(target, rng.randint(10, 45) * 1024 * 1024, rng)
        created.append(target)

    # 4. One >100 MB file -- the BLOCK_STRIPE / tape-spanning candidate.
    big = root / "datasets" / "bigblob.dat"
    _write_binary(big, 120 * 1024 * 1024, rng)
    created.append(big)

    # 5. Empty files (a real archive always has some; they are the classic
    #    off-by-one in a chunked writer).
    for index in range(5):
        target = root / "logs" / f"empty-{index}.log"
        target.write_bytes(b"")
        created.append(target)

    # 6. Two symlinks, recorded separately. Whether the product stores the link
    #    or dereferences it is exactly the sort of thing this campaign must
    #    state -- and the DANGLING one matters just as much, because a source
    #    tree in the wild has them and the operator needs to know whether the
    #    job says so or silently archives fewer files than they handed it.
    #    note-0001.txt lands in text_dirs[1] == "documents/reports 2026".
    link = root / "documents" / "latest-report.txt"
    link_target = "reports 2026/note-0001.txt"
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(link_target)
    assert link.resolve().is_file(), "the campaign's valid symlink must actually resolve"

    dangling = root / "documents" / "missing-report.txt"
    dangling_target = "reports 2026/q2/note-does-not-exist.txt"
    if dangling.exists() or dangling.is_symlink():
        dangling.unlink()
    dangling.symlink_to(dangling_target)

    entries: list[Entry] = []
    for path in sorted(created):
        rel = path.relative_to(root).as_posix()
        checksum, size = _sha256_file(path)
        entries.append(Entry(path=rel, kind="empty" if size == 0 else "file", size=size, sha256=checksum))
    entries.append(
        Entry(
            path=link.relative_to(root).as_posix(),
            kind="symlink",
            size=0,
            sha256=hashlib.sha256(link_target.encode()).hexdigest(),
            target=link_target,
        )
    )
    entries.append(
        Entry(
            path=dangling.relative_to(root).as_posix(),
            kind="dangling-symlink",
            size=0,
            sha256=hashlib.sha256(dangling_target.encode()).hexdigest(),
            target=dangling_target,
        )
    )
    return sorted(entries, key=lambda e: e.path)


def write_manifest(entries: list[Entry], manifest: Path, root: Path, seed: int) -> dict:
    payload = {
        "seed": seed,
        "root": str(root),
        "generator": "scripts/campaign/gen_dataset.py",
        "counts": {
            "total": len(entries),
            "files": sum(1 for e in entries if e.kind == "file"),
            "empty": sum(1 for e in entries if e.kind == "empty"),
            "symlinks": sum(1 for e in entries if e.kind == "symlink"),
            "dangling_symlinks": sum(1 for e in entries if e.kind == "dangling-symlink"),
        },
        "total_bytes": sum(e.size for e in entries),
        "entries": [{k: v for k, v in asdict(e).items() if v is not None} for e in entries],
    }
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--no-clean", action="store_true")
    args = parser.parse_args(argv)

    entries = generate(args.root, seed=args.seed, clean=not args.no_clean)
    payload = write_manifest(entries, args.manifest, args.root, args.seed)
    counts = payload["counts"]
    print(
        f"generated {counts['total']} entries "
        f"({counts['files']} files, {counts['empty']} empty, "
        f"{counts['symlinks']} symlink, {counts['dangling_symlinks']} dangling) "
        f"{payload['total_bytes'] / 1024 / 1024:.1f} MiB under {args.root}"
    )
    print(f"manifest: {args.manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
