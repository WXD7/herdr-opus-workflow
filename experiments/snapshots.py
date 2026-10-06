"""Freeze code, four-document revisions and execution context before starting a session."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from .config import DEFAULT_PROFILE, RULE_FILES, digest, environment, profile
from .store import write_json

ROOT = Path(__file__).resolve().parents[1]


def git(repo, *args, binary=False):
    result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True,
                            text=not binary, timeout=60)
    if result.returncode:
        message = result.stderr.decode(errors='replace') if binary else result.stderr
        raise ValueError('git failed: ' + message.strip()[:1200])
    return result.stdout if binary else result.stdout.strip()


def commit(repo, ref):
    return git(repo, 'rev-parse', '--verify', '--end-of-options', ref + '^{commit}')


def workflow_snapshot(destination, selected, runtime_root=ROOT):
    destination, runtime_root = Path(destination), Path(runtime_root)
    if destination.exists():
        raise ValueError('Snapshot directory already exists; never overwrite a running version')
    p = profile(selected)
    ref = p['workflow_ref']
    sha = commit(runtime_root, 'HEAD' if ref == 'working-tree' else ref)
    dirty = bool(git(runtime_root, 'status', '--porcelain')) if ref == 'working-tree' else False
    prefix = 'source/herdr-dispatch/'
    if ref == 'working-tree':
        read = lambda rel: (runtime_root / prefix / rel).read_bytes()
    else:
        try:
            git(runtime_root, 'cat-file', '-e', sha + ':' + prefix + '.claude-plugin/plugin.json')
        except ValueError:
            prefix = ''  # Historical plugin-only tags.
        read = lambda rel: git(runtime_root, 'show', sha + ':' + prefix + rel, binary=True)
    content = {rel: read(rel) for rel in RULE_FILES}
    driver = content[RULE_FILES[-1]].decode()
    adapter = 'profile-v1' if 'HERDR_EXPERIMENT_CONTEXT' in driver else 'legacy-fixed-opus'
    if adapter == 'legacy-fixed-opus':
        if 'claude-opus-5-5' not in driver:
            raise ValueError('This historical revision uses another engine; select an explicitly adapted revision')
        if any(p[k] != DEFAULT_PROFILE[k] for k in DEFAULT_PROFILE if k != 'workflow_ref'):
            raise ValueError('This unchanged historical rule set fixes Opus 5.5/max and the original child limits. '
                             'Use its original profile or select the new parameterized rule revision.')
    destination.mkdir(parents=True, mode=0o700)
    plugin = destination / 'source/herdr-dispatch'
    for rel, data in content.items():
        target = plugin / rel; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
    manifest = plugin / '.claude-plugin/plugin.json'
    manifest.parent.mkdir(parents=True); manifest.write_bytes(read('.claude-plugin/plugin.json'))
    for name in ('plan.md', 'supervise.md'):
        (plugin / 'skills/dispatch-codex/references' / name).symlink_to('../../_shared/' + name)
    # Freeze the current platform adapter separately from the selected historical rules.
    excluded = shutil.ignore_patterns('private', 'node_modules', '__pycache__', '.venv',
                                       'runs', 'research', 'evidence', 'source', '*.log', '*.jsonl',
                                       '*.env', '.env*', '*.key', '*.pem', '*.sqlite*', '*.db')
    for directory in ('scripts', 'experiments', 'observability'):
        shutil.copytree(runtime_root / directory, destination / directory, ignore=excluded)
    private = runtime_root / 'observability/langwatch/private'
    if private.is_dir():
        (destination / 'observability/langwatch/private').symlink_to(private.resolve(), target_is_directory=True)
    if (runtime_root / 'SNAPSHOT.json').is_file():
        shutil.copy2(runtime_root / 'SNAPSHOT.json', destination / 'SNAPSHOT.json')
    hashes = {rel: hashlib.sha256(data).hexdigest() for rel, data in content.items()}
    adapter_hashes = {str(path.relative_to(destination)): hashlib.sha256(path.read_bytes()).hexdigest()
                      for directory in ('scripts', 'experiments', 'observability')
                      for path in (destination / directory).rglob('*')
                      if path.is_file() and 'private' not in path.relative_to(destination).parts
                      and path.suffix in ('.py', '.sh', '.zsh', '.cjs', '.html', '.js', '.css')}
    provenance = {'ref': ref, 'commit': sha, 'dirty': dirty, 'four_document_sha256': hashes,
                  'adapter': adapter, 'adapter_commit': commit(runtime_root, 'HEAD'),
                  'adapter_dirty': bool(git(runtime_root, 'status', '--porcelain')),
                  'adapter_sha256': digest(adapter_hashes), 'adapter_files': adapter_hashes,
                  'plugin_dir': str(plugin), 'runtime_root': str(destination)}
    write_json(destination / 'provenance.json', provenance)
    return provenance


def load_context(path=None, expected=None, cwd=None):
    path = Path(path or os.environ.get('HERDR_EXPERIMENT_CONTEXT', '')).resolve()
    expected = expected or os.environ.get('HERDR_EXPERIMENT_CONTEXT_HASH')
    if not expected or not path.is_file() or path.stat().st_size > 256000:
        raise ValueError('Missing frozen experiment context/hash')
    data = json.loads(path.read_text())
    if digest(data) != expected:
        raise ValueError('Experiment context changed; cold-start a new run')
    profile(data['profile'])
    location = Path(cwd or Path.cwd()).resolve()
    roots = [Path(p).resolve() for p in data['allowed_cwds']]
    if not any(location == p or location.is_relative_to(p) for p in roots):
        raise ValueError('cwd is outside this experiment group')
    plugin = Path(data['workflow']['plugin_dir'])
    actual = {rel: hashlib.sha256((plugin / rel).read_bytes()).hexdigest() for rel in RULE_FILES}
    if actual != data['workflow']['four_document_sha256']:
        raise ValueError('Frozen workflow documents changed; refusing to mix versions')
    runtime = Path(data['workflow']['runtime_root'])
    files = data['workflow'].get('adapter_files', {})
    if not files or any(hashlib.sha256((runtime / rel).read_bytes()).hexdigest() != value
                        for rel, value in files.items()):
        raise ValueError('Frozen platform adapter changed')
    return data


def context_environment(context, context_path):
    env = environment(context['profile']) | context['runtime_env']
    service_file = Path(context_path).parent / 'service-environment.json'
    if service_file.is_file():
        env.update(json.loads(service_file.read_text()))
    env.update({'HERDR_EXPERIMENT_CONTEXT': str(context_path),
                'HERDR_EXPERIMENT_CONTEXT_HASH': digest(context),
                'HERDR_EXPERIMENT_ID': context['experiment_id'],
                'HERDR_EXPERIMENT_GROUP': context['group_id'],
                'HERDR_EXPERIMENT_ATTEMPT': context['attempt_id'],
                'HERDR_WORKFLOW_ROOT': context['workflow']['runtime_root'],
                'HERDR_EXECUTOR_MODEL': context['profile']['model'],
                'HERDR_EXECUTOR_EFFORT': context['profile']['effort'],
                'HERDR_LANGWATCH_RUN_ID': context['telemetry_run_id']})
    return env


def session_command(context, context_path):
    p = context['profile']
    command = [str(Path(context['workflow']['runtime_root']) / 'observability/langwatch/instrumentation/bin/claude'),
               '--plugin-dir', context['workflow']['plugin_dir'], '--model', p['model'],
               '--effort', p['effort'], '--permission-mode', 'auto', '--settings',
               json.dumps({'ultracode': False, 'autoMemoryEnabled': False}),
               '--setting-sources', '', '--session-id', context['supervisor_session'],
               '--name', context['title_line']]
    if not p['subagents']:
        command.extend(['--disallowedTools', 'Agent,Task'])
    env = context_environment(context, context_path)
    env.update({'HERDR_LANGWATCH_ROLE': 'supervisor',
                'HERDR_DISPATCH_SUPERVISOR_SESSION': context['supervisor_session']})
    return command, env
