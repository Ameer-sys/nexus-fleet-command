#!/bin/bash
exec "$(cd "$(dirname "$0")" && pwd)/scripts/run_policy.sh" lean "$@"
