#!/usr/bin/env bash
#
# Return the mhvtl rehearsal rig to its at-rest state: every drive empty, every
# tape back in a storage slot.
#
# The hardware suite leaves media loaded when a test fails part way through, and
# several tests skip themselves with "Drive 0 is not empty" or "Scratch barcode
# ... is not currently present in the library" if you just re-run. Run this
# between passes so each run starts from the same place.
#
# A failed test run is also exactly when an LTFS mount is most likely to have
# been left behind, so this REFUSES to move anything while LTFS holds a drive -
# see the project non-negotiable "never unload while LTFS is mounted or dirty".
# It tells you how to clear the mount rather than doing it for you: unmounting
# someone else's dirty volume is not a decision a cleanup script should make.
#
# Usage:  sudo scripts/mhvtl/reset.sh

set -euo pipefail

# shellcheck source=scripts/mhvtl/_rig.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_rig.sh"

log()  { printf '\n=== %s\n' "$*"; }

command -v mtx    >/dev/null || rig_die "mtx not installed"
command -v lsscsi >/dev/null || rig_die "lsscsi not installed"
command -v sg_inq >/dev/null || rig_die "sg_inq not installed (sg3-utils)"

# Must come before anything reads or moves media.
rig_require_no_ltfs

changer=$(rig_changer)

log "Unloading any loaded drives on $changer"

# Parse the drive lines once. A loaded drive reports its origin slot as
#   Data Transfer Element 0:Full (Storage Element 6 Loaded):VolumeTag = OB0007L8
# Note mtx spaces the '=' here but not on Storage Element lines - the same
# inconsistency openblade/hardware/mtx.py has a regression test for.
status=$(mtx -f "$changer" status)

moved=0
while read -r drive_id source_slot; do
  [ -n "$drive_id" ] || continue
  if [ -z "$source_slot" ]; then
    printf 'drive %s is loaded but reports no origin slot; ' "$drive_id"
    # Fall back to the first empty STORAGE slot. The regex excludes
    # import/export elements, whose lines carry an " IMPORT/EXPORT" infix
    # before the colon - parking a cartridge in the operator mailslot is not
    # a reset.
    source_slot=$(printf '%s\n' "$status" \
      | awk '/^ *Storage Element [0-9]+:Empty/{gsub(/:.*/,"",$3); print $3; exit}')
    [ -n "$source_slot" ] || rig_die "no empty storage slot to unload drive $drive_id into"
    printf 'using empty slot %s\n' "$source_slot"
  fi
  printf 'unloading drive %s -> slot %s\n' "$drive_id" "$source_slot"
  mtx -f "$changer" unload "$source_slot" "$drive_id"
  moved=$((moved + 1))
done < <(printf '%s\n' "$status" | awk '
  /^Data Transfer Element [0-9]+:Full/ {
    drive = $4; sub(/:.*/, "", drive)
    slot = ""
    if (match($0, /Storage Element [0-9]+ Loaded/)) {
      chunk = substr($0, RSTART, RLENGTH)
      split(chunk, parts, " ")
      slot = parts[3]
    }
    print drive, slot
  }')

if [ "$moved" -eq 0 ]; then
  printf 'all drives were already empty\n'
fi

log "Library state"
mtx -f "$changer" status
