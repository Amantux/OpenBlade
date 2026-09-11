#!/usr/bin/env bash
#
# Stand up the OpenBlade mhvtl rehearsal rig: 1 medium changer + 3 LTO-8
# drives + 8 barcoded slots, so tests/hardware/ can run the *real* hardware
# backend with zero physical hardware.
#
# Phase 2 of docs/runbooks/real-i3-bringup-plan.md.
#
# Idempotent: safe to re-run. Installs packages, builds and installs mhvtl
# from source if it is not already present, writes the config, starts the
# daemons, and verifies with lsscsi.
#
# Usage:  sudo scripts/mhvtl/setup.sh
#
# Env overrides:
#   MHVTL_SRC   where to clone/build mhvtl        (default /usr/local/src/mhvtl)
#   MHVTL_REF   git ref to build                  (default master)
#   SKIP_APT    set to 1 to skip apt-get install

set -euo pipefail

MHVTL_SRC="${MHVTL_SRC:-/usr/local/src/mhvtl}"
# Pinned, not "master". patches/ is byte-exact against this commit, and the
# results recorded in docs/runbooks/mhvtl-rehearsal.md are from this build. A
# moving ref would give the next person either a hard "failed to apply" or,
# worse, a quietly different rig. Bump deliberately, re-running the suite.
# Full 40-character SHA: GitHub's fetch-by-SHA only accepts the complete id.
MHVTL_REF="${MHVTL_REF:-59f32ee50b3269c118965bffa29cf38b1553268f}"
MHVTL_REPO="https://github.com/markh794/mhvtl.git"
CONFIG_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/config"

