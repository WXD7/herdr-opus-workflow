#!/bin/zsh
set -eu
TASK_ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd)"
if [[ "${HERDR_ENV:-}" != 1 || -z "${HERDR_PANE_ID:-}" ]]; then
  print -u2 'This check must run inside the new Herdr pane.'
  exit 1
fi
cd "$TASK_ROOT/demo"
python3 - "$TASK_ROOT" <<'PY'
import json, os, pathlib, sys, datetime, subprocess
root = pathlib.Path(sys.argv[1])
fields = ('HERDR_ENV', 'HERDR_PANE_ID', 'HERDR_WORKSPACE_ID', 'HERDR_TAB_ID', 'HERDR_SESSION', 'HERDR_CONFIG_PATH')
data = {key: os.environ.get(key) for key in fields}
data['cwd'] = os.getcwd()
data['at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
for name in ('herdr', 'claude', 'codex'):
    p = subprocess.run([name, '--version'], capture_output=True, text=True)
    data[name + '_version'] = p.stdout.strip()
(root / 'evidence' / 'orchestrator-environment.json').write_text(json.dumps(data, indent=2) + '\n')
print(json.dumps(data, indent=2))
PY
herdr status > "$TASK_ROOT/evidence/herdr-status.json"
herdr agent list > "$TASK_ROOT/evidence/agents-before.json"
herdr --skill > "$TASK_ROOT/evidence/herdr-installed-skill.md"
