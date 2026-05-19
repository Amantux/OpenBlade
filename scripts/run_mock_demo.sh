#!/usr/bin/env bash
set -euo pipefail
openblade mock init --slots 20 --drives 1 --cartridges 5
openblade inventory
