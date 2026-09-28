#!/bin/zsh
set -eu
TASK_ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$TASK_ROOT/demo"
export HERDR_CONFIG_PATH="$TASK_ROOT/runtime/herdr.toml"
exec herdr --session herdr-fresh-20260926
