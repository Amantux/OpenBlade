"""Tests for the shard planning and reassembly engine."""

import hashlib
from pathlib import Path

from openblade.jobs.shard import (
    ShardMode,
    compute_checksum,
    plan_block_stripe,
    plan_stripe,
    reassemble_block_stripe,
    write_shard_to_tempfile,
)


def _make_file(tmp_path: Path, size_bytes: int, content: bytes | None = None) -> Path:
    file_path = tmp_path / "testfile.bin"
    if content is not None:
        file_path.write_bytes(content)
    else:
        file_path.write_bytes(bytes(range(256)) * (size_bytes // 256) + bytes(size_bytes % 256))
    return file_path


def test_plan_stripe_single_lane(tmp_path: Path) -> None:
    source_file = _make_file(tmp_path, 1024)
    plan = plan_stripe(source_file, ["TAPE01L8"], "/photos")
    assert plan.mode == ShardMode.STRIPE
    assert len(plan.shards) == 1
    assert plan.shards[0].barcode == "TAPE01L8"
    assert plan.shards[0].shard_total == 1
    assert plan.file_size == 1024


def test_plan_block_stripe_splits_correctly(tmp_path: Path) -> None:
    data = bytes(range(256)) * 40
    source_file = _make_file(tmp_path, len(data), content=data)
    plan = plan_block_stripe(source_file, ["T1", "T2", "T3"], "/archive", block_size=1024)
    assert plan.mode == ShardMode.BLOCK_STRIPE
    assert len(plan.shards) == 3
    total = sum(shard.block_end - shard.block_start for shard in plan.shards)
    assert total == len(data)


def test_block_stripe_roundtrip(tmp_path: Path) -> None:
    original_data = bytes(range(256)) * 100
    source_file = tmp_path / "original.bin"
    source_file.write_bytes(original_data)

    block_size = 1024
    shard_files = [
        write_shard_to_tempfile(source_file, index, 3, block_size, tmp_path) for index in range(3)
    ]

    total_shard_bytes = sum(path.stat().st_size for path in shard_files)
    assert total_shard_bytes == len(original_data)

    dest = tmp_path / "restored.bin"
    checksum = reassemble_block_stripe(shard_files, dest, block_size)

    assert dest.read_bytes() == original_data
    assert checksum == hashlib.sha256(original_data).hexdigest()


def test_block_stripe_roundtrip_various_sizes(tmp_path: Path) -> None:
    for size in [1, 100, 1023, 1024, 1025, 3000, 10000]:
        data = bytes(index % 256 for index in range(size))
        source_file = tmp_path / f"file_{size}.bin"
        source_file.write_bytes(data)
        shard_files = [
            write_shard_to_tempfile(source_file, index, 3, 512, tmp_path) for index in range(3)
        ]
        dest = tmp_path / f"restored_{size}.bin"
        reassemble_block_stripe(shard_files, dest, 512)
        assert dest.read_bytes() == data


def test_compute_checksum(tmp_path: Path) -> None:
    file_path = tmp_path / "f.txt"
    file_path.write_bytes(b"hello world")
    checksum = compute_checksum(file_path)
    assert checksum == hashlib.sha256(b"hello world").hexdigest()
