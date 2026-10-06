"""Apply frozen settings to supervisor and lane launches without changing official login."""
import json
import uuid
from pathlib import Path
from .snapshots import context_environment


def pin_arguments(args, context):
    args = list(args)
    boundary = args.index('--') if '--' in args else len(args)
    flags, tail = args[:boundary], args[boundary:]
    blocked = {'--fallback-model', '--dangerously-skip-permissions', '--allow-dangerously-skip-permissions',
        '--continue', '-c', '-r', '--fork-session', '--allowedTools', '--agent', '--agents',
        '--system-prompt', '--append-system-prompt', '--worktree', '-w', '--betas'}
    if any(v.split('=', 1)[0] in blocked for v in flags):
        raise ValueError('Frozen experiments forbid fallback, ambiguous resumes and profile/permission overrides')
    desired = {'--model': context['profile']['model'], '--effort': context['profile']['effort'],
        '--permission-mode': 'auto', '--setting-sources': '',
        '--settings': json.dumps({'ultracode': False, 'autoMemoryEnabled': False})}
    # A lane must never become a supervisor by inheriting its plugin. Only the exact supervisor loads it.
    sessions = [flags[i+1] for i, v in enumerate(flags[:-1]) if v == '--session-id']
    sessions += [v.split('=',1)[1] for v in flags if v.startswith('--session-id=')]
    resumes = [flags[i+1] for i,v in enumerate(flags[:-1]) if v == '--resume']
    resumes += [v.split('=',1)[1] for v in flags if v.startswith('--resume=')]
    if resumes:
        if sessions or len(resumes) != 1: raise ValueError('Use one exact same-group resume UUID')
        from observability.collect_run import resolve_claude
        record = resolve_claude(Path.home() / '.claude', resumes[0], str(Path.cwd()))
        if record['status'] != 'resolved': raise ValueError('Resume identity/cwd was not verified in this group')
        observed = record['parsed']['observed']
        if any(m != context['profile']['model'] for m in observed.get('models') or ()):
            raise ValueError('Cannot resume another model profile')
        sessions = resumes
    if len(sessions) != 1:
        raise ValueError('Each experiment launch requires one fresh explicit session UUID')
    try: uuid.UUID(sessions[0])
    except ValueError: raise ValueError('Invalid session UUID') from None
    supervisor = sessions[0] == context['supervisor_session']
    if supervisor: desired['--plugin-dir'] = context['workflow']['plugin_dir']
    elif any(v.split('=',1)[0] == '--plugin-dir' for v in flags):
        raise ValueError('Workers use the original task brief, not a supervisor plugin override')
    for option, value in desired.items():
        actual, indices = [], []
        for i, arg in enumerate(flags):
            if arg == option:
                if i + 1 >= len(flags): raise ValueError('Missing frozen flag value')
                actual.append(flags[i+1]); indices.append(i+1)
            elif arg.startswith(option + '='):
                actual.append(arg.split('=',1)[1]); indices.append(i)
        if option == '--settings' and len(actual) == 1:
            parsed = json.loads(actual[0])
            if parsed not in ({'ultracode': False}, {'ultracode': False, 'autoMemoryEnabled': False}):
                raise ValueError('Settings conflict with frozen profile')
            i = indices[0]; flags[i] = option + '=' + value if flags[i].startswith(option+'=') else value
        elif actual and (len(actual) != 1 or actual[0] != value):
            raise ValueError('Agent flags conflict with frozen profile: ' + option)
        if not actual: flags.extend([option, value])
    if not context['profile']['subagents']:
        if any(v.split('=',1)[0] == '--disallowedTools' for v in flags):
            matches = [flags[i+1] for i,v in enumerate(flags[:-1]) if v == '--disallowedTools']
            if matches != ['Agent,Task']: raise ValueError('Native children are disabled for this group')
        else: flags.extend(['--disallowedTools', 'Agent,Task'])
    return flags + tail
