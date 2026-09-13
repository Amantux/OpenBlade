#!/usr/bin/env bash
#
# The real-data campaign, phase by phase. Every command it runs is echoed with
# its exit status into $CAMPAIGN_LOG/campaign.log, so the runbook's "exact
# commands" section is a transcript rather than a reconstruction.
#
#   eval "$(scripts/mhvtl/env.sh)"        # or the real i3's device env
#   source scripts/campaign/env.sh
#   scripts/campaign/run_campaign.sh format
#   scripts/campaign/run_campaign.sh archive
#   ...
#
# Phases are separate on purpose: each one is slow and any one of them can be
# re-run on its own after a fix.

set -uo pipefail

OB="${OB:-$(dirname "$0")/../../.venv/bin/openblade}"
API="${API:-http://127.0.0.1:8099}"
LOG="${CAMPAIGN_LOG:?source scripts/campaign/env.sh first}/campaign.log"
TAPES="${CAMPAIGN_TAPES:-OB0001L8 OB0002L8 OB0003L8 OB0004L8 OB0007L8 OB0008L8}"
LANES="${CAMPAIGN_LANES:-OB0002L8,OB0003L8,OB0004L8}"

mkdir -p "$(dirname "$LOG")"

say() { printf '\n\033[1m== %s\033[0m\n' "$*" | tee -a "$LOG"; }

# run <description> -- <argv...>
run() {
  local desc="$1"; shift
  [ "${1:-}" = "--" ] && shift
  printf '\n$ %s\n' "$*" | tee -a "$LOG"
  local start out rc
  start=$(date +%s.%N)
  out=$("$@" 2>&1)
  rc=$?
  printf '%s\n' "$out" | tee -a "$LOG" | tail -40
  printf '[exit %d in %.1fs] %s\n' "$rc" "$(echo "$(date +%s.%N) - $start" | bc)" "$desc" | tee -a "$LOG"
  return $rc
}

phase_inventory() {
  say "inventory"
  run "library inventory" -- "$OB" inventory
}

phase_format() {
  say "format: dry-run + safety-token confirm, per tape"
  for barcode in $TAPES; do
    local token
    token=$("$OB" format dry-run --barcode "$barcode" 2>>"$LOG" \
      | "$(dirname "$OB")/python" -c 'import sys,json;print(json.load(sys.stdin)["token"])')
    if [ -z "$token" ]; then
      echo "FAILED to obtain a safety token for $barcode" | tee -a "$LOG"
      continue
    fi
    printf '\n$ %s format dry-run --barcode %s  -> token %s...\n' \
      "$OB" "$barcode" "${token:0:8}" | tee -a "$LOG"
    run "format $barcode" -- "$OB" format confirm --barcode "$barcode" --token "$token"
  done
  run "post-format inventory" -- "$OB" inventory
}

phase_volume_groups() {
  say "volume groups"
  run "create campaign-plain" -- "$OB" volume-group campaign-plain
  run "create campaign-shard" -- "$OB" volume-group campaign-shard
}

phase_archive_plain() {
  say "plain archive (per-file tape selection, spillover path)"
  run "archive dataset into campaign-plain" -- \
    "$OB" archive --volume-group campaign-plain --path "$CAMPAIGN_DATA"
}

phase_archive_sharded() {
  say "sharded archive across ${LANES} (API-only surface)"
  run "POST /archive/sharded (stripe)" -- \
    curl -sS -X POST "$API/archive/sharded" -H 'content-type: application/json' \
    -d "{\"source_path\":\"$CAMPAIGN_DATA\",\"volume_group\":\"campaign-shard\",\
\"lane_barcodes\":[$(echo "$LANES" | sed 's/[^,]*/"&"/g')],\"mode\":\"stripe\"}"
}

phase_catalog() {
  say "catalog"
  run "catalog root" -- "$OB" catalog /
  run "catalog volume group" -- "$OB" catalog /campaign-plain
  run "GET /catalog (API, paged)" -- curl -sS "$API/catalog/?limit=5"
}

phase_jobs() {
  say "jobs"
  run "job list" -- "$OB" jobs
  run "GET /jobs (API)" -- curl -sS "$API/jobs/"
}

phase_status() {
  say "drive + library status surfaces"
  run "GET /health" -- curl -sS "$API/health"
  run "GET /dashboard/stats" -- curl -sS "$API/dashboard/stats"
  run "GET /ltfs/status" -- curl -sS "$API/ltfs/status"
  run "GET /ltfs/tapes" -- curl -sS "$API/ltfs/tapes"
  run "hardware connect-i3" -- "$OB" hardware connect-i3
}

case "${1:-all}" in
  inventory)  phase_inventory ;;
  format)     phase_format ;;
  vg)         phase_volume_groups ;;
  archive)    phase_archive_plain ;;
  sharded)    phase_archive_sharded ;;
  catalog)    phase_catalog ;;
  jobs)       phase_jobs ;;
  status)     phase_status ;;
  all)
    phase_inventory; phase_format; phase_volume_groups
    phase_archive_plain; phase_catalog; phase_jobs
    ;;
  *) echo "unknown phase: $1" >&2; exit 2 ;;
esac
