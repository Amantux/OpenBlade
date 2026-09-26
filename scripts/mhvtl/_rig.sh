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
  rig_sg_inq "$1" \
    | awk -F': *' '/Unit serial number/{print $2; exit}' \
    | awk '{print $NF}'
}

# `sg_inq` on a device, escalating to `sudo -n` when the calling user cannot
# open the node.
#
# mhvtl's /dev/sgN nodes are mode 0660 root:tape. On the bare-metal rehearsal
# host the operator was in the `tape` group, so a plain `sg_inq` worked. A
# GitHub-hosted runner's `runner` user is NOT in `tape`, so every `sg_inq`
# failed with EACCES; because the serial lookup swallows stderr, that surfaced
# as an EMPTY serial and rig_changer reported "found medium changer(s) /dev/sg1
# but none reporting unit serial 'OBLADE_L10'" on a perfectly healthy rig
# (mhvtl weekly run 35688121678). A permission problem must not be
# indistinguishable from "that is somebody else's library".
rig_sg_inq() {
  local dev="$1"
  if [ -r "$dev" ] && sg_inq "$dev" 2>/dev/null; then
    return 0
  fi
  [ "$(id -u)" -eq 0 ] && return 1
  command -v sudo >/dev/null || return 1
  sudo -n sg_inq "$dev" 2>/dev/null
}

# True when this user cannot read the sg node directly and cannot escalate.
# Used to turn "no serial" into an accurate diagnosis instead of a wrong one.
rig_sg_unreadable() {
  local dev="$1"
  [ -r "$dev" ] && return 1
  [ "$(id -u)" -eq 0 ] && return 1
  sudo -n true 2>/dev/null && return 1
  return 0
}

# Echo the no-rewind tape node (/dev/nstN) for an sg node, using the same sysfs
# relationship openblade.hardware.discovery.resolve_sg_device() reads forwards.
# st and sg numbers are allocated independently (st0 -> sg1 and st2 -> sg4 are
# both real pairings on this rig), so this must be looked up, never derived.
#
# Failure means the kernel's `st` upper-level driver has not claimed the device
# — see the "upper-level SCSI drivers" section of setup.sh.
rig_nst_for_sg() {
  local want="${1#/dev/}" tape
  for tape in /sys/class/scsi_tape/nst*; do
    [ -e "$tape/device/scsi_generic/$want" ] || continue
    printf '/dev/%s' "$(basename "$tape")"
    return 0
  done
  return 1
}

# Why there is no /dev/nst node. Printed by both setup.sh and env.sh so the
# operator gets the same diagnosis whichever one they hit first.
rig_no_nst_hint() {
  cat <<'EOF'
The kernel's SCSI tape upper-level driver ('st') has not attached to the rig's
drives: no /dev/nstN nodes exist and lsscsi prints '-' in the block column.
On Ubuntu's generic kernel st/ch ship inside linux-modules-$(uname -r) and udev
autoloads them, which is why the bare-metal rehearsal host never needed this.
On an Azure-tuned kernel (GitHub-hosted runners) they live in
linux-modules-extra-$(uname -r), which is NOT installed on the runner image.
Fix:  sudo apt-get install -y "linux-modules-extra-$(uname -r)"
      sudo modprobe st && sudo scripts/mhvtl/setup.sh
EOF
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
  for dev in $candidates; do
    if rig_sg_unreadable "$dev"; then
      rig_die "cannot read $dev to check its unit serial (it is mode 0660
         root:tape and this user is neither root, in the 'tape' group, nor able
         to 'sudo -n'). This is a PERMISSION problem, not a wrong library.
         Re-run as root, or: sudo usermod -aG tape \$USER (then re-login)."
    fi
  done
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
