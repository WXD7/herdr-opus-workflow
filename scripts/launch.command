#!/bin/zsh
set -eu
TASK_ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$TASK_ROOT/demo"
export HERDR_CONFIG_PATH="${HERDR_CONFIG_PATH:-$TASK_ROOT/runtime/herdr.local.toml}"
[[ -f "$HERDR_CONFIG_PATH" ]] || { print -u2 'Prepare runtime/herdr.local.toml as described in docs/setup.md.'; exit 1; }
exec herdr --session "${HERDR_WORKFLOW_SESSION:-herdr-opus-workflow}"
