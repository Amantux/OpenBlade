# OpenBlade — Sharding & Parallel I/O

## The Problem

A tape drive maxes out at ~300 MB/s (LTO-8 native). The Quantum Scalar i3 supports up to 6 drives. Naive single-stream jobs use one drive — leaving 5 idle. OpenBlade solves this with a shard engine and a drive scheduler that allocates N drives atomically and reads/writes them simultaneously.

---

## Sharding Modes

| Mode | How it works | Best for |
|------|-------------|----------|
| `STRIPE` | Whole files assigned round-robin to tape lanes | Many small/medium files |
| `BLOCK_STRIPE` | Single large file split into fixed-size blocks across N tapes | Multi-TB single files |
| `ERASURE` *(future)* | k-of-(k+m) Reed-Solomon sharding | Fault tolerance |

See [`docs/diagrams/shard-design.md`](diagrams/shard-design.md) for detailed flow diagrams and throughput models.

---

## Key Components

### `DriveScheduler` (`openblade/jobs/scheduler.py`)

Thread-safe allocator. Atomically locks N drives for a job, queues others.

```python
scheduler = DriveScheduler(num_drives=4)

# Acquire 3 drives simultaneously (or wait up to 300s)
handles = scheduler.acquire_drives(["TAPE01L8", "TAPE02L8", "TAPE03L8"])
# ... do parallel I/O ...
scheduler.release_drives(handles)
```

### `ShardEngine` (`openblade/jobs/shard.py`)

Plans and reassembles shards.

```python
# STRIPE: assign file to one lane
plan = plan_stripe(source_file, [barcode], "/photos")

# BLOCK_STRIPE: split 9 GB file across 3 tapes
plan = plan_block_stripe(source_file, [b1, b2, b3], "/archive", block_size=1_073_741_824)
```

### `ShardedArchiveJob` (`openblade/jobs/sharded_archive.py`)

Parallel write to N drives simultaneously.

### `ShardedRestoreJob` (`openblade/jobs/sharded_restore.py`)

Parallel read from N drives, then reassemble.

---

## STRIPE Example (file-level parallelism)

```
VolumeGroup "photos", shard_width=3, mode=STRIPE

Lane 0 → PHOTO01L8   Lane 1 → PHOTO02L8   Lane 2 → PHOTO03L8
──────────────────   ──────────────────   ──────────────────
a.jpg (200 MB)       b.jpg (150 MB)       c.jpg (300 MB)
d.jpg (100 MB)       e.jpg (200 MB)       f.jpg (180 MB)

Restoring [a.jpg, b.jpg, c.jpg] simultaneously:
  Drive 0 ← PHOTO01L8 → reads a.jpg at 300 MB/s
  Drive 1 ← PHOTO02L8 → reads b.jpg at 300 MB/s   } all parallel
  Drive 2 ← PHOTO03L8 → reads c.jpg at 300 MB/s
  Total throughput: ~900 MB/s
```

## BLOCK_STRIPE Example (single large file)

```
bigfile.tar (9 GB), block_size=1 GB, 3 lanes

Block 0 (0–1 GB)  → ARCH01L8:/shards/bigfile.tar/shard0000
Block 1 (1–2 GB)  → ARCH02L8:/shards/bigfile.tar/shard0001
Block 2 (2–3 GB)  → ARCH03L8:/shards/bigfile.tar/shard0002
Block 3 (3–4 GB)  → ARCH01L8:/shards/bigfile.tar/shard0003
...

Restore:
  Drive 0 ← ARCH01L8 → reads blocks 0,3,6 → shard_0.tmp ─┐
  Drive 1 ← ARCH02L8 → reads blocks 1,4,7 → shard_1.tmp ─┼→ reassemble → bigfile.tar
  Drive 2 ← ARCH03L8 → reads blocks 2,5,8 → shard_2.tmp ─┘
  Effective throughput: 3× 300 MB/s = ~900 MB/s
```

---

## Throughput Model

| Drives | Mode | Theoretical max |
|--------|------|----------------|
| 1 | NONE | 300 MB/s |
| 2 | STRIPE | 600 MB/s |
| 3 | BLOCK_STRIPE | 900 MB/s |
| 4 | BLOCK_STRIPE | 1.2 GB/s |
| 6 | BLOCK_STRIPE | 1.8 GB/s |

*Assumes LTO-8 native 300 MB/s. Compressed data can reach 750 MB/s per drive.*

---

## API

```
POST /archive/sharded
{
  "source_path": "/staging/photos",
  "volume_group": "photos",
  "lane_barcodes": ["PHOTO1L8", "PHOTO2L8", "PHOTO3L8"],
  "mode": "stripe",
  "block_size_mb": 1024
}
→ {"job_id": "...", "status": "pending"}
```

---

## Safety invariants preserved under sharding

- All N shards must verify checksum before any shard is marked `archived`
- If any shard write fails, all shards for that file are marked `failed`
- Restore always uses `READ_ONLY` mounts, even on shard tapes
- DriveScheduler never grants the same drive to two jobs
- All drives are released (even on error) via try/finally
