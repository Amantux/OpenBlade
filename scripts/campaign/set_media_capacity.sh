#!/usr/bin/env bash
#
# Resize the mhvtl rig's virtual cartridges. OPTIONAL, and rig-only.
#
#   sudo scripts/campaign/set_media_capacity.sh 400     # small tapes
#   sudo scripts/campaign/set_media_capacity.sh 8000    # back to the rig default
#
# Why this exists: the campaign dataset is ~430 MB and the rig's cartridges are
# 8000 MB, so a single tape swallows everything and the archive path's
# roll-to-the-next-tape logic never runs. On the real i3 you do not do this --
# you simply have more data than a cartridge holds. This is the virtual-rig
# equivalent, kept separate from the campaign proper so nobody runs it by
# accident against real media.
#
# CAPACITY is read by make_vtl_media at media CREATION time, so the existing
# cartridges must be deleted first. Everything on this rig is virtual scratch.
# The mhvtl kernel module and device nodes are left alone -- this is a media
# rebuild, not a teardown.

set -euo pipefail

CAPACITY_MB="${1:?usage: set_media_capacity.sh <megabytes>}"
CONF=/etc/mhvtl/mhvtl.conf
CONTENTS=/etc/mhvtl/library_contents.10
HOME_DIR=/opt/mhvtl

[ "$(id -u)" -eq 0 ] || { echo "must run as root" >&2; exit 1; }
[ -f "$CONF" ] || { echo "$CONF not found -- run scripts/mhvtl/setup.sh first" >&2; exit 1; }

case "$CAPACITY_MB" in
  ''|*[!0-9]*) echo "capacity must be a positive integer (MB)" >&2; exit 1 ;;
esac

# Refuse if anything is still mounted or loaded: rebuilding media under a live
# LTFS mount is the "never unload while mounted" rule with extra steps.
if mount | grep -q "$HOME_DIR" || mount | grep -qi ltfs; then
  echo "An LTFS mount is still live. Unmount it before rebuilding media." >&2
  mount | grep -i ltfs >&2 || true
  exit 1
fi

echo "Setting CAPACITY=$CAPACITY_MB in $CONF"
sed -i -E "s/^CAPACITY=[0-9]+/CAPACITY=$CAPACITY_MB/" "$CONF"
grep -E '^CAPACITY=' "$CONF"

# Delete only the barcodes this rig's own library_contents declares.
while read -r barcode; do
  [ -n "$barcode" ] || continue
  if [ -d "$HOME_DIR/$barcode" ]; then
    rm -rf "${HOME_DIR:?}/${barcode:?}"
    echo "removed media $barcode"
  fi
done <<EOF
$(awk '/^Slot [0-9]+:/ {gsub(/^Slot [0-9]+:[ \t]*/, ""); if ($0 != "") print $1}' "$CONTENTS")
EOF

make_vtl_media --config-dir=/etc/mhvtl --home-dir="$HOME_DIR" >/dev/null \
  || make_vtl_media --home-dir="$HOME_DIR" >/dev/null

echo "Rebuilt media at ${CAPACITY_MB} MB:"
ls -1 "$HOME_DIR"
echo
echo "Every cartridge is now blank and UNFORMATTED -- run the format flow again."
