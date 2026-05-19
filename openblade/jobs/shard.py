"""Shard engine: splits files across N tape lanes and reassembles them on restore."""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_BLOCK_SIZE = 128 * 1024 * 1024
BLOCK_STRIPE_THRESHOLD = 64 * 1024 * 1024


class ShardMode(str, Enum):
    NONE = "none"
    STRIPE = "stripe"
    BLOCK_STRIPE = "block_stripe"


@dataclass
class ShardSpec:
    """Describes one shard of a file."""

    shard_index: int
    shard_total: int
    barcode: str
    tape_path: str
    block_start: int
    block_end: int
    shard_group_id: str


@dataclass
class ShardPlan:
    """Plan for sharding a file."""

    source_path: Path
    file_size: int
    checksum_sha256: str
    mode: ShardMode
    shards: list[ShardSpec]
    shard_group_id: str = field(default_factory=lambda: str(uuid.uuid4()))


def compute_checksum(path: Path) -> str:
    hash_obj = hashlib.sha256()
    with path.open("rb") as source_handle:
        for chunk in iter(lambda: source_handle.read(1024 * 1024), b""):
            hash_obj.update(chunk)
    return hash_obj.hexdigest()


def plan_stripe(
    source_path: Path,
    lane_barcodes: list[str],
    catalog_base_path: str,
) -> ShardPlan:
    """Create a single-lane stripe plan for one file."""
    assert len(lane_barcodes) == 1, "STRIPE mode takes exactly one lane"
    file_size = source_path.stat().st_size
    checksum = compute_checksum(source_path)
    group_id = str(uuid.uuid4())
    tape_path = f"{catalog_base_path}/{source_path.name}"
    spec = ShardSpec(
        shard_index=0,
        shard_total=1,
        barcode=lane_barcodes[0],
        tape_path=tape_path,
        block_start=0,
        block_end=file_size,
        shard_group_id=group_id,
    )
    return ShardPlan(
        source_path=source_path,
        file_size=file_size,
        checksum_sha256=checksum,
        mode=ShardMode.STRIPE,
        shards=[spec],
        shard_group_id=group_id,
    )


def plan_block_stripe(
    source_path: Path,
    lane_barcodes: list[str],
    catalog_base_path: str,
    block_size: int = DEFAULT_BLOCK_SIZE,
) -> ShardPlan:
    """Split a file into block-striped shards across multiple lanes."""
    lane_count = len(lane_barcodes)
    assert lane_count >= 2, "BLOCK_STRIPE requires at least 2 lanes"

    file_size = source_path.stat().st_size
    checksum = compute_checksum(source_path)
    group_id = str(uuid.uuid4())
    num_blocks = max(1, (file_size + block_size - 1) // block_size)

    lane_sizes = [0] * lane_count
    for block_index in range(num_blocks):
        lane_index = block_index % lane_count
        block_start = block_index * block_size
        block_end = min(block_start + block_size, file_size)
        lane_sizes[lane_index] += block_end - block_start

    shards: list[ShardSpec] = []
    for lane_index, barcode in enumerate(lane_barcodes):
        tape_path = f"{catalog_base_path}/{group_id}/{source_path.name}.shard{lane_index:04d}"
        shards.append(
            ShardSpec(
                shard_index=lane_index,
                shard_total=lane_count,
                barcode=barcode,
                tape_path=tape_path,
                block_start=0,
                block_end=lane_sizes[lane_index],
                shard_group_id=group_id,
            )
        )

    return ShardPlan(
        source_path=source_path,
        file_size=file_size,
        checksum_sha256=checksum,
        mode=ShardMode.BLOCK_STRIPE,
        shards=shards,
        shard_group_id=group_id,
    )


def write_shard_to_tempfile(
    source_path: Path,
    shard_index: int,
    shard_total: int,
    block_size: int,
    tmp_dir: Path,
) -> Path:
    """Extract bytes for one shard into a temporary file in tmp_dir."""
    out_path = tmp_dir / f"shard_{shard_index:04d}.tmp"
    with source_path.open("rb") as source_handle, out_path.open("wb") as dest_handle:
        block_index = 0
        while True:
            chunk = source_handle.read(block_size)
            if not chunk:
                break
            if block_index % shard_total == shard_index:
                dest_handle.write(chunk)
            block_index += 1
    return out_path


def reassemble_block_stripe(
    shard_files: list[Path],
    dest_path: Path,
    block_size: int = DEFAULT_BLOCK_SIZE,
) -> str:
    """Reassemble shard files into the original file and return its checksum."""
    handles = [path.open("rb") for path in shard_files]
    hash_obj = hashlib.sha256()
    try:
        with dest_path.open("wb") as dest_handle:
            done = False
            while not done:
                for shard_handle in handles:
                    chunk = shard_handle.read(block_size)
                    if not chunk:
                        done = True
                        break
                    dest_handle.write(chunk)
                    hash_obj.update(chunk)
    finally:
        for shard_handle in handles:
            shard_handle.close()
    return hash_obj.hexdigest()
