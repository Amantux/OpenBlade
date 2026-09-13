#!/usr/bin/env bash
# Campaign environment. Source it (do not execute it):
#
#     eval "$(scripts/mhvtl/env.sh)"          # rig device paths
#     source scripts/campaign/env.sh          # campaign paths + serial map
#
# On the real i3 replace the two exports from scripts/mhvtl/env.sh with the
# i3's own changer/drive nodes; everything below is rig-independent.

# Serial->drive-element correlation. Verified against sg_inq at startup; without
# it OpenBlade falls back to unverified positional order (see
# openblade/hardware/correlation.py).
export OPENBLADE_DRIVE_SERIAL_MAP="${OPENBLADE_DRIVE_SERIAL_MAP:-OBLADE_D01:0,OBLADE_D02:1,OBLADE_D03:2}"

# All rig media are virtual scratch, so the campaign is allowed to format the
# four data barcodes as well as the two the hardware suite declares. On the real
# i3 this MUST be narrowed to genuinely blank cartridges before running anything.
export OPENBLADE_SCRATCH_BARCODES="${OPENBLADE_SCRATCH_BARCODES:-OB0001L8,OB0002L8,OB0003L8,OB0004L8,OB0007L8,OB0008L8}"

CAMPAIGN_ROOT="${CAMPAIGN_ROOT:-/srv/openblade-campaign}"
export CAMPAIGN_ROOT
export CAMPAIGN_DATA="$CAMPAIGN_ROOT/data"
export CAMPAIGN_RESTORE="$CAMPAIGN_ROOT/restore"
export CAMPAIGN_LOG="$CAMPAIGN_ROOT/log"

export OPENBLADE_DB_URL="sqlite:///$CAMPAIGN_ROOT/campaign.db"
export OPENBLADE_CACHE_DIR="$CAMPAIGN_ROOT/cache"
export OPENBLADE_STAGING_DIR="$CAMPAIGN_ROOT/staging"
export OPENBLADE_RESTORE_DIR="$CAMPAIGN_RESTORE"
export OPENBLADE_LTFS_MOUNT_ROOT="$CAMPAIGN_ROOT/ltfs"

mkdir -p "$CAMPAIGN_RESTORE" "$CAMPAIGN_LOG" "$OPENBLADE_CACHE_DIR" \
         "$OPENBLADE_STAGING_DIR" "$OPENBLADE_LTFS_MOUNT_ROOT"