log()  { printf '\n=== %s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "must run as root (needs modprobe + /etc/mhvtl)"

# ---------------------------------------------------------------- packages
if [ "${SKIP_APT:-0}" != "1" ]; then
  log "Installing build and tape userspace packages"
  export DEBIAN_FRONTEND=noninteractive
  apt-get install -y --no-install-recommends \
      lsscsi sg3-utils mtx zlib1g-dev liblzo2-dev \
      build-essential git "linux-headers-$(uname -r)" \
    || fail "apt-get install failed — see output above"
fi

for tool in lsscsi mtx sg_inq gcc make git; do
  command -v "$tool" >/dev/null || fail "required tool '$tool' not on PATH"
done

# mhvtl's kernel module is an out-of-tree build; it needs headers that match
# the *running* kernel exactly, not merely some installed kernel.
[ -d "/lib/modules/$(uname -r)/build" ] \
  || fail "no kernel build dir for running kernel $(uname -r).
         Install linux-headers-$(uname -r) (and reboot into that kernel)."

# ---------------------------------------------------------------- source
log "Fetching mhvtl source into $MHVTL_SRC ($MHVTL_REF)"
if [ ! -d "$MHVTL_SRC/.git" ]; then
  mkdir -p "$(dirname "$MHVTL_SRC")"
  git init -q "$MHVTL_SRC"
  git -C "$MHVTL_SRC" remote add origin "$MHVTL_REPO"
fi
# Fetch the ref by name OR by raw commit SHA - `git clone --branch` only
# accepts branches and tags, and MHVTL_REF is pinned to a commit.
git -C "$MHVTL_SRC" fetch --depth 1 origin "$MHVTL_REF" \
  || fail "could not fetch '$MHVTL_REF' from $MHVTL_REPO.
         If you pinned a commit, the server must allow fetching it by SHA."
# Keep local modifications (our applied patch) rather than clobbering them;
# the patch step below detects an already-patched tree.
git -C "$MHVTL_SRC" checkout -q FETCH_HEAD 2>/dev/null \
  || git -C "$MHVTL_SRC" reset -q --mixed FETCH_HEAD

# ---------------------------------------------------------------- patches
# mhvtl's Scalar personality module has three defects that make vtllibrary
# abort with heap corruption ("malloc(): invalid next size") the moment you
# configure a QUANTUM/ADIC library. We need that personality, because its SCSI
# element addressing is the realistic Scalar i3 case. See the patch header and
# scripts/mhvtl/README.md. Reported upstream; drop this once it lands.
log "Applying OpenBlade patches to mhvtl source"
for patch in "$(dirname "$CONFIG_SRC")"/patches/*.patch; do
  [ -e "$patch" ] || continue
  if git -C "$MHVTL_SRC" apply --check --reverse "$patch" 2>/dev/null; then
    printf 'already applied: %s\n' "$(basename "$patch")"
  else
    git -C "$MHVTL_SRC" apply "$patch" \
      || fail "failed to apply $(basename "$patch") — the upstream fix has
         probably landed or the file moved. Re-check against $MHVTL_REF."
    printf 'applied: %s\n' "$(basename "$patch")"
  fi
done

# ---------------------------------------------------------------- kernel module
log "Building mhvtl kernel module"
make -C "$MHVTL_SRC/kernel" || fail "kernel module build failed.
         This is the most likely place for this script to block on a new host.
         Capture the full output above — a mismatched or missing
         linux-headers-\$(uname -r) is the usual cause."
make -C "$MHVTL_SRC/kernel" install
depmod -a

# NB: `grep -q` exits on first match, which SIGPIPEs `lsmod`; under
# `set -o pipefail` that makes the pipeline return 141 and a loaded module
# reads as "not loaded". Let grep drain stdin instead.
module_loaded() { lsmod | grep '^mhvtl ' >/dev/null; }

if module_loaded; then
  log "mhvtl module already loaded"
else
  log "Loading mhvtl module"
  modprobe mhvtl || fail "modprobe mhvtl failed.
         If this host has Secure Boot enabled, an unsigned out-of-tree module
         cannot load — check 'mokutil --sb-state'. If this is a container,
         module loading is not permitted from inside it at all."
fi
module_loaded || fail "mhvtl module is not loaded after modprobe"

# ---------------------------------------------------------------- userspace
log "Building and installing mhvtl userspace daemons"
make -C "$MHVTL_SRC"
make -C "$MHVTL_SRC" install
command -v systemctl >/dev/null && systemctl daemon-reload || true

# ---------------------------------------------------------------- config
# /etc/mhvtl and /opt/mhvtl are MHVTL'S OWN defaults, not paths this rig
# invented. If someone already runs mhvtl on this host, their library lives
# here. Back their config up once, the first time we touch it, and say so -
# silently overwriting an operator's device.conf is not acceptable just
# because we got here second.
log "Installing OpenBlade rig config into /etc/mhvtl"
install -d -m 755 /etc/mhvtl
backup_dir=/etc/mhvtl/pre-openblade.bak
if [ ! -d "$backup_dir" ] && ls /etc/mhvtl/*.conf /etc/mhvtl/library_contents.* >/dev/null 2>&1; then
  install -d -m 755 "$backup_dir"
  cp -a /etc/mhvtl/mhvtl.conf "$backup_dir"/ 2>/dev/null || true
  cp -a /etc/mhvtl/device.conf "$backup_dir"/ 2>/dev/null || true
  cp -a /etc/mhvtl/library_contents.* "$backup_dir"/ 2>/dev/null || true
  printf 'Existing /etc/mhvtl config backed up to %s\n' "$backup_dir"
fi

install -m 644 "$CONFIG_SRC/mhvtl.conf"            /etc/mhvtl/mhvtl.conf
install -m 644 "$CONFIG_SRC/device.conf"           /etc/mhvtl/device.conf
install -m 644 "$CONFIG_SRC/library_contents.10"   /etc/mhvtl/library_contents.10

# The stock install ships a second library (30), and our device.conf defines
# only library 10, so a leftover library_contents.30 would describe a library
# that no longer exists. Move it aside rather than deleting it - on a host that
# already ran mhvtl, that file is someone's library definition.
if [ -f /etc/mhvtl/library_contents.30 ]; then
  install -d -m 755 "$backup_dir"
  mv /etc/mhvtl/library_contents.30 "$backup_dir"/library_contents.30
  printf 'Moved library_contents.30 aside to %s (this rig defines one library)\n' "$backup_dir"
fi

# ---------------------------------------------------------------- media
log "Creating virtual media under /opt/mhvtl"
install -d -m 755 /opt/mhvtl
# make_vtl_media is idempotent: it only creates media files that are absent,
# so it never disturbs cartridges belonging to another library.
# Both invocations must target the same home dir; without --home-dir the
# fallback would write media somewhere the rest of this script does not look.
make_vtl_media --config-dir=/etc/mhvtl --home-dir=/opt/mhvtl >/dev/null \
  || make_vtl_media --home-dir=/opt/mhvtl >/dev/null \
  || fail "make_vtl_media failed — 'mtx load' will return a hardware error
         without media files present"

# ------------------------------------------------- systemd unit workarounds
# mhvtl ships vtltape@.service / vtllibrary@.service with sandboxing that stops
# its own daemons from working. Two independent problems, both reproduced here:
#
#   1. ProtectClock=yes implies a DeviceAllow= rule, and setting ANY DeviceAllow
#      switches the cgroup device policy from "auto" to "closed" — which then
#      denies the daemon its own /dev/mhvtl<n> node. Symptom:
#        chrdev_create(): Error creating device node for mhvtl: Operation not permitted
#      or, once the node exists,
#        Could not open transport for minor NN: Operation not permitted
#      Fix: re-allow the module's (dynamically allocated) character major.
#
#   2. ProtectKernelTunables=yes mounts /sys read-only, so the daemon cannot
#      write /sys/bus/mhvtl/drivers/mhvtl/add_lu to register its logical unit.
#      Symptom: daemon reports "active" but NO SCSI device ever appears:
#        Could not open .../add_lu: Read-only file system
#      Fix: turn it off. There is no narrower knob — the daemon genuinely needs
#      to write to sysfs.
#
# These are drop-ins under /run, so they evaporate on reboot and never edit a
# packaged unit file. setup.sh re-creates them.
log "Installing systemd drop-ins for the mhvtl units"
mhvtl_major=$(cat /sys/bus/mhvtl/drivers/mhvtl/major) \
  || fail "cannot read the mhvtl character major from sysfs — is the module loaded?"
for unit in vtltape vtllibrary; do
  install -d -m 755 "/run/systemd/system/${unit}@.service.d"
  cat > "/run/systemd/system/${unit}@.service.d/10-openblade-mhvtl.conf" <<EOF
# Written by scripts/mhvtl/setup.sh - see that script for the rationale.
[Service]
DeviceAllow=char-${mhvtl_major} rw
ProtectKernelTunables=no
EOF
done
systemctl daemon-reload

# The daemons mknod their own /dev/mhvtl<n>; chrdev_create() treats EEXIST as
# success, so pre-creating the nodes as root keeps that path off the critical
# path entirely.
log "Pre-creating /dev/mhvtl device nodes"
for minor in 10 11 12 13; do
  [ -e "/dev/mhvtl${minor}" ] || mknod -m 660 "/dev/mhvtl${minor}" c "$mhvtl_major" "$minor"
done

# ---------------------------------------------------------------- start
log "Starting mhvtl daemons"
stop_daemons() {
  systemctl stop mhvtl.target 2>/dev/null || true
  pkill -x vtllibrary 2>/dev/null || true
  pkill -x vtltape    2>/dev/null || true
  sleep 1
}
stop_daemons
if command -v systemctl >/dev/null && [ -f /lib/systemd/system/mhvtl.target ]; then
  systemctl enable mhvtl.target >/dev/null 2>&1 || true
  systemctl start  mhvtl.target
else
  # Fallback for hosts without systemd: start the daemons directly.
  vtllibrary -q10 &
  for id in 11 12 13; do vtltape -q"$id" & done
fi

# The SCSI hosts appear asynchronously as each daemon registers, and the
# /dev/sg nodes are created by udev a moment after that. Waiting only for the
# mediumx ROW is not enough: lsscsi will happily print it with a "-" in the sg
# column, and the rig then looks broken. Wait for the sg node itself.
log "Waiting for SCSI devices and udev to settle"
for _ in $(seq 1 30); do
  sleep 1
  changers_sg=$(lsscsi -g 2>/dev/null | awk '/ mediumx /{print $NF}' | grep -c '^/dev/sg' || true)
  drives_sg=$(lsscsi -g 2>/dev/null | awk '/ tape /{print $NF}' | grep -c '^/dev/sg' || true)
  if [ "$changers_sg" -ge 1 ] && [ "$drives_sg" -ge 3 ]; then break; fi
done
command -v udevadm >/dev/null && udevadm settle --timeout=30 || true

# ---------------------------------------------------------------- verify
log "Verifying with lsscsi -g"
lsscsi -g

changers=$(lsscsi -g | grep -c ' mediumx ' || true)
drives=$(lsscsi -g | grep -c ' tape ' || true)
printf '\nmediumx devices: %s (want >= 1)\ntape devices:    %s (want >= 3)\n' \
       "$changers" "$drives"

[ "$changers" -ge 1 ] || fail "no medium changer appeared — check 'ps ax | grep vtl' and dmesg"
[ "$drives"   -ge 3 ] || fail "expected 3 tape devices, found $drives — check vtltape daemons"

changer_sg=$(lsscsi -g | awk '/ mediumx /{print $NF}' | head -1)
case "$changer_sg" in
  /dev/sg*) ;;
  *) fail "the changer has no /dev/sg node yet (lsscsi shows '$changer_sg').
         udev had not caught up. Re-run this script, or check 'udevadm settle'." ;;
esac
log "mtx status via $changer_sg"
mtx -f "$changer_sg" status

# Drive-order correlation. mhvtl assigns /dev/stN in daemon-registration order,
# which is NOT guaranteed to match SCSI target order - we have observed target
# 1/2/3 mapping to st2/st1/st0 on one boot and st0/st1/st2 on another. This is
# the "wrote to the wrong drive" trap from Phase 2 of the bring-up plan, so
# print the correlation every time rather than letting anyone assume it.
log "Drive correlation (SCSI address -> devices -> serial)"
printf '%-14s %-10s %-10s %s\n' "SCSI_ADDR" "BLOCK" "SG" "SERIAL"
lsscsi -g | awk '/ tape /{print $1, $(NF-1), $NF}' | tr -d '[]' | while read -r addr blk sg; do
  serial=$(sg_inq "$sg" 2>/dev/null | awk -F': *' '/Unit serial number/{print $2}')
  printf '%-14s %-10s %-10s %s\n' "$addr" "$blk" "$sg" "${serial:-<unreadable>}"
done

# ---------------------------------------------------------------- scratch media
# The archive/restore jobs mount scratch media without formatting it first, so
# blank cartridges fail the mount. Supply formatted scratch media here.
if command -v mkltfs >/dev/null; then
  log "LTFS-formatting the scratch cartridges"
  bash "$(dirname "${BASH_SOURCE[0]}")/format-scratch.sh" >/dev/null \
    || fail "scratch formatting failed — run scripts/mhvtl/format-scratch.sh
         directly to see the error"
  printf 'OB0007L8, OB0008L8 formatted\n'
else
  printf '\nNOTE: mkltfs is not installed, so the scratch cartridges are BLANK.\n'
  printf 'The discovery, drive-health and changer suites still run; the LTFS,\n'
  printf 'archive/restore, sharded and performance suites will fail or skip.\n'
  printf 'See docs/runbooks/mhvtl-rehearsal.md for how to install LTFS.\n'
fi

cat <<EOF

=== Rig is up ===

Run the OpenBlade hardware suite with:

  scripts/mhvtl/env.sh            # prints the export block for this rig
  eval "\$(scripts/mhvtl/env.sh)"
  .venv/bin/python -m pytest tests/hardware/ -v -m real_hardware

Tear down with:  sudo scripts/mhvtl/teardown.sh
EOF
