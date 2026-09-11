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
# Usage:  sudo scripts/mhvtl/reset.sh

set -euo pipefail

log()  { printf '\n=== %s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

command -v mtx    >/dev/null || fail "mtx not installed"
command -v lsscsi >/dev/null || fail "lsscsi not installed"

changer=$(lsscsi -g | awk '/ mediumx /{print $NF}' | head -1)
case "$changer" in
  /dev/sg*) ;;
  *) fail "no changer with an sg node found - is the rig up? (scripts/mhvtl/setup.sh)" ;;
esac

log "Unloading any loaded drives on $changer"

# Parse the drive lines once. A loaded drive reports its origin slot as
#   Data Transfer Element 0:Full (Storage Element 6 Loaded):VolumeTag = OB0007L8
# Note mtx spaces the '=' here but not on Storage Element lines - the same
# inconsistency that openblade/hardware/mtx.py has a regression test for.
status=$(mtx -f "$changer" status)

moved=0
while read -r drive_id source_slot; do
  [ -n "$drive_id" ] || continue
  if [ -z "$source_slot" ]; then
    printf 'drive %s is loaded but reports no origin slot; ' "$drive_id"
    # Fall back to the first empty, non-I/E storage slot.
    source_slot=$(printf '%s\n' "$status" \
      | awk '/^ *Storage Element [0-9]+:Empty/{gsub(/:.*/,"",$3); print $3; exit}')
    [ -n "$source_slot" ] || fail "no empty storage slot to unload drive $drive_id into"
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

[ "$moved" -eq 0 ] && printf 'all drives were already empty\n'

log "Library state"
mtx -f "$changer" status
