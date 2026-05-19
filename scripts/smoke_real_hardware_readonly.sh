#!/usr/bin/env bash
# Read-only hardware smoke script. Never loads, unloads, formats, or moves media.
# Safe to run on a real server for initial discovery.
set -euo pipefail

echo "=== OpenBlade Read-Only Hardware Smoke Script ==="
echo "This script only discovers and inventories hardware. No writes, moves, or formats."
echo ""

for cmd in lsscsi mtx sg_inq; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "WARNING: $cmd not found, some checks will be skipped"
    fi
done

if ! command -v lsscsi &>/dev/null; then
    echo "lsscsi is required for discovery"
    exit 0
fi

echo "--- SCSI Devices ---"
lsscsi -g 2>/dev/null || echo "(lsscsi not available)"

echo ""
echo "--- Tape Changers ---"
changer_lines="$(lsscsi -g 2>/dev/null | awk '$2 == "mediumx" {print $0}')"
if [[ -z "$changer_lines" ]]; then
    echo "(no tape changers found)"
else
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        block_device="$(awk '{print $(NF-1)}' <<<"$line")"
        sg_device="$(awk '{print $NF}' <<<"$line")"
        echo "Changer: block=${block_device} sg=${sg_device}"
        if command -v mtx &>/dev/null; then
            mtx -f "$sg_device" status || echo "(mtx status failed for ${sg_device})"
        else
            echo "(mtx not available)"
        fi
        echo ""
    done <<<"$changer_lines"
fi

echo "--- Tape Drives ---"
drive_lines="$(lsscsi -g 2>/dev/null | awk '$2 == "tape" {print $0}')"
if [[ -z "$drive_lines" ]]; then
    echo "(no tape drives found)"
else
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        block_device="$(awk '{print $(NF-1)}' <<<"$line")"
        sg_device="$(awk '{print $NF}' <<<"$line")"
        echo "Drive: block=${block_device} sg=${sg_device}"
        if command -v sg_inq &>/dev/null; then
            sg_inq "$sg_device" || echo "(sg_inq failed for ${sg_device})"
        else
            echo "(sg_inq not available)"
        fi
        echo ""
    done <<<"$drive_lines"
fi

echo "Smoke discovery complete. No media was moved or modified."
