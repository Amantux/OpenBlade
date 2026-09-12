<!-- GENERATED FILE -- do not edit by hand.
     Regenerate with: python3 tools/gen_wiki_reference.py
     Guarded by: tests/unit/test_wiki_reference_generated.py -->

# CLI reference

Every command exposed by the `openblade` Typer application, introspected
from `openblade.cli.main:app`.

`pip install -e .` puts the `openblade` console script on your PATH.
Every command below also accepts `--help`.

> ℹ️ **Backend selection:** every command builds its backend through
> `load_config()`, so `OPENBLADE_BACKEND` / `OPENBLADE_REAL_HARDWARE_ENABLED`
> apply to the CLI exactly as to the API. (Before the real-data campaign
> the CLI silently ignored them and printed simulator data — fixed and
> regression-tested; see docs/runbooks/real-data-campaign.md.) Real-
> hardware commands still refuse without both flags set.

**21 commands**, in 7 sub-group(s) plus the top level.

## Command groups

| Group | Purpose |
| --- | --- |
| `openblade archive` | Archive commands |
| `openblade format` | Format commands |
| `openblade fuse` | Read-only FUSE mount over the catalog namespace |
| `openblade hardware` | Real hardware validation commands |
| `openblade mailslot` | Import/export (mailslot) commands |
| `openblade mock` | Mock library commands |
| `openblade restore` | Restore commands |

## Commands

### `openblade archive sharded`

Archive across several drives in parallel (the CLI half of POST /archive/sharded).

| Parameter | Kind | Type | Required | Default | Help |
| --- | --- | --- | --- | --- | --- |
| `SOURCE` | argument | `PATH` | yes | - | Source file or directory to archive |
| `--volume-group` | option | `STR` | yes | - | Target volume group |
| `--mode` | option | `STR` | no | `stripe` | stripe | block-stripe |
| `--lanes` | option | `INT RANGE` | no | none | Number of lane cartridges to use (default: all in the group) |
| `--lane-barcode` | option | `STR` | no | none | Explicit lane barcode; repeatable, overrides --lanes |
| `--block-size-mb` | option | `INT RANGE` | no | `128` | - |

### `openblade assist`

Ask the OpenBlade assistant about this installation, or talk through setup.

| Parameter | Kind | Type | Required | Default | Help |
| --- | --- | --- | --- | --- | --- |
| `QUESTION` | argument | `STR` | no | none | Ask one question and exit. Omit for an interactive session. |

### `openblade catalog`

List files in the catalog.

| Parameter | Kind | Type | Required | Default | Help |
| --- | --- | --- | --- | --- | --- |
| `PATH` | argument | `STR` | no | `/` | - |

### `openblade format confirm`

Format a tape with safety confirmation.

| Parameter | Kind | Type | Required | Default | Help |
| --- | --- | --- | --- | --- | --- |
| `--barcode` | option | `STR` | yes | - | - |
| `--token` | option | `STR` | yes | - | - |

### `openblade format dry-run`

Show what format would do without doing it.

| Parameter | Kind | Type | Required | Default | Help |
| --- | --- | --- | --- | --- | --- |
| `--barcode` | option | `STR` | yes | - | - |

### `openblade fuse mount`

Mount the catalog namespace read-only. Runs in the foreground.

| Parameter | Kind | Type | Required | Default | Help |
| --- | --- | --- | --- | --- | --- |
| `MOUNTPOINT` | argument | `STR` | yes | - | Existing empty directory to mount on |
| `--hydrate` | option | `BOOLEAN` | no | false | Fetch uncached files from tape during read(). OFF by default: a read then blocks for a full load/mount/restore cycle (tens of seconds on real hardware) and any process that touches the file -- including `ls` previewers and indexers -- can trigger one. |
| `--allow-other` | option | `BOOLEAN` | no | false | Expose the mount to other users on the host (needs user_allow_other in /etc/fuse.conf) |
| `--verbose` | option | `BOOLEAN` | no | false | Log every FUSE decision to stderr |

### `openblade hardware connect-i3`

Validate guarded Quantum i3 discovery and inventory wiring.

Takes no arguments or options.

### `openblade hardware drive-health`

Report drive inquiry data and TapeAlert flags.

| Parameter | Kind | Type | Required | Default | Help |
| --- | --- | --- | --- | --- | --- |
| `--device` | option | `STR` | no | none | Tape device to inspect, e.g. /dev/nst0. Default: every discovered drive. |

