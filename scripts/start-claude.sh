#!/bin/zsh
# Main entry: original herdr-dispatch Skill flow, supervisor fixed to Claude Opus 5.5 / max
# through the project LangWatch wrapper. `--check` prints the profile without Herdr or a model
# call; a real launch always requires a genuine Herdr pane.
set -eu
TASK_ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd)"
if [[ "${1:-}" == --experiments ]]; then
  shift
  exec python3.13 "$TASK_ROOT/scripts/experiment.py" "$@"
fi
if [[ -n "${HERDR_EXPERIMENT_CONTEXT:-}" ]]; then
  exec python3.13 "$TASK_ROOT/scripts/experiment.py" session "$@"
fi
PLUGIN_DIR="$TASK_ROOT/source/herdr-dispatch"
CLAUDE_WRAPPER="$TASK_ROOT/observability/langwatch/instrumentation/bin/claude"
RUN_ID_TOOL="$TASK_ROOT/observability/langwatch/instrumentation/run_id.py"
MODEL=claude-opus-5-5
EFFORT=max

check=0
for arg in "$@"; do
  [[ "$arg" == -- ]] && break
  case "${arg%%=*}" in
    --check) check=1 ;;
    --model|--fallback-model|--effort|--plugin-dir|--settings|--permission-mode|--dangerously-skip-permissions|--allow-dangerously-skip-permissions)
      print -u2 "start-claude.sh fixes model, effort, plugin and permission settings; refused: ${arg%%=*}"
      exit 2 ;;
  esac
done
if (( check )); then
  argv=("${(@)argv:#--check}")
fi

[[ -f "$PLUGIN_DIR/.claude-plugin/plugin.json" ]] || { print -u2 "herdr-dispatch plugin missing: $PLUGIN_DIR"; exit 1; }
[[ -x "$CLAUDE_WRAPPER" ]] || { print -u2 "LangWatch claude wrapper missing: $CLAUDE_WRAPPER"; exit 1; }

export HERDR_LANGWATCH_ROLE=supervisor
export CLAUDE_CODE_SUBAGENT_MODEL="$MODEL"
export CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1
export CLAUDE_CODE_EFFORT_LEVEL="$EFFORT"
export CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH=3
export CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS=20

# Make the supervisor identity available to the original Skill's state file.
# Exact resume keeps its identity; ambiguous pickers cannot supply a reliable mapping.
supervisor_session=''
session_option=''
session_option_count=0
for arg in "$@"; do
  if [[ -n "$session_option" ]]; then
    supervisor_session="$arg"
    session_option=''
    continue
  fi
  [[ "$arg" == -- ]] && break
  case "$arg" in
    --resume|-r|--session-id) session_option="$arg"; (( session_option_count += 1 )) ;;
    --resume=*|--session-id=*) supervisor_session="${arg#*=}"; (( session_option_count += 1 )) ;;
    --continue|-c|--fork-session)
      print -u2 'Use --resume <exact-session-UUID> so the original state and telemetry stay associated.'; exit 2 ;;
  esac
done
if [[ -n "$session_option" ]] || (( session_option_count > 1 )); then
  print -u2 'Supply at most one --session-id or --resume with an exact UUID.'; exit 2
fi
session_args=()
if (( session_option_count )); then
  python3 -c 'import sys,uuid; assert str(uuid.UUID(sys.argv[1])) == sys.argv[1].lower()' "$supervisor_session" || exit 2
else
  supervisor_session="$(python3 -c 'import uuid; print(uuid.uuid4())')"
  session_args=(--session-id "$supervisor_session")
fi
export HERDR_DISPATCH_SUPERVISOR_SESSION="$supervisor_session"
# An exact resume gets the same default telemetry label without searching past sessions.
# The label keeps the UUID's hyphens (LangWatch redacts a bare 32-hex run); the shared rule
# maps an inherited legacy run-<32 hex> label to that same UUID and keeps custom labels as-is.
# The dot keeps $(...) from stripping newlines that end a custom label; a rule failure skips
# the dot and fails the assignment, so set -e stops the entry.
run_id="$(python3 "$RUN_ID_TOOL" --shell "${HERDR_LANGWATCH_RUN_ID:-run-${(L)supervisor_session}}" && printf .)"
export HERDR_LANGWATCH_RUN_ID="${run_id%.}"

cmd=("$CLAUDE_WRAPPER" --plugin-dir "$PLUGIN_DIR" --model "$MODEL" --effort "$EFFORT"
     --settings '{"ultracode":false}' --permission-mode auto "${session_args[@]}")

if (( check )); then
  exec python3 - "$TASK_ROOT" "${cmd[@]}" "$@" <<'PY'
import hashlib, json, os, sys
root, command = sys.argv[1], sys.argv[2:]
plugin_dir = command[command.index('--plugin-dir') + 1]
with open(os.path.join(plugin_dir, '.claude-plugin', 'plugin.json')) as fh:
    manifest = json.load(fh)
rule_paths = ('skills/dispatch-codex/SKILL.md', 'skills/_shared/plan.md',
              'skills/_shared/supervise.md', 'skills/dispatch-codex/references/driver.md')
rule_hashes = {}
for relative in rule_paths:
    with open(os.path.join(plugin_dir, relative), 'rb') as fh:
        rule_hashes[relative] = hashlib.sha256(fh.read()).hexdigest()
snapshot_path = os.path.join(root, 'SNAPSHOT.json')
with open(snapshot_path) as fh:
    snapshot = json.load(fh)
rules_match = rule_hashes == snapshot.get('workflow_sha256')
env = {k: os.environ[k] for k in (
    'CLAUDE_CODE_SUBAGENT_MODEL', 'CLAUDE_CODE_SUBAGENT_MODEL_FORCE', 'CLAUDE_CODE_EFFORT_LEVEL',
    'CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH', 'CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS')}
print(json.dumps({
    'entry': os.path.join(root, 'scripts', 'start-claude.sh'),
    'model_invoked': False,
    'herdr_required_for_launch': True,
    'orchestrator_session': os.environ['HERDR_DISPATCH_SUPERVISOR_SESSION'],
    'profile': {'model': 'claude-opus-5-5', 'effort': 'max', 'permission_mode': 'auto', 'ultracode': False},
    'plugin': {'dir': plugin_dir, 'name': manifest.get('name'), 'version': manifest.get('version')},
    'workflow': {'version': snapshot.get('workflow_version') if rules_match else 'modified',
                 'snapshot_match': rules_match, 'sha256': rule_hashes},
    'langwatch': {'wrapper': command[0], 'run_id': os.environ['HERDR_LANGWATCH_RUN_ID'],
                  'role': os.environ['HERDR_LANGWATCH_ROLE']},
    'subagent_env': env,
    'worker': {'prepare': os.path.join(root, 'scripts', 'prepare-claude-observed.zsh'),
               'lane_state': os.path.join(root, 'scripts', 'claude_lane_state.py'),
               'role': 'worker', 'env': env},
    'command': command,
}, indent=2, ensure_ascii=False))
PY
fi

if [[ "${HERDR_ENV:-}" != 1 || -z "${HERDR_PANE_ID:-}" ]]; then
  print -u2 'Run this script inside the fresh Herdr pane: ../scripts/start-claude.sh'
  exit 1
fi
cd "$TASK_ROOT/demo"
"$TASK_ROOT/scripts/preflight.sh"
exec "${cmd[@]}" "$@"
