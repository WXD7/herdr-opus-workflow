#!/bin/sh
set -eu
LW_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$LW_DIR"
if [ -f images.env ]; then
  set -- --env-file images.env -f compose.yml "$@"
else
  set -- -f compose.yml "$@"
fi
exec docker compose "$@"
