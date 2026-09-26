#!/usr/bin/env bash
#
# Print the environment block that points the OpenBlade hardware suite at the
# mhvtl rehearsal rig created by scripts/mhvtl/setup.sh.
#
# Devices are DISCOVERED, not hardcoded: mhvtl attaches to whatever SCSI host
# number is free, so /dev/sgN and /dev/stN move between boots. The changer is
# identified by its unit serial number rather than "first mediumx", so a real
# library attached to the same host can never be selected - see _rig.sh.
#
# Diagnostics go to stderr so `eval` only ever consumes the exports.
#
# Usage:
#   eval "$(scripts/mhvtl/env.sh)"
#   python -m pytest tests/hardware/ -v -m real_hardware

set -euo pipefail

# shellcheck source=scripts/mhvtl/_rig.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_rig.sh"

command -v lsscsi >/dev/null || rig_die "lsscsi not installed"
command -v sg_inq >/dev/null || rig_die "sg_inq not installed (sg3-utils)"

changer_sg=$(rig_changer)

# Tape drives in SCSI address order, which is the changer's Data Transfer
# Element order. We export the NO-REWIND nodes (/dev/nstN), never /dev/stN:
# issuing SCSI commands on a rewinding node makes it rewind on close, which
# fails on an empty drive and is a corruption footgun under LTFS. See Phase 0.3
# of docs/runbooks/real-i3-bringup-plan.md.
# The sg -> nst lookup lives in _rig.sh (rig_nst_for_sg) so setup.sh can verify
# it at bring-up time rather than leaving the test suite to discover it.

drive_nst=""
while read -r sg; do
  [ -n "$sg" ] || continue
  if ! node=$(rig_nst_for_sg "$sg"); then
    rig_no_nst_hint >&2
    rig_die "could not map $sg back to a /dev/nst node via sysfs (see above)"
  fi
  drive_nst="${drive_nst:+$drive_nst,}${node}"
done <<EOF
$(rig_drive_sgs "$changer_sg")
EOF

[ -n "$drive_nst" ] || rig_die "no tape drives found on this rig"

cat <<EOF
export OPENBLADE_BACKEND=real
export OPENBLADE_REAL_HARDWARE_ENABLED=true
export OPENBLADE_CHANGER_DEVICE=${changer_sg}
export OPENBLADE_DRIVE_DEVICES=${drive_nst}
# Only these barcodes may be formatted/overwritten. They are the scratch media
# declared in scripts/mhvtl/config/library_contents.10 - nothing else.
export OPENBLADE_SCRATCH_BARCODES=OB0007L8,OB0008L8
EOF
