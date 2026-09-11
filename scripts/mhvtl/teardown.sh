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
# This script is scoped to this rig: the four /dev/mhvtl minors our device.conf
# defines, the daemons for those units, and (with --purge) only the media
# barcodes named in our library_contents.10. /etc/mhvtl and /opt/mhvtl are
# mhvtl's own defaults and may be shared with another library on this host, so
# they are never removed wholesale; any config setup.sh displaced is restored
# from /etc/mhvtl/pre-openblade.bak. It does not stop unrelated services and
# does not remove packages.

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
# Only the minors this rig's device.conf defines (library 10, drives 11-13).
# Another mhvtl library on this host uses different minors; leave them be.
for minor in 10 11 12 13; do rm -f "/dev/mhvtl${minor}"; done

if [ "$PURGE" -eq 1 ]; then
  log "Purging this rig's virtual media and config (--purge)"

  # /opt/mhvtl is MHVTL'S default home, not a path this rig invented, and on a
  # host that already ran mhvtl it holds someone else's cartridges. Delete only
  # the media directories named in OUR library_contents, never the whole tree.
  contents=/etc/mhvtl/library_contents.10
  if [ -f "$contents" ]; then
    while read -r barcode; do
      [ -n "$barcode" ] || continue
      if [ -d "/opt/mhvtl/$barcode" ]; then
        rm -rf "/opt/mhvtl/${barcode:?}"
        printf 'removed media %s\n' "$barcode"
      fi
    done <<EOF
$(awk '/^Slot [0-9]+:/ {gsub(/^Slot [0-9]+:[ \t]*/, ""); if ($0 != "") print $1}' "$contents")
EOF
  else
    printf 'No %s present; leaving /opt/mhvtl alone.\n' "$contents"
  fi
  rmdir /opt/mhvtl 2>/dev/null && printf 'removed empty /opt/mhvtl\n' || true

  rm -f /etc/mhvtl/device.conf /etc/mhvtl/library_contents.10 /etc/mhvtl/mhvtl.conf

  # Put back whatever was here before setup.sh first ran.
  backup_dir=/etc/mhvtl/pre-openblade.bak
  if [ -d "$backup_dir" ]; then
    cp -a "$backup_dir"/. /etc/mhvtl/ 2>/dev/null || true
    rm -rf "${backup_dir:?}"
    printf 'Restored the pre-existing /etc/mhvtl config from its backup.\n'
  fi

  printf 'Binaries and the kernel module are still installed; that is deliberate.\n'
else
  printf '\nVirtual media under /opt/mhvtl and config in /etc/mhvtl were kept.\n'
  printf 'Re-run scripts/mhvtl/setup.sh to bring the same library back up.\n'
  printf 'Use --purge to delete them.\n'
fi

log "Remaining SCSI devices"
command -v lsscsi >/dev/null && lsscsi -g || true
