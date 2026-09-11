#!/usr/bin/env bash
#
# LTFS-format the rig's scratch cartridges.
#
# The archive/restore and sharded jobs MOUNT a scratch tape; they do not format
# it first. On blank media that fails with
#   LTFS17168E Cannot read volume: medium is not partitioned.
# surfacing as "readwrite mount failed". Phase 4 of
# docs/runbooks/real-i3-bringup-plan.md sequences format before
# archive/restore for exactly this reason, so the rig supplies formatted
# scratch media as a precondition.
#
# DESTRUCTIVE. Every cartridge it touches is confirmed three times before
# mkltfs runs:
#   1. the barcode is validated as a well-formed, fully-specified barcode;
#   2. it is matched against the whole VolumeTag, never a substring;
#   3. after loading, the drive is re-read and the LOADED cartridge's VolumeTag
#      must equal the requested barcode.
# AGENTS.md: "Never perform format or erase operations without positive barcode
# confirmation." Step 3 is that confirmation - steps 1 and 2 only decide what
# to load.
#
# Usage:  sudo scripts/mhvtl/format-scratch.sh [BARCODE ...]

set -euo pipefail

# shellcheck source=scripts/mhvtl/_rig.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_rig.sh"

if [ "$#" -gt 0 ]; then
  SCRATCH_BARCODES=("$@")
else
  SCRATCH_BARCODES=(OB0007L8 OB0008L8)
fi

log()  { printf '\n=== %s\n' "$*"; }
fail() { rig_die "$@"; }

command -v mkltfs >/dev/null \
  || fail "mkltfs not installed - cannot format. The changer, discovery and
         drive-health suites still run without LTFS; see
         docs/runbooks/mhvtl-rehearsal.md for what that leaves untested."
command -v mtx    >/dev/null || fail "mtx not installed"
command -v lsscsi >/dev/null || fail "lsscsi not installed"
command -v sg_inq >/dev/null || fail "sg_inq not installed (sg3-utils)"

# Validate EVERY barcode before touching anything, so a bad argument in
# position 2 cannot be discovered only after position 1 has been formatted.
for barcode in "${SCRATCH_BARCODES[@]}"; do
  rig_require_valid_barcode "$barcode"
done

# Moving media while LTFS holds a drive can discard an unwritten index.
rig_require_no_ltfs

changer=$(rig_changer)

# Drive 0 means the changer's Data Transfer Element 0, which is the FIRST drive
# in SCSI address order. It is NOT the lowest-numbered /dev/stN: mhvtl hands out
# st nodes in daemon registration order, and we regularly see DTE 0 on /dev/st1.
drive0_sg=$(rig_drive_sgs "$changer" | head -1)
[ -n "$drive0_sg" ] || fail "no tape drives found on this rig"

printf 'changer: %s\ndrive 0: %s (serial %s)\n' \
  "$changer" "$drive0_sg" "$(rig_serial_of "$drive0_sg")"
printf 'will format: %s\n' "${SCRATCH_BARCODES[*]}"

for barcode in "${SCRATCH_BARCODES[@]}"; do
  log "Formatting $barcode"

  slot=$(rig_find_slot "$changer" "$barcode")
  [ -n "$slot" ] || fail "barcode $barcode is not in a storage slot.
         Run scripts/mhvtl/reset.sh first to return media to their slots.
         (Note this matches the WHOLE VolumeTag - a partial barcode will not
         find anything, which is deliberate.)"

  printf 'loading slot %s -> drive 0\n' "$slot"
  mtx -f "$changer" load "$slot" 0

  # Positive barcode confirmation. Re-read the changer and check what is
  # ACTUALLY in the drive. Without this, a slot-selection bug, a robotics
  # misfeed, or a concurrent move would silently format the wrong cartridge -
  # mkltfs formats whatever it finds, it does not check barcodes.
  loaded=$(rig_barcode_in_drive "$changer" 0)
  if [ "$loaded" != "$barcode" ]; then
    mtx -f "$changer" unload "$slot" 0 || true
    fail "drive 0 holds '${loaded:-<empty>}' but '$barcode' was requested.
         Refusing to format. Nothing was written."
  fi
  printf 'confirmed: drive 0 holds %s\n' "$loaded"

  # --volume-name, NOT --tape-serial: the latter takes exactly 6 alphanumeric
  # characters and an 8-character LTO barcode fails with
  # "LTFS15029E Tape serial must be 6 characters."
  if mkltfs --device="$drive0_sg" --volume-name="$barcode" --force >/dev/null 2>&1; then
    printf 'formatted %s\n' "$barcode"
  else
    mtx -f "$changer" unload "$slot" 0 || true
    fail "mkltfs failed for $barcode. Re-run the command by hand to see why:
         mkltfs --device=$drive0_sg --volume-name=$barcode --force"
  fi

  printf 'unloading drive 0 -> slot %s\n' "$slot"
  mtx -f "$changer" unload "$slot" 0
done

log "Scratch media formatted"
printf '%s\n' "${SCRATCH_BARCODES[@]}"
