"""Prepare native Herdr startup files. Preparation never launches a process or model."""
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import uuid

from .config import identifier, integer
from .snapshots import ROOT
from .store import write_json


def prepare(engine, capacity=2):
    integer(capacity, 1, 32, 'capacity')
    binary = shutil.which('herdr')
    if not binary:
        raise ValueError('Install Herdr before preparing native startup')
    directory = engine.store.root / 'launch'
    coordinator = directory / 'coordinator'
    metadata = directory / 'launch.json'
    with engine.store.lock('launch'):
        # Keep the same named session on retries; never attach the focused/old session.
        if metadata.exists():
            previous = json.loads(metadata.read_text())
            if previous['capacity'] != capacity:
                raise ValueError('Prepared capacity differs; use a separate state directory')
            return previous
        coordinator.mkdir(parents=True, exist_ok=True)
        session = 'herdr-exp-' + uuid.uuid4().hex[:16]
        identifier(session)
        common = [sys.executable, str(ROOT / 'scripts/experiment.py'), '--state-dir', str(engine.store.root)]
        for repo in engine.allowed_repos:
            common.extend(['--allow-repo', str(repo)])
        worker = common + ['worker', '--session', session, '--capacity', str(capacity)]
        # A real Herdr server injects the pane context. All business panes stay normal shells.
        shell = directory / 'pane-shell'
        shell.write_text('#!/bin/zsh\nset -eu\n' +
            'if [[ "${HERDR_ENV:-}" = 1 && -n "${HERDR_PANE_ID:-}" && "$PWD" = ' +
            shlex.quote(str(coordinator)) + ' ]]; then\n  ' +
            shlex.join(worker) + '\nfi\nexec /bin/zsh -l\n')
        config = directory / 'herdr.toml'
        config.write_text('[terminal]\ndefault_shell = ' + json.dumps(str(shell), ensure_ascii=False) +
                          '\nshell_mode = "non_login"\nnew_cwd = "follow"\n' +
                          '[session]\nresume_agents_on_restore = false\n')
        launch = directory / 'launch-herdr.command'
        launch.write_text('#!/bin/zsh\nset -eu\n' +
            'if [[ "${HERDR_ENV:-}" = 1 ]]; then\n  print -u2 "Open this file in a new ordinary terminal, outside existing Herdr panes."\n  exit 2\nfi\n' +
            'cd -- ' + shlex.quote(str(coordinator)) + '\n' +
            'export PATH=' + shlex.quote(os.environ.get('PATH', '/usr/bin:/bin')) + '\n' +
            'export HERDR_CONFIG_PATH=' + shlex.quote(str(config)) + '\n' +
            'exec ' + shlex.join([binary, '--session', session]) + '\n')
        dashboard = directory / 'dashboard.command'
        dashboard.write_text('#!/bin/zsh\nset -eu\nexec ' + shlex.join(common + ['serve']) + '\n')
        for path in (shell, launch, dashboard):
            path.chmod(0o700)
        result = {'session': session, 'capacity': capacity, 'state_dir': str(engine.store.root),
                  'herdr_config': str(config), 'pane_shell': str(shell), 'coordinator': str(coordinator),
                  'launch_command': str(launch), 'dashboard_command': str(dashboard),
                  'model_invoked': False, 'live_start_verified': False}
        write_json(metadata, result)
        return result
