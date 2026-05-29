#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ROOT_COMPOSE="${ROOT_DIR}/docker-compose.yml"
DEFAULT_EMULATOR_URLS="http://host.docker.internal:8010,http://host.docker.internal:8011,http://host.docker.internal:8012"
EMULATOR_URLS="${OPENBLADE_EMULATOR_URLS:-${DEFAULT_EMULATOR_URLS}}"

usage() {
  cat <<'EOF'
Usage: run_fleet_stack.sh <up|down|logs|ps|config|build> [docker-compose args...]

Actions:
  up      Build/launch standalone emulators and OpenBlade api+web
  down    Stop OpenBlade api+web and standalone emulators
  logs    Tail OpenBlade api+web logs
  ps      Show OpenBlade api+web and standalone emulator process state
  config  Show merged OpenBlade compose config with external emulator URLs
  build   Build standalone emulator image and OpenBlade api+web images

Environment:
  OPENBLADE_EMULATOR_URLS  Override API targets (default host-gateway URLs for 8010-8012)
  EMULATOR_ENV_FILE        Optional env file for standalone emulator stack
EOF
}

cleanup_legacy_emulators() {
  for container in openblade-emulator-1 openblade-emulator-2 openblade-emulator-3; do
    if docker ps -a --format '{{.Names}}' | grep -Fxq "${container}"; then
      docker rm -f "${container}" >/dev/null
    fi
  done
}

up_stack() {
  cleanup_legacy_emulators
  "${ROOT_DIR}/scripts/emulator/run_standalone_stack.sh" up
  OPENBLADE_EMULATOR_URLS="${EMULATOR_URLS}" docker compose -f "${ROOT_COMPOSE}" up -d api web
}

down_stack() {
  docker compose -f "${ROOT_COMPOSE}" down
  "${ROOT_DIR}/scripts/emulator/run_standalone_stack.sh" down
}

logs_stack() {
  OPENBLADE_EMULATOR_URLS="${EMULATOR_URLS}" docker compose -f "${ROOT_COMPOSE}" logs -f api web
}

ps_stack() {
  echo "== OpenBlade API/Web =="
  OPENBLADE_EMULATOR_URLS="${EMULATOR_URLS}" docker compose -f "${ROOT_COMPOSE}" ps api web
  echo
  echo "== Standalone emulators =="
  "${ROOT_DIR}/scripts/emulator/run_standalone_stack.sh" ps
}

config_stack() {
  OPENBLADE_EMULATOR_URLS="${EMULATOR_URLS}" docker compose -f "${ROOT_COMPOSE}" config
}

build_stack() {
  "${ROOT_DIR}/scripts/emulator/run_standalone_stack.sh" build
  OPENBLADE_EMULATOR_URLS="${EMULATOR_URLS}" docker compose -f "${ROOT_COMPOSE}" build api web
}

if [[ $# -lt 1 ]]; then
  usage
  exit 1
fi

action="$1"
shift

case "${action}" in
  up)
    up_stack "$@"
    ;;
  down)
    down_stack "$@"
    ;;
  logs)
    logs_stack "$@"
    ;;
  ps)
    ps_stack "$@"
    ;;
  config)
    config_stack "$@"
    ;;
  build)
    build_stack "$@"
    ;;
  *)
    usage
    exit 1
    ;;
esac
