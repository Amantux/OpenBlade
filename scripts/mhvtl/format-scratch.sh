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
# DESTRUCTIVE, by design: it formats the barcodes named below and nothing else.
# Those are the same barcodes env.sh publishes as OPENBLADE_SCRATCH_BARCODES.
#
# Usage:  sudo scripts/mhvtl/format-scratch.sh [BARCODE ...]

set -euo pipefail

SCRATCH_BARCODES=("${@:-}")
[ -z "${SCRATCH_BARCODES[0]:-}" ] && SCRATCH_BARCODES=(OB0007L8 OB0008L8)

log()  { printf '\n=== %s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

command -v mkltfs >/dev/null \
  || fail "mkltfs not installed - cannot format. The changer, discovery and
         drive-health suites still run without LTFS; see
         docs/runbooks/mhvtl-rehearsal.md for what that leaves untested."
command -v mtx    >/dev/null || fail "mtx not installed"
command -v lsscsi >/dev/null || fail "lsscsi not installed"

scsi=$(lsscsi -g)
changer=$(printf '%s\n' "$scsi" | awk '/ mediumx /{print $NF}' | head -1)
case "$changer" in
  /dev/sg*) ;;
  *) fail "no changer with an sg node found - is the rig up?" ;;
esac

# Drive 0 means the changer's Data Transfer Element 0, which is the FIRST drive
# in SCSI address order - the same ordering openblade.hardware.library uses.
# It is NOT the lowest-numbered /dev/stN: mhvtl hands out st nodes in daemon
# registration order, and we regularly see DTE 0 sitting on /dev/st1.
# lsscsi already sorts by [host:bus:target:lun], so "first tape row" is right.
drive0_sg=$(printf '%s\n' "$scsi" | awk '/ tape /{print $NF; exit}')
[ -n "$drive0_sg" ] || fail "no tape drives found"

drive0_serial=$(sg_inq "$drive0_sg" 2>/dev/null | awk -F': *' '/Unit serial number/{print $2}')
printf 'changer: %s\ndrive 0: %s (serial %s)\n' "$changer" "$drive0_sg" "${drive0_serial:-unknown}"

find_slot() {
  mtx -f "$changer" status | awk -v want="$1" '
    /^ *Storage Element [0-9]+/ {
      if (index($0, want) == 0) next
      slot = $3; sub(/:.*/, "", slot); sub(/[^0-9].*/, "", slot)
      print slot; exit
    }'
}

for barcode in "${SCRATCH_BARCODES[@]}"; do
  log "Formatting $barcode"

  slot=$(find_slot "$barcode")
  [ -n "$slot" ] || fail "barcode $barcode is not in a storage slot.
         Run scripts/mhvtl/reset.sh first to return media to their slots."

  printf 'loading slot %s -> drive 0\n' "$slot"
  mtx -f "$changer" load "$slot" 0

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
