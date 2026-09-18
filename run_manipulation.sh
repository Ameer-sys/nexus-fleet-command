#!/bin/bash
cd "$(dirname "$0")" || exit 1
exec uv run --locked python -m bbsim.manipulation "$@"
