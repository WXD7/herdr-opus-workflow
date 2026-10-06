"""Exact launch ownership, disk completion signals and bounded process cleanup. No LLM calls."""
import hashlib
import datetime
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import uuid

from .config import digest
from .store import write_json


def processes():
    result = subprocess.run(['ps', '-axo', 'pid=,ppid=,pgid=,lstart=,stat=,comm='],
                            capture_output=True, text=True, timeout=5)
    if result.returncode:
        raise RuntimeError('Process inventory unavailable; cleanup not confirmed')
    table = {}
    for line in result.stdout.splitlines():
        fields = line.strip().split(None, 9)
        if len(fields) == 10 and all(v.isdigit() for v in fields[:3]) and not fields[8].startswith('Z'):
            pid = int(fields[0])
            table[pid] = {'pid': pid, 'ppid': int(fields[1]), 'pgid': int(fields[2]),
                          'started': ' '.join(fields[3:8]), 'command': fields[9]}
    if os.getpid() not in table:
        raise RuntimeError('Incomplete process inventory; cleanup not confirmed')
    return table


def register(context, context_path, args, executable):
    """Called immediately before exec: PID/start time survive exec; no argv secrets saved."""
    flags = args[:args.index('--')] if '--' in args else args
    sessions = [flags[i+1] for i, v in enumerate(flags[:-1]) if v in ('--session-id', '--resume')]
    sessions += [v.split('=', 1)[1] for v in flags if v.startswith(('--session-id=', '--resume='))]
    if len(sessions) != 1:
        raise ValueError('An exact session is required for process ownership')
    sid = str(uuid.UUID(sessions[0]))
    current = processes().get(os.getpid())
    if not current:
        raise ValueError('Cannot register process identity before launch')
    record = {**current, 'command': str(Path(executable).resolve()), 'session': sid,
              'attempt_id': context['attempt_id'], 'context_hash': digest(context),
              'cwd': str(Path.cwd().resolve()), 'registered_at': time.time(),
              'role': 'supervisor' if sid == context['supervisor_session'] else 'lane'}
    write_json(Path(context_path).parent / 'processes' / (str(uuid.uuid4()) + '.json'), record)


def registrations(attempt):
    root = Path(attempt['directory']).resolve()
    allowed = [Path(attempt['checkout']).resolve(), Path(attempt['lanes_dir']).resolve()]
    records = []
    for path in sorted((root / 'processes').glob('*.json')):
        if path.is_symlink() or not path.resolve().is_relative_to(root) or path.stat().st_size > 8000:
            raise ValueError('Invalid process registration path')
        record = json.loads(path.read_text())
        cwd = Path(record['cwd']).resolve()
        if record['attempt_id'] != attempt['id'] or record['context_hash'] != attempt['context_hash']:
            raise ValueError('Process registration belongs to another frozen group')
        if not any(cwd == p or cwd.is_relative_to(p) for p in allowed):
            raise ValueError('Process registration cwd outside group')
        uuid.UUID(record['session'])
        if type(record['pid']) is not int or record['pid'] <= 1 or not record.get('started'):
            raise ValueError('Invalid process identity')
        records.append(record)
    return records


def same_process(record, current):
    return bool(current and current['started'] == record['started'])


def track(attempt, table=None):
    table = processes() if table is None else table
    records = registrations(attempt)
    cleanup = attempt.setdefault('cleanup', {})
    owned = cleanup.setdefault('processes', {})
    for record in records:
        live = table.get(record['pid'])
        if same_process(record, live):
            # Registration is performed by the wrapper itself before exec. CLI
            # proctitle changes do not revoke PID/start-time ownership.
            key = str(record['pid']) + ':' + record['started']
            owned.setdefault(key, record | {'root': True})
    selected = {v['pid'] for v in owned.values() if same_process(v, table.get(v['pid']))}
    # Capture all descendant identities before any root is stopped, including native children.
    while True:
        children = {pid for pid, p in table.items() if p['ppid'] in selected} - selected
        if not children:
            break
        for pid in children:
            p = table[pid]
            owned.setdefault(str(pid) + ':' + p['started'], p | {'root': False})
        selected.update(children)
    cleanup['registered_supervisor'] = any(r['session'] == attempt['supervisor_session'] for r in records)
    cleanup['live_pids'] = sorted(selected)
    cleanup['checked_at'] = time.time()
    # A whole process group may only be signalled while its registered root is
    # still its leader. This closes the child-spawn race without touching a pane shell.
    return cleanup


