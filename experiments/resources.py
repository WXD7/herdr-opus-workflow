"""Owned subprocesses and port leases. Allocation, collision recovery and cleanup use zero LLMs."""
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request

from .store import write_json

_HANDLES = {}  # Retain our Popen handles until reaped; persisted birth records handle restarts.


def birth(pid):
    """Use kernel start ticks on Linux; process start time and command on macOS."""
    try:
        stat = Path(f'/proc/{int(pid)}/stat')
        if stat.exists():
            fields = stat.read_text().rsplit(')', 1)[1].split()
            if fields[0] == 'Z':
                return None
            return 'linux:' + fields[19]
        p = subprocess.run(['ps', '-p', str(int(pid)), '-o', 'lstart=', '-o', 'command='],
                           capture_output=True, text=True, timeout=3)
        return p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def owned(process):
    if not process or not process.get('birth') or birth(process.get('pid')) != process['birth']:
        return False
    try:
        return os.getpgid(process['pid']) == process['pid']
    except (OSError, TypeError):
        return False


def stop_owned(process):
    """Never kill by port/name, or signal a reused PID. Children in our group are included."""
    if not owned(process):
        if process:
            handle = _HANDLES.pop(process.get('pid'), None)
            if handle: handle.poll()
        return False
    os.killpg(process['pid'], signal.SIGTERM)
    until = time.monotonic() + 2
    while time.monotonic() < until and owned(process):
        time.sleep(.05)
    if owned(process):
        os.killpg(process['pid'], signal.SIGKILL)
    # Reap when this instance is still the parent. On recovery a process may be adopted.
    handle = _HANDLES.pop(process['pid'], None)
    if handle:
        try: handle.wait(timeout=2)
        except subprocess.TimeoutExpired: pass
    else:
        try: os.waitpid(process['pid'], os.WNOHANG)
        except ChildProcessError: pass
    return True


def spawn(argv, cwd, env, log):
    Path(log).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(log, 'ab', buffering=0) as output:
        p = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                             stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
    stamp = birth(p.pid)
    if not stamp:
        p.poll()
        raise RuntimeError('Service exited before its process identity could be recorded')
    _HANDLES[p.pid] = p
    return {'pid': p.pid, 'birth': stamp, 'argv': argv, 'log': str(log)}


def listener_owned(port, process):
    if not owned(process):
        return False
    binary = shutil.which('lsof') or ('/usr/sbin/lsof' if Path('/usr/sbin/lsof').exists() else None)
    if not binary:
        raise RuntimeError('lsof is required to verify service ownership; refusing to trust a port alone')
    result = subprocess.run([binary, '-nP', '-iTCP:' + str(port), '-sTCP:LISTEN', '-t'],
                            capture_output=True, text=True, timeout=3)
    pids = {int(value) for value in result.stdout.split() if value.isdigit()}
    if not pids:
        return False
    try:
        return all(os.getpgid(pid) == process['pid'] for pid in pids)
    except ProcessLookupError:
        return False


class Services:
    def __init__(self, store):
        self.store = store

    def start(self, attempt, specs, tries=3, health_seconds=5):
        if not specs:
            return {}
        # Port allocation and handoff are serialized. An unrelated program can still bind
        # during handoff, so ownership + health are checked and the whole bundle is retried.
        with self.store.lock('ports'):
            current = self._read(attempt['id'])
            if current and all(owned(v.get('process')) and listener_owned(v['port'], v['process'])
                               for v in current.values()):
                return current
            self._stop(attempt['id'], current)
            error = None
            for number in range(tries):
                sockets, leases = {}, {}
                try:
                    used = {v['port'] for record in self.store.list('services')
                            for v in record.get('leases', {}).values() if owned(v.get('process'))}
                    for spec in specs:
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sockets[spec['name']] = sock
                        preferred = spec['preferred_port'] if number == 0 else 0
                        try:
                            if preferred in used:
                                preferred = 0
                            sock.bind(('127.0.0.1', preferred))
                        except OSError:
                            sock.bind(('127.0.0.1', 0))
                        port = sock.getsockname()[1]
                        used.add(port)
                        leases[spec['name']] = {'port': port, 'url': f'http://127.0.0.1:{port}',
                                               'process': None, 'status': 'reserved'}
                    env = dict(os.environ)
                    env.update(attempt.get('runtime_env', {}))
                    substitutions = {}
                    for spec in specs:
                        name = spec['name']
                        prefix = name.upper().replace('-', '_')
                        env[spec['port_env']] = str(leases[name]['port'])
                        env[prefix + '_URL'] = leases[name]['url']
                        substitutions[name + '.port'] = str(leases[name]['port'])
                        substitutions[name + '.url'] = leases[name]['url']
                    write_json(Path(attempt['directory']) / 'service-environment.json',
                               {k: v for k, v in env.items() if k in [s['port_env'] for s in specs]
                                or k in [s['name'].upper().replace('-', '_') + '_URL' for s in specs]})
                    self._save(attempt['id'], leases)
                    for spec in specs:
                        name = spec['name']
                        replacements = substitutions | {'port': str(leases[name]['port']),
                                                          'cwd': attempt['checkout'], 'data': attempt['data_dir']}
                        command = []
                        for arg in spec['argv']:
                            for key, value in replacements.items():
                                arg = arg.replace('{' + key + '}', value)
                            command.append(arg)
                        sockets.pop(name).close()
                        process = spawn(command, attempt['checkout'], env,
                                        Path(attempt['directory']) / ('service-' + name + '.log'))
                        leases[name]['process'] = process
                        self._save(attempt['id'], leases)
                        deadline = time.monotonic() + health_seconds
                        ok = False
                        while time.monotonic() < deadline:
                            if not owned(process):
                                break
                            if listener_owned(leases[name]['port'], process):
                                try:
                                    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                                    with opener.open(leases[name]['url'] + spec['health_path'], timeout=.5) as response:
                                        ok = 200 <= response.status < 400
                                except (OSError, urllib.error.URLError):
                                    pass
                            if ok:
                                break
                            time.sleep(.1)
                        if not ok:
                            raise RuntimeError(f'{name}: owned listener/health check failed; see its service log')
                        leases[name]['status'] = 'healthy'
                        self._save(attempt['id'], leases)
                    return leases
                except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                    error = str(exc)
                    self._stop(attempt['id'], leases)
                finally:
                    for sock in sockets.values():
                        sock.close()
            raise RuntimeError(f'Service startup failed after {tries} bounded attempts: {error}')

    def _read(self, aid):
        try:
            return self.store.read('services', aid)['leases']
        except FileNotFoundError:
            return {}

    def _save(self, aid, leases):
        self.store.save('services', aid, {'attempt_id': aid, 'leases': leases, 'llm_calls': 0})

    def _stop(self, aid, leases):
        for lease in leases.values():
            stop_owned(lease.get('process'))
        self._save(aid, {})

    def stop(self, aid):
        with self.store.lock('ports'):
            self._stop(aid, self._read(aid))

    def recover(self):
        """Remove dead leases, retain verified live owners. No speculative process killing."""
        changes = []
        with self.store.lock('ports'):
            for record in self.store.list('services'):
                leases = record['leases']
                alive = {k: v for k, v in leases.items() if owned(v.get('process'))}
                if alive != leases:
                    self._save(record['attempt_id'], alive)
                    changes.append(record['attempt_id'])
        return changes
