#!/bin/zsh
# Source this file in the target Herdr pane's current zsh before `agent start`.
if [[ "${ZSH_EVAL_CONTEXT:-}" != *:file ]]; then
  print -u2 'Source this script in the worker pane: source /absolute/path/prepare-codex-observed.zsh RUN_ID [ROLE]'
  return 2 2>/dev/null || exit 2
fi
if [[ "${HERDR_ENV:-}" != 1 || -z "${HERDR_PANE_ID:-}" ]]; then
  print -u2 'This setup requires a genuine Herdr pane.'
  return 2
fi
if [[ -z "${1:-}" ]]; then
  print -u2 'A workflow run id is required.'
  return 2
fi
typeset _lw_script_path="${(%):-%x}"
typeset _lw_project_root="${_lw_script_path:A:h:h}"
typeset _lw_wrapper_bin="$_lw_project_root/observability/langwatch/instrumentation/bin"
case "$PWD/" in
  "$_lw_project_root/"*) ;;
  *) print -u2 'cd to this workflow project or its worktree before sourcing this script.'; return 2 ;;
esac
# Shared run label rule: a legacy run-<32 hex> id recorded in state becomes the same UUID with
# hyphens (LangWatch redacts the bare hex); canonical and custom ids are used as given. The dot
# keeps $(...) from stripping newlines that end a custom id; it is only printed on success.
typeset _lw_run_id
if ! _lw_run_id="$(python3 "$_lw_project_root/observability/langwatch/instrumentation/run_id.py" --shell "$1" && printf .)"; then
  print -u2 'The workflow run id could not be normalized; do not launch the worker.'
  unset _lw_script_path _lw_project_root _lw_wrapper_bin _lw_run_id
  return 2
fi
_lw_run_id="${_lw_run_id%.}"
[[ "$_lw_run_id" == "$1" ]] || print -r -- "Legacy run id $1 is labelled $_lw_run_id (same UUID)."
export HERDR_LANGWATCH_RUN_ID="$_lw_run_id"
export HERDR_LANGWATCH_ROLE="${2:-worker}"
export PATH="$_lw_wrapper_bin:$PATH"
rehash
if [[ "$(command -v codex)" != "$_lw_wrapper_bin/codex" ]]; then
  print -u2 'Codex did not resolve to the project wrapper; do not launch the worker.'
  return 2
fi
print "Codex wrapper selected in this pane; run=$HERDR_LANGWATCH_RUN_ID role=$HERDR_LANGWATCH_ROLE"
print 'Launch via herdr agent start with --sandbox workspace-write --ask-for-approval on-request and no bypass flag.'
unset _lw_script_path _lw_project_root _lw_wrapper_bin _lw_run_id