def shutdown(attempt, checkpoint=lambda: None):
    """Two finite signals per exact owner at most, over normal dispatcher ticks; never sleep."""
    cleanup = track(attempt)
    if not cleanup['registered_supervisor']:
        cleanup.update(status='unverified', reason='Legacy run has no launch ownership registry; no PID inferred or killed')
        return False
    for record in sorted(cleanup['processes'].values(), key=lambda x: x.get('root', False)):
        if not same_process(record, processes().get(record['pid'])):
            continue
        now = time.time()
        if not record.get('term_attempted_at'):
            record['term_attempted_at'] = now
            checkpoint()  # Intent is durable before a signal, including interrupted dispatchers.
            try:
                if record.get('root') and record.get('pgid') == record['pid'] and os.getpgid(record['pid']) == record['pid']:
                    os.killpg(record['pid'], signal.SIGTERM)
                else: os.kill(record['pid'], signal.SIGTERM)
            except ProcessLookupError: pass
        elif now - record['term_attempted_at'] >= 3 and not record.get('kill_attempted_at'):
            record['kill_attempted_at'] = now
            checkpoint()
            try:
                if record.get('root') and record.get('pgid') == record['pid'] and os.getpgid(record['pid']) == record['pid']:
                    os.killpg(record['pid'], signal.SIGKILL)
                else: os.kill(record['pid'], signal.SIGKILL)
            except ProcessLookupError: pass
    cleanup = track(attempt)
    cleanup['status'] = 'stopping' if cleanup['live_pids'] else 'confirmed'
    if cleanup['status'] == 'confirmed':
        cleanup.pop('reason', None)
    live = [r for r in cleanup['processes'].values() if r['pid'] in cleanup['live_pids']]
    if live and all(r.get('kill_attempted_at') and time.time()-r['kill_attempted_at'] > 3 for r in live):
        cleanup.update(status='unverified', reason='Processes remain after the bounded stop attempts; inspect recorded identities')
    return cleanup['status'] == 'confirmed'


def completed_lanes(attempt):
    """Completion or one 15-minute idle fallback, scoped to exact registered lanes."""
    from scripts.claude_lane_state import probe
    result = []
    for record in registrations(attempt):
        if record['role'] != 'lane':
            continue
        checkout = Path(record['cwd']).resolve()
        marker = checkout / '.dispatch/DONE'
        if marker.is_symlink() or not marker.resolve().is_relative_to(checkout):
            continue
        exists = marker.is_file()
        if exists and marker.stat().st_size > 100000:
            raise ValueError('DONE marker is too large')
        state = probe(record['session'], str(checkout), context_path=attempt['context_path'],
                      context_hash=attempt['context_hash'])
        if state['turn_state'] != 'complete':
            continue
        modified = datetime.datetime.fromisoformat(state['mtime']).timestamp() if state.get('mtime') else None
        if not exists and (modified is None or time.time() - modified < 900):
            continue
        result.append({'session': record['session'], 'checkout': str(checkout),
                       'kind': 'done' if exists else 'idle_incomplete', 'marker': str(marker) if exists else None,
                       'sha256': hashlib.sha256(marker.read_bytes()).hexdigest() if exists else None})
    return sorted(result, key=lambda r: (r['checkout'], r['session']))
