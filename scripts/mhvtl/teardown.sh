#!/usr/bin/env bash
#
# Tear down the OpenBlade mhvtl rehearsal rig.
#
# By default this stops the daemons and removes the SCSI devices but LEAVES the
# virtual media, the config, and the installed binaries in place, so
# scripts/mhvtl/setup.sh brings the same library straight back up.
#
# Usage:
#   sudo scripts/mhvtl/teardown.sh              # stop daemons, unload module
#   sudo scripts/mhvtl/teardown.sh --purge      # also delete media + config
#
# This script only ever touches things the rig created. It does not stop
# unrelated services and does not remove packages.

set -euo pipefail

PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

log()  { printf '\n=== %s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "must run as root"

log "Stopping mhvtl daemons"
if command -v systemctl >/dev/null; then
  systemctl stop mhvtl.target 2>/dev/null || true
  for unit in vtllibrary@10 vtltape@11 vtltape@12 vtltape@13; do
    systemctl stop "$unit.service" 2>/dev/null || true
  done
fi
# Catch anything started without systemd by the setup script's fallback path.
pkill -x vtllibrary 2>/dev/null || true
pkill -x vtltape    2>/dev/null || true
sleep 1

log "Removing systemd drop-ins"
for unit in vtltape vtllibrary; do
  rm -f "/run/systemd/system/${unit}@.service.d/10-openblade-mhvtl.conf"
  rmdir "/run/systemd/system/${unit}@.service.d" 2>/dev/null || true
done
command -v systemctl >/dev/null && systemctl daemon-reload || true

log "Unloading mhvtl kernel module"
# grep -q would SIGPIPE lsmod, and pipefail would turn that into a false
# "not loaded". Let grep drain stdin.
if lsmod | grep '^mhvtl ' >/dev/null; then
  # Anything still holding a device keeps the refcount up; report rather than
  # force, so we never yank a module out from under a running process.
  if ! modprobe -r mhvtl 2>/dev/null; then
    printf 'WARNING: could not unload mhvtl (in use?). Still-open users:\n'
    lsof /dev/mhvtl* 2>/dev/null || printf '  (lsof not available)\n'
    printf 'The daemons are stopped; the module can be unloaded later.\n'
  fi
else
  printf 'mhvtl module not loaded\n'
fi

log "Removing /dev/mhvtl device nodes"
for minor in 10 11 12 13; do rm -f "/dev/mhvtl${minor}"; done

if [ "$PURGE" -eq 1 ]; then
  log "Purging virtual media and config (--purge)"
  # Scoped to the exact paths the rig owns. /opt/mhvtl holds only virtual
  # cartridges; /etc/mhvtl holds only mhvtl config.
  rm -rf /opt/mhvtl
  rm -f /etc/mhvtl/device.conf /etc/mhvtl/library_contents.10
  printf 'Removed /opt/mhvtl and the rig config.\n'
  printf 'Binaries and the kernel module are still installed; that is deliberate.\n'
else
  printf '\nVirtual media under /opt/mhvtl and config in /etc/mhvtl were kept.\n'
  printf 'Re-run scripts/mhvtl/setup.sh to bring the same library back up.\n'
  printf 'Use --purge to delete them.\n'
fi

log "Remaining SCSI devices"
command -v lsscsi >/dev/null && lsscsi -g || true