### `openblade hardware validate-ltfs`

Validate LTFS discovery, planning, and optional mount capability.

| Parameter | Kind | Type | Required | Default | Help |
| --- | --- | --- | --- | --- | --- |
| `--device` | option | `STR` | yes | - | No-rewind tape device path such as /dev/nst0 (never the rewinding /dev/stN) |
| `--barcode` | option | `STR` | yes | - | Barcode used for LTFS format planning |
| `--mount-point` | option | `STR` | no | none | Mount point for optional mount checks |
| `--exercise-mounts` | option | `BOOLEAN` | no | false | Attempt readonly and readwrite mount/unmount checks in addition to device discovery |

### `openblade inventory`

Show current library inventory.

Takes no arguments or options.

### `openblade jobs`

Show job status.

| Parameter | Kind | Type | Required | Default | Help |
| --- | --- | --- | --- | --- | --- |
| `JOB_ID` | argument | `STR` | no | none | - |

### `openblade mailslot export`

Move a cartridge out of storage into the I/E station.

| Parameter | Kind | Type | Required | Default | Help |
| --- | --- | --- | --- | --- | --- |
| `BARCODE` | argument | `STR` | yes | - | Barcode of the cartridge to export |
| `--ie-slot` | option | `INT` | no | none | Import/export element to use (default: first empty) |
| `--force` | option | `BOOLEAN` | no | false | Export even though the cartridge carries archived data |

### `openblade mailslot import`

Move a cartridge from an I/E slot into library storage.

| Parameter | Kind | Type | Required | Default | Help |
| --- | --- | --- | --- | --- | --- |
| `IE_SLOT` | argument | `INT` | yes | - | Import/export element holding the cartridge |
| `--to-slot` | option | `INT` | no | none | Storage slot to import into (default: first empty) |

### `openblade mailslot list`

Show the import/export (I/E) station: which slots hold which barcodes.

Takes no arguments or options.

### `openblade mock init`

Initialize a mock library and save state.

| Parameter | Kind | Type | Required | Default | Help |
| --- | --- | --- | --- | --- | --- |
| `--slots` | option | `INT` | no | `20` | Number of slots |
| `--drives` | option | `INT` | no | `1` | Number of drives |
| `--cartridges` | option | `INT` | no | `5` | Number of cartridges |
| `--ie-slots` | option | `INT RANGE` | no | `4` | Number of import/export (mailslot) elements; 4 matches the Scalar i3 |

### `openblade mock inventory`

Show mock library inventory.

Takes no arguments or options.

### `openblade mock load`

Load cartridge from slot into drive.

| Parameter | Kind | Type | Required | Default | Help |
| --- | --- | --- | --- | --- | --- |
| `--slot` | option | `INT` | yes | - | - |
| `--drive` | option | `INT` | no | `0` | - |

### `openblade mock unload`

Unload cartridge from drive to slot.

| Parameter | Kind | Type | Required | Default | Help |
| --- | --- | --- | --- | --- | --- |
| `--drive` | option | `INT` | no | `0` | - |
| `--slot` | option | `INT` | yes | - | - |

### `openblade restore file`

Restore one cataloged file, spanning tapes when it is sharded.

| Parameter | Kind | Type | Required | Default | Help |
| --- | --- | --- | --- | --- | --- |
| `CATALOG_PATH` | argument | `STR` | yes | - | Catalog path of the file to restore |
| `--dest` | option | `PATH` | yes | - | Destination file path, or a directory (pass --into-dir, or an existing directory) to keep the catalog basename |
| `--into-dir` | option | `BOOLEAN` | no | false | Treat --dest as a directory even if it does not exist yet |

### `openblade restore tree`

Restore every archived file under a catalog prefix, spanning tapes.

| Parameter | Kind | Type | Required | Default | Help |
| --- | --- | --- | --- | --- | --- |
| `CATALOG_PREFIX` | argument | `STR` | yes | - | Catalog path prefix, e.g. /photo-archive |
| `--dest` | option | `PATH` | yes | - | Destination directory |
| `--dry-run` | option | `BOOLEAN` | no | false | Plan only; move no media |

### `openblade volume-group`

Create a volume group.

| Parameter | Kind | Type | Required | Default | Help |
| --- | --- | --- | --- | --- | --- |
| `NAME` | argument | `STR` | yes | - | - |
