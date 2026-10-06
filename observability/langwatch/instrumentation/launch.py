#!/usr/bin/env python3.13
"""Project-only telemetry launcher. Auth/provider configuration is left to each CLI."""
import datetime
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tomllib
import urllib.parse
import uuid

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
PRIVATE = ROOT.parent / 'private'
BIN = ROOT / 'bin'
# The run label rule shared with the zsh entry points and collect_run.py.
spec = importlib.util.spec_from_file_location('herdr_run_id', ROOT / 'run_id.py')
run_ids = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_ids)
OTEL_KEYS = (
    'OTEL_EXPORTER_OTLP_ENDPOINT', 'OTEL_EXPORTER_OTLP_HEADERS',
    'OTEL_EXPORTER_OTLP_PROTOCOL', 'OTEL_RESOURCE_ATTRIBUTES',
    'OTEL_TRACES_EXPORTER', 'OTEL_LOGS_EXPORTER', 'OTEL_METRICS_EXPORTER',
    'CLAUDE_CODE_ENABLE_TELEMETRY', 'CLAUDE_CODE_ENHANCED_TELEMETRY_BETA',
    'OTEL_LOG_USER_PROMPTS', 'OTEL_LOG_TOOL_DETAILS', 'OTEL_LOG_TOOL_CONTENT',
    'OTEL_LOG_RAW_API_BODIES', 'OTEL_LOG_ASSISTANT_RESPONSES',
) + tuple('OTEL_EXPORTER_OTLP_' + signal + '_' + suffix
          for signal in ('TRACES', 'LOGS', 'METRICS') for suffix in ('ENDPOINT', 'HEADERS', 'PROTOCOL'))


