#!/bin/zsh
# Compatibility alias; start-claude.sh is the main entry and applies the Opus/LangWatch profile.
set -eu
exec "$(cd -- "$(dirname -- "$0")" && pwd)/start-claude.sh" "$@"
