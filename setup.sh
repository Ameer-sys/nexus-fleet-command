#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
uv sync --locked
echo "Ready: ./run_arms.sh, ./run_terrain.sh, ./run_lean.sh"