def private_write(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w') as output:
        output.write(value)


def read_settings():
    key_path = PRIVATE / 'ingest-key'
    if key_path.stat().st_mode & 0o077:
        raise ValueError('private/ingest-key must have mode 600')
    key = key_path.read_text().strip()
    if not key or any(c.isspace() for c in key):
        raise ValueError('private/ingest-key must contain one nonempty token')
    endpoint = (PRIVATE / 'endpoint').read_text().strip().rstrip('/')
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or parsed.path not in ('', '/'):
        raise ValueError('private/endpoint must be a loopback HTTP base URL')
    return endpoint, key


def actual_binary(tool):
    override = os.environ.get('HERDR_LANGWATCH_REAL_' + tool.upper())
    search = os.pathsep.join(p for p in os.environ.get('PATH', '').split(os.pathsep)
                            if Path(p).resolve() != BIN.resolve())
    result = override or shutil.which(tool, path=search)
    if not result or Path(result).resolve() == (BIN / tool).resolve():
        raise ValueError('Original ' + tool + ' executable was not found')
    return str(Path(result).resolve())


def effective_cwd(args):
    cwd = Path.cwd()
    for index, arg in enumerate(args):
        if arg == '--':
            break
        if arg in ('-C', '--cd') and index + 1 < len(args):
            cwd = Path(args[index + 1]).expanduser().absolute()
        elif arg.startswith('--cd='):
            cwd = Path(arg.split('=', 1)[1]).expanduser().absolute()
    return cwd.resolve()


def original_notify(args):
    """Read only config TOML, never auth.json/keychain; preserve the selected notify."""
    codex_dir = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    config = {}
    if '--ignore-user-config' not in args and (codex_dir / 'config.toml').exists():
        config = tomllib.loads((codex_dir / 'config.toml').read_text())
    profile = config.get('profile')
    for index, arg in enumerate(args):
        if arg in ('-p', '--profile') and index + 1 < len(args):
            profile = args[index + 1]
        elif arg.startswith('--profile='):
            profile = arg.split('=', 1)[1]
    if profile:
        profile_path = codex_dir / (profile + '.config.toml')
        if profile_path.is_file():
            profile_config = tomllib.loads(profile_path.read_text())
            if 'notify' in profile_config:
                config['notify'] = profile_config['notify']
    cleaned = []
    index = 0
    while index < len(args):
        arg = args[index]
        value = None
        consumed = 1
        if arg in ('-c', '--config') and index + 1 < len(args):
            value, consumed = args[index + 1], 2
        elif arg.startswith('--config='):
            value = arg.split('=', 1)[1]
        if value and value.split('=', 1)[0].strip() == 'notify':
            config['notify'] = tomllib.loads(value)['notify']
        else:
            cleaned.extend(args[index:index + consumed])
        index += consumed
    notify = config.get('notify', [])
    if not isinstance(notify, list) or not all(isinstance(x, str) for x in notify):
        raise ValueError('Existing Codex notify must be a string array')
    return notify, cleaned, str(codex_dir.resolve())


def toml_string(value):
    return json.dumps(str(value), ensure_ascii=False)


def prepare(tool, args, check=False):
    executable = actual_binary(tool)
    cwd = effective_cwd(args)
    experiment = None
    pointer = PROJECT / 'context-pointer.json'
    if not os.environ.get('HERDR_EXPERIMENT_CONTEXT') and pointer.is_file():
        reference = json.loads(pointer.read_text())
        os.environ['HERDR_EXPERIMENT_CONTEXT'] = reference['path']
        os.environ['HERDR_EXPERIMENT_CONTEXT_HASH'] = reference['hash']
    if os.environ.get('HERDR_EXPERIMENT_CONTEXT'):
        sys.path.insert(0, str(PROJECT))
        from experiments.snapshots import load_context, context_environment
        from experiments.profile import pin_arguments
        experiment = load_context(cwd=cwd)
        if tool != 'claude':
            raise ValueError('This experimental profile is a Claude executor; other harnesses require an explicit adapter')
        args = pin_arguments(args, experiment)
    elif not cwd.is_relative_to(PROJECT):
        raise ValueError('This wrapper is limited to ' + str(PROJECT))
    endpoint, key = read_settings()
    env = dict(os.environ)
    if experiment:
        env.update(context_environment(experiment, os.environ['HERDR_EXPERIMENT_CONTEXT']))
    # Parent agent telemetry must not override this worker's selected signal destination.
    for name in OTEL_KEYS:
        env.pop(name, None)
    inherited = env.get('HERDR_LANGWATCH_RUN_ID')
    # A legacy run-<32 hex> label is sent as the same UUID with hyphens, which LangWatch keeps.
    run_id = run_ids.normalize_run_id(inherited or run_ids.new_run_id())
    # Consume a per-launch override so supervisor does not leak into worker children.
    role = env.pop('HERDR_LANGWATCH_ROLE', None) or ('supervisor' if tool == 'claude' else 'worker')
    launch_id = str(uuid.uuid4())
    attrs = {'herdr.run_id': run_id, 'herdr.role': role, 'herdr.launch_id': launch_id,
             'project.repo': PROJECT.name, 'service.name': 'claude-code' if tool == 'claude' else 'codex'}
    if experiment:
        attrs.update({'herdr.experiment_id': experiment['experiment_id'],
                      'herdr.group_id': experiment['group_id'], 'herdr.attempt_id': experiment['attempt_id'],
                      'herdr.requested_model': experiment['profile']['model'],
                      'herdr.requested_effort': experiment['profile']['effort']})
    if env.get('HERDR_LANGWATCH_SYNTHETIC') == '1':
        attrs['herdr.synthetic'] = 'true'
    env.update({
        'HERDR_LANGWATCH_RUN_ID': run_id,
        'OTEL_EXPORTER_OTLP_ENDPOINT': endpoint + '/api/otel',
        'OTEL_EXPORTER_OTLP_HEADERS': 'Authorization=Bearer ' + key,
        'OTEL_EXPORTER_OTLP_PROTOCOL': 'http/json',
        'OTEL_TRACES_EXPORTER': 'otlp',
        'OTEL_LOGS_EXPORTER': 'otlp',
        'OTEL_METRICS_EXPORTER': 'otlp',
        'OTEL_RESOURCE_ATTRIBUTES': ','.join(k + '=' + urllib.parse.quote(v, safe='-_./:') for k, v in attrs.items()),
        'LANGWATCH_ENDPOINT': endpoint,
        'LANGWATCH_CLI_CONFIG': str(PRIVATE / 'cli.json'),
        'LANGWATCH_NO_DAEMON': '1',
    })
    # Rust reqwest honors proxy env; Node fetch may not. Local telemetry must be
    # direct even when the surrounding shell uses a proxy for external traffic.
    for name in ('NO_PROXY', 'no_proxy'):
        bypass = [entry.strip() for entry in env.get(name, '').split(',') if entry.strip()]
        env[name] = ','.join(dict.fromkeys(bypass + ['127.0.0.1', 'localhost', '::1']))
    notify = []
    context_path = PRIVATE / 'contexts' / (launch_id + '.json')
    context = {'tool': tool, 'run_id': run_id, 'role': role, 'launch_id': launch_id,
               'cwd': str(cwd), 'endpoint': endpoint, 'resource_attributes': attrs,
               'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    if inherited and inherited != run_id:
        context['legacy_run_id'] = inherited
    if tool == 'claude':
        env.update({name: '1' for name in (
            'CLAUDE_CODE_ENABLE_TELEMETRY', 'CLAUDE_CODE_ENHANCED_TELEMETRY_BETA',
            'OTEL_LOG_USER_PROMPTS', 'OTEL_LOG_TOOL_DETAILS', 'OTEL_LOG_TOOL_CONTENT',
            'OTEL_LOG_ASSISTANT_RESPONSES')})
        env['OTEL_LOG_RAW_API_BODIES'] = '1' if env.get('HERDR_LANGWATCH_RAW_API_BODIES') == '1' else '0'
    else:
        notify, args, sessions_home = original_notify(args)
        context.update({'original_notify': notify, 'sessions_root': str(Path(sessions_home) / 'sessions')})
        env['HERDR_LANGWATCH_CONTEXT'] = str(context_path)
        telemetry = ['environment = "herdr-local"', 'log_user_prompt = true']
        for signal, suffix in [('trace_exporter', 'traces'), ('exporter', 'logs'), ('metrics_exporter', 'metrics')]:
            telemetry.append(signal + ' = { otlp-http = { endpoint = ' + toml_string(endpoint + '/api/otel/v1/' + suffix) + ', protocol = "json" } }')
        metadata = ', '.join(toml_string(k) + ' = ' + toml_string(v) for k, v in attrs.items() if k != 'service.name')
        telemetry.append('span_attributes = { ' + metadata + ' }')
        notify_argv = [shutil.which('node') or 'node', str(ROOT / 'notify.cjs'), str(context_path)]
        additions = ['-c', 'otel = { ' + ', '.join(telemetry) + ' }', '-c', 'notify = ' + json.dumps(notify_argv, ensure_ascii=False)]
        insert_at = args.index('--') if '--' in args else len(args)
        args = args[:insert_at] + additions + args[insert_at:]
        # Keep telemetry in this invocation rather than a shared app-server process.
        if '--no-daemon' not in args:
            args.insert(0, '--no-daemon')
    if not check:
        private_write(context_path, json.dumps(context, indent=2) + '\n')
    return executable, args, env, context, bool(notify)


def main():
    args = sys.argv[1:]
    check = bool(args and args[0] == '--check')
    if check:
        args.pop(0)
    if not args or args[0] not in ('claude', 'codex'):
        raise ValueError('Usage: launch.py [--check] claude|codex [original CLI arguments]')
    tool, args = args[0], args[1:]
    if not check and any(value in args for value in ('--version', '-V', '--help', '-h')):
        if os.environ.get('HERDR_LANGWATCH_PROBE') == '1':
            run_id = os.environ.get('HERDR_LANGWATCH_RUN_ID')
            # Record the label a real launch would send, not a legacy spelling of it.
            probe = {'tool': tool, 'cwd': str(Path.cwd()), 'run_id': run_id and run_ids.normalize_run_id(run_id),
                     'role': os.environ.get('HERDR_LANGWATCH_ROLE'),
                     'at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'model_started': False}
            private_write(PRIVATE / 'wrapper-probe.json', json.dumps(probe, indent=2) + '\n')
        os.execv(actual_binary(tool), [tool] + args)
    executable, args, env, context, chained = prepare(tool, args, check=check)
    if check:
        print(json.dumps({k: context[k] for k in ('tool', 'cwd', 'endpoint', 'run_id', 'legacy_run_id', 'role') if k in context}
                         | {'key': '[redacted]', 'original_notify_preserved': chained,
                            'auth_home_unchanged': env.get('CODEX_HOME') == os.environ.get('CODEX_HOME'),
                            'global_files_written': False, 'loopback_proxy_bypass': True}, indent=2))
        return
    # argv[0] stays the tool name so Herdr identifies the pane's agent by process name.
    os.execvpe(executable, [tool] + args, env)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, tomllib.TOMLDecodeError) as error:
        # Do not include source TOML, arguments, or environment in diagnostics.
        print('LangWatch project launcher: ' + str(error), file=sys.stderr)
        sys.exit(2)
