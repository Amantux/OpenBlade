#!/usr/bin/env bash
# shellcheck shell=bash
#
# Shared helpers for the mhvtl rehearsal rig scripts. Sourced, not executed.
#
# Everything here exists so the scripts act on THIS rig and nothing else. That
# matters more than it looks: Phase 3 of docs/runbooks/real-i3-bringup-plan.md
# cables a real Quantum Scalar i3 to the same host, and a real i3 reports the
# same `QUANTUM` vendor string as our emulated library. "First mediumx lsscsi
# prints" would then very plausibly select the real library — a real HBA
# usually enumerates at a LOWER SCSI host number than mhvtl's dynamically
# allocated one. So we key on the rig's unit serial number instead.

# Unit serial numbers from scripts/mhvtl/config/device.conf. Changing them
# there means changing them here.
RIG_CHANGER_SERIAL="${RIG_CHANGER_SERIAL:-OBLADE_L10}"

rig_die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# Unit serial number reported by a device, normalised to its last
# whitespace-separated token, or empty.
#
# The token matters: mhvtl's Scalar personality builds VPD page 0x80 as
# "<vendor id><serial>", so the changer reports "QUANTUM OBLADE_L10" while the
# drives report a bare "OBLADE_D01". Taking the last token handles both without
# resorting to a substring test - a loose match here could select a real
# library, which is the whole thing this is guarding against.
rig_serial_of() {
  sg_inq "$1" 2>/dev/null \
    | awk -F': *' '/Unit serial number/{print $2; exit}' \
    | awk '{print $NF}'
}

# Echo the /dev/sg node of THIS rig's medium changer.
rig_changer() {
  local scsi dev serial candidates=""
  scsi=$(lsscsi -g 2>/dev/null) || rig_die "lsscsi failed"
  while read -r dev; do
    [ -n "$dev" ] || continue
    case "$dev" in /dev/sg*) ;; *) continue ;; esac
    candidates="$candidates $dev"
    serial=$(rig_serial_of "$dev")
    if [ "$serial" = "$RIG_CHANGER_SERIAL" ]; then
      printf '%s\n' "$dev"
      return 0
    fi
  done <<EOF
$(printf '%s\n' "$scsi" | awk '/ mediumx /{print $NF}')
EOF

  if [ -z "$candidates" ]; then
    rig_die "no medium changer with an sg node found - is the rig up?
         Run: sudo scripts/mhvtl/setup.sh"
  fi
  rig_die "found medium changer(s)$candidates but none reporting unit serial
         '$RIG_CHANGER_SERIAL'. Refusing to guess: a real library attached to
         this host must not be driven by the rehearsal scripts. Check
         'lsscsi -g' and 'sg_inq <dev>'."
}

# Echo this rig's tape drives' sg nodes, in SCSI address order (= changer Data
# Transfer Element order). lsscsi already sorts by [host:bus:target:lun].
# Restricted to the SCSI host the rig's changer lives on, so a real library's
# drives can never be included.
rig_drive_sgs() {
  local changer host
  changer=${1:-$(rig_changer)}
  host=$(lsscsi -g | awk -v c="$changer" '$NF == c {print $1}' | tr -d '[]' | cut -d: -f1)
  [ -n "$host" ] || rig_die "could not determine the SCSI host of $changer"
  lsscsi -g | awk -v h="$host" '
    / tape / {
      addr = $1; gsub(/[\[\]]/, "", addr)
      split(addr, parts, ":")
      if (parts[1] == h) print $NF
    }'
}

# Is any LTFS process or mount currently holding a tape drive?
# Prints a human-readable list on stdout; empty output means nothing is held.
rig_ltfs_holders() {
  # "Nothing found" is the normal case, and both pipelines exit non-zero for
  # it (pgrep returns 1 on no match). Under `set -e` with `pipefail` that
  # would abort the caller at `holders=$(rig_ltfs_holders)`, so swallow the
  # status and always succeed - emptiness is the signal, not the exit code.
  mount 2>/dev/null | awk '/^ltfs/ {print "mount: " $3}' || true
  pgrep -a -x ltfs 2>/dev/null | sed 's/^/process: /' || true
  return 0
}

# Refuse to move media while LTFS holds a drive.
#
# The project non-negotiable is "never unload while LTFS is mounted or dirty"
# (CLAUDE.md / AGENTS.md). Pulling a cartridge out from under a mounted LTFS
# can lose the index that was about to be written. These scripts exist to clean
# up after FAILED test runs, which is exactly when a mount is most likely to
# have been left behind - so the check belongs here, not in the caller.
rig_require_no_ltfs() {
  local holders
  holders=$(rig_ltfs_holders)
  [ -z "$holders" ] && return 0
  printf 'ERROR: LTFS is still holding a drive; refusing to move media.\n' >&2
  printf '%s\n' "$holders" | sed 's/^/  /' >&2
  cat >&2 <<'EOF'
Unloading now could discard an LTFS index that has not been written yet.
Unmount first, then re-run:
  for m in $(mount | awk '/^ltfs/{print $3}'); do umount "$m" || fusermount -u "$m"; done
EOF
  exit 1
}

# A barcode we are willing to act on destructively. Deliberately strict: this
# is the gate that stops a typo, an empty argument, or a partial string from
# selecting a cartridge the operator did not name.
rig_require_valid_barcode() {
  case "$1" in
    "") rig_die "empty barcode argument - refusing to act on an unnamed cartridge" ;;
  esac
  printf '%s' "$1" | grep -qE '^[A-Z0-9]{6,8}$' \
    || rig_die "barcode '$1' is not 6-8 upper-case alphanumeric characters.
         Refusing to act: a partial or malformed barcode must never select a
         cartridge by accident."
}

# Echo the storage slot holding exactly this barcode, or empty.
#
# The match is ANCHORED on the whole VolumeTag. A substring test here is a
# data-loss bug: "OB000" would select OB0001L8, and "L8" would select the first
# cartridge in the library.
#
# Import/export elements are excluded - scratch media lives in storage slots,
# and we should not be reaching into the operator mailslot.
rig_find_slot() {
  local changer="$1" barcode="$2"
  mtx -f "$changer" status | awk -v want="$barcode" '
    /^ *Storage Element [0-9]+:/ {
      if ($0 ~ /IMPORT\/EXPORT/) next
      slot = $3; sub(/:.*/, "", slot)
      tag = $0
      if (match(tag, /VolumeTag[ ]*=[ ]*[^ ]+/)) {
        tag = substr(tag, RSTART, RLENGTH)
        sub(/VolumeTag[ ]*=[ ]*/, "", tag)
        if (tag == want) { print slot; exit }
      }
    }'
}

# Echo the barcode currently loaded in drive <n>, or empty if it is empty.
rig_barcode_in_drive() {
  local changer="$1" drive="$2"
  mtx -f "$changer" status | awk -v d="$drive" '
    $0 ~ ("^Data Transfer Element " d ":") {
      if ($0 !~ /Full/) exit
      tag = $0
      if (match(tag, /VolumeTag[ ]*=[ ]*[^ ]+/)) {
        tag = substr(tag, RSTART, RLENGTH)
        sub(/VolumeTag[ ]*=[ ]*/, "", tag)
        print tag
      }
      exit
    }'
}
