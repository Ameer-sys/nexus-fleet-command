#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
"${PYTHON:-python3}" -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m bbsim.model
echo "Ready: ./run_arms.sh, ./run_terrain.sh, ./run_lean.sh"
