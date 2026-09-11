#!/usr/bin/env bash
#
# Print the environment block that points the OpenBlade hardware suite at the
# mhvtl rehearsal rig created by scripts/mhvtl/setup.sh.
#
# Devices are DISCOVERED from lsscsi, not hardcoded: mhvtl attaches to whatever
# SCSI host number is free, so /dev/sgN moves between boots.
#
# Usage:
#   eval "$(scripts/mhvtl/env.sh)"
#   python -m pytest tests/hardware/ -v -m real_hardware
#
# Or, to avoid polluting your shell:
#   env $(scripts/mhvtl/env.sh | sed 's/^export //') python -m pytest ...

set -euo pipefail

command -v lsscsi >/dev/null || { echo "lsscsi not installed" >&2; exit 1; }

scsi=$(lsscsi -g)

changer_sg=$(printf '%s\n' "$scsi" | awk '/ mediumx /{print $NF}' | head -1)
[ -n "$changer_sg" ] || { echo "no mediumx device found - is the rig up?" >&2; exit 1; }

# Tape drives, in SCSI address order. lsscsi already sorts by [host:bus:tgt:lun],
# which is the order mhvtl instantiated the drives in device.conf (11, 12, 13).
#
# We export the NO-REWIND nodes (/dev/nstN), never /dev/stN. Writing LTFS
# through a rewinding node is a data-loss footgun; see Phase 0.3 of
# docs/runbooks/real-i3-bringup-plan.md.
drive_st=$(printf '%s\n' "$scsi" | awk '/ tape /{print $(NF-1)}')
drive_nst=$(printf '%s\n' "$drive_st" | sed 's#/dev/st#/dev/nst#' | paste -sd,)

# The LTFS `sg` backend addresses drives by their /dev/sg node, NOT /dev/nst.
drive_sg=$(printf '%s\n' "$scsi" | awk '/ tape /{print $NF}' | paste -sd,)

cat <<EOF
export OPENBLADE_BACKEND=real
export OPENBLADE_REAL_HARDWARE_ENABLED=true
export OPENBLADE_CHANGER_DEVICE=${changer_sg}
export OPENBLADE_DRIVE_DEVICES=${drive_nst}
# LTFS sg-backend device paths for the same drives, in the same order.
export OPENBLADE_DRIVE_SG_DEVICES=${drive_sg}
# Only these barcodes may be formatted/overwritten. They are the scratch media
# declared in scripts/mhvtl/config/library_contents.10 - nothing else.
export OPENBLADE_SCRATCH_BARCODES=OB0007L8,OB0008L8
EOF
