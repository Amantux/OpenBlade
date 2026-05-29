#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/deploy/emulator/docker-compose.standalone.yml"
DEFAULT_ENV_FILE="${ROOT_DIR}/openblade/emulator_contract/standalone-runtime.env.example"
ENV_FILE="${EMULATOR_ENV_FILE:-${DEFAULT_ENV_FILE}}"

usage() {
  cat <<'EOF'
Usage: run_standalone_stack.sh <up|down|logs|config|ps|pull|build> [docker-compose args...]

Environment:
  EMULATOR_ENV_FILE   Optional env file path (defaults to standalone-runtime.env.example)
                     The env file can also override EMULATOR_UI_TARGET_LIBRARY{1,2,3}_URL
EOF
}

if [[ $# -lt 1 ]]; then
  usage
  exit 1
fi

action="$1"
shift

case "${action}" in
  up)
    docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" up -d "$@"
    ;;
  down)
    docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" down "$@"
    ;;
  logs)
    docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" logs -f "$@"
    ;;
  config)
    docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" config "$@"
    ;;
  ps)
    docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" ps "$@"
    ;;
  pull)
    docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" pull "$@"
    ;;
  build)
    docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" build "$@"
    ;;
  *)
    usage
    exit 1
    ;;
esac
