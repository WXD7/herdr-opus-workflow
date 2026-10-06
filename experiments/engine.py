"""Shared experiment operations and a bounded, deterministic Herdr queue adapter."""
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time
import uuid

from . import VERSION
from .config import apply_patch, digest, differences, identifier, parse_instruction, validate
from .resources import Services
from .snapshots import ROOT, commit, git, workflow_snapshot
from .store import Store, write_json
from .herdr import task_prompt
from .checks import resolve_checks

TERMINAL = {'completed', 'failed', 'cancelled'}
ACTIVE = {'launching', 'running', 'blocked', 'needs_attention', 'checking', 'cancelling', 'finalizing'}


def record_issue(attempt, phase, error):
    message = str(error)[:1500]
    attempt.setdefault('error', message)
    issues = attempt.setdefault('issues', [])
    if not any(v['phase'] == phase and v['message'] == message for v in issues):
        issues.append({'phase': phase, 'message': message, 'at': time.time()})
    attempt['status'] = 'needs_attention'


class Engine:
    def __init__(self, state_dir, allowed_repos=(), runtime_root=ROOT):
        self.store = Store(state_dir)
        self.runtime_root = Path(runtime_root).resolve()
        self.allowed_repos = [Path(p).expanduser().resolve() for p in allowed_repos]
        self.services = Services(self.store)
        self.dispatcher_build = self._build_signature()

    def _build_signature(self):
        return digest({str(p.relative_to(self.runtime_root)): hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in sorted((self.runtime_root / 'experiments').glob('*.py'))})

    def repo_allowed(self, value):
        path = Path(value).resolve()
        if not any(path == root or path.is_relative_to(root) for root in self.allowed_repos):
            raise ValueError('Repository is outside the explicitly allowed roots; configure --allow-repo locally')
        if Path(git(path, 'rev-parse', '--show-toplevel')).resolve() != path:
            raise ValueError('Choose the repository root')
        return path

    def create(self, config):
        spec = validate(config)
        self.repo_allowed(spec['repo'])
        item = {'id': 'exp-' + uuid.uuid4().hex[:16], 'revision': 1, 'status': 'draft',
                'created_at': time.time(), 'config': spec, 'attempts': [], 'version': VERSION}
        with self.store.lock():
            self.store.save('experiments', item['id'], item)
            self.store.save('revisions', item['id'] + '-r1', spec)
        return self.get(item['id'])

    def get(self, eid):
        item = self.store.read('experiments', eid)
        for attempt in item['attempts']:
            path = Path(attempt['outcome_path'])
            if not attempt.get('business_status') and path.is_file() and path.stat().st_size < 100000:
                try: attempt['business_status'] = json.loads(path.read_text()).get('status', 'unknown')
                except (OSError, ValueError): pass
        return item | {'differences': differences(item['config'])}

    def update(self, eid, revision, config=None, operations=None, instruction=None):
        with self.store.lock():
            item = self.store.read('experiments', eid)
            if item['status'] != 'draft' or item['revision'] != revision:
                raise ValueError('Draft revision changed or the run is frozen; reload or clone it')
            if sum(v is not None for v in (config, operations, instruction)) != 1:
                raise ValueError('Provide exactly one form of configuration update')
            if instruction is not None:
                operations = parse_instruction(instruction)
            spec = validate(config) if config is not None else apply_patch(item['config'], operations)
            self.repo_allowed(spec['repo'])
            self.store.save('revisions', eid + '-r' + str(revision + 1), spec)
            item.update(config=spec, revision=revision + 1)
            self.store.save('experiments', eid, item)
        return self.get(eid)

    def clone(self, eid):
        return self.create(self.get(eid)['config'])

    def preset(self, name, config=None):
        identifier(name)
        if config is None:
            return self.store.read('presets', name)
        value = {'name': name, 'config': validate(config), 'saved_at': time.time()}
        with self.store.lock():
            self.store.save('presets', name, value)
        return value

    def enqueue(self, eid, revision, request_id):
        identifier(request_id)
        with self.store.lock('freeze-' + eid):
            with self.store.lock():
                item = self.store.read('experiments', eid)
                if item.get('start_request_id') == request_id:
                    return self.get(eid)
                if item['status'] != 'draft' or item['revision'] != revision:
                    raise ValueError('Only the exact reviewed draft revision can be started')
                spec = item['config']
                worker_path = self.store.root / 'worker.json'
                if worker_path.is_file():
                    from .resources import birth
                    worker = json.loads(worker_path.read_text())
                    if birth(worker['pid']) == worker.get('birth') and worker.get('dispatcher_build') != self.dispatcher_build:
                        raise ValueError('Connected dispatcher is an older build; cold-start the updated dispatcher before launch')
                if self._build_signature() != self.dispatcher_build:
                    raise ValueError('Platform files changed after server startup; restart the configuration server')
                repo = self.repo_allowed(spec['repo'])
                commands = resolve_checks(spec['checks'], repo)
                base = commit(repo, spec['base_ref'])
                item.update(status='preparing', start_request_id=request_id, base_commit=base,
                            config_hash=digest(spec), frozen_at=time.time(), acceptance_commands=commands)
                item['dispatcher_build'] = self.dispatcher_build
                self.store.save('experiments', eid, item)
            try:
                # The exact original tests are retained as an integrity check, in addition
                # to independent execution of the user-selected acceptance commands.
                baseline_tests = {}
                for rel in git(repo, 'ls-tree', '-r', '--name-only', base).splitlines():
                    if re.search(r'(^|/)(tests?|__tests__)(/|$)|(^|/)test_[^/]+|\.(test|spec)\.', rel):
                        baseline_tests[rel] = hashlib.sha256(git(repo, 'show', base + ':' + rel, binary=True)).hexdigest()
                item['baseline_test_hashes'] = baseline_tests
                frozen_versions = {}
                for group in spec['groups']:
                    aid = 'att-' + uuid.uuid4().hex[:16]
                    directory = self.store.root / 'runs' / eid / aid
                    directory.mkdir(parents=True, mode=0o700)
                    item.setdefault('provisioning', []).append({'attempt_id': aid, 'directory': str(directory)})
                    self.store.save('experiments', eid, item)
                    provenance = workflow_snapshot(directory / 'workflow', group['profile'], self.runtime_root)
                    version_key = group['profile']['workflow_ref']
                    fingerprints = (provenance['four_document_sha256'], provenance['adapter_sha256'])
                    if version_key in frozen_versions and frozen_versions[version_key] != fingerprints:
                        raise ValueError('Workflow changed during freeze; create a fresh draft from a stable version')
                    frozen_versions[version_key] = fingerprints
                    branch = 'experiment/' + eid + '/' + group['id'] + '-' + aid[-6:]
                    checkout = directory / 'checkout'
                    git(repo, 'worktree', 'add', '-b', branch, str(checkout), base)
                    runtime_dirs = {name: directory / name for name in ('data', 'tmp', 'cache', 'lanes')}
                    for folder in runtime_dirs.values():
                        folder.mkdir(mode=0o700)
                    env = {'TMPDIR': str(runtime_dirs['tmp']), 'XDG_CACHE_HOME': str(runtime_dirs['cache']),
                           'HERDR_DATA_DIR': str(runtime_dirs['data']), 'HERDR_LANE_ROOT': str(runtime_dirs['lanes']),
                           'HERDR_BUILD_DIR': str(directory / 'build')}
                    (directory / 'build').mkdir(mode=0o700)
                    # Tracked source-only snapshot: ignored .env/auth/builds are deliberately not copied.
                    session = str(uuid.uuid4())
                    title = f"{group['label']} | {group['profile']['model']} | {group['profile']['effort']} | {provenance['commit'][:8]}"
                    context = {'schema': 1, 'state_root': str(self.store.root), 'experiment_id': eid, 'group_id': group['id'], 'attempt_id': aid,
                               'profile': group['profile'], 'workflow': provenance, 'supervisor_session': session,
                               'telemetry_run_id': 'experiment-' + str(uuid.uuid4()), 'title_line': title,
                               'allowed_cwds': [str(checkout), str(runtime_dirs['lanes'])], 'runtime_env': env}
                    context_path = directory / 'context.json'; write_json(context_path, context)
                    write_json(directory / 'workflow/context-pointer.json', {'path': str(context_path), 'hash': digest(context)})
                    attempt = {'id': aid, 'group_id': group['id'], 'label': group['label'], 'profile': group['profile'],
                               'status': 'queued', 'directory': str(directory), 'checkout': str(checkout),
                               'lanes_dir': str(runtime_dirs['lanes']), 'data_dir': str(runtime_dirs['data']),
                               'branch': branch, 'base_commit': base, 'context_path': str(context_path),
                               'context_hash': digest(context), 'runtime_env': env, 'workflow': provenance,
                               'supervisor_session': session, 'telemetry_run_id': context['telemetry_run_id'],
                               'outcome_path': str(directory / 'outcome.json'), 'observed': {'model': None, 'effort': None},
                               'launch_attempted': False, 'prompt_attempted': False,
                               'prompt_submitted': False, 'prompt_stage': 'not_started', 'interventions': []}
                    item['attempts'].append(attempt)
                    self.store.save('experiments', eid, item)
                item['status'] = 'queued'
                self.store.event('experiment.queued', eid, detail={'groups': len(item['attempts'])})
            except Exception as error:
                item.update(status='failed', error=str(error)[:1600])
                for attempt in item['attempts']:
                    attempt['status'] = 'failed'
                self.store.event('experiment.failed', eid, detail={'reason': item['error']})
                raise
            finally:
                self.store.save('experiments', eid, item)
        return self.get(eid)

    def attempt(self, eid, aid):
        item = self.get(eid)
        return item, next(a for a in item['attempts'] if a['id'] == aid)

    def request_stop(self, eid, aid=None):
        with self.store.lock():
            item = self.store.read('experiments', eid)
            if item['status'] == 'preparing':
                raise ValueError('Configuration freeze is in progress; no model has started. Retry stop after preparation.')
            if aid is not None and aid not in [a['id'] for a in item['attempts']]:
                raise ValueError('Unknown attempt')
            for attempt in item['attempts']:
                if aid is None or aid == attempt['id']:
                    if attempt['status'] == 'queued':
                        attempt.update(status='cancelled', ended_at=time.time())
                    elif attempt['status'] in ACTIVE:
                        attempt['stop_requested'] = True
            self._aggregate(item)
            self.store.save('experiments', eid, item)
        return self.get(eid)

    def request_instruction(self, eid, aid, text, request_id):
        from .config import text as checked_text
        identifier(request_id)
        instruction = checked_text(text, 'instruction', 4000)
        with self.store.lock():
            item = self.store.read('experiments', eid)
            attempt = next(a for a in item['attempts'] if a['id'] == aid)
            existing = next((x for x in attempt['interventions'] if x['request_id'] == request_id), None)
            if existing:
                if existing['text'] != instruction:
                    raise ValueError('Request ID already used for a different instruction')
                return existing
            if attempt['status'] not in ACTIVE:
                raise ValueError('This attempt is not active')
            if attempt.get('result_status') or attempt.get('stop_attempted'):
                raise ValueError('This attempt is closing or stopped; additional model work requires a fresh authorized run')
            previous = attempt['interventions']
            if previous and time.time() - previous[-1]['at'] < 900:
                raise ValueError('One intervention per 15 minutes; no automatic retry')
            action = {'request_id': request_id, 'text': instruction, 'at': time.time(), 'status': 'pending'}
            previous.append(action)
            self.store.save('experiments', eid, item)
            return action

    def _aggregate(self, item):
        states = [a['status'] for a in item['attempts']]
        if not states:
            return
        if all(s in TERMINAL for s in states):
            item['status'] = 'completed' if all(s == 'completed' for s in states) else 'finished_with_issues'
            item.setdefault('ended_at', time.time())
            if not item.get('completion_event_recorded'):
                self.store.event('experiment.completed', item['id'], detail={'status': item['status']})
                item['completion_event_recorded'] = True
        elif any(s in ('needs_attention', 'blocked') for s in states):
            item['status'] = 'needs_attention'
        elif any(s in ACTIVE for s in states):
            item['status'] = 'running'

    def _checkpoint(self, item):
        # Reconcile UI stop/instruction writes without holding a lock across subprocess calls.
        with self.store.lock():
            current = self.store.read('experiments', item['id'])
            for attempt in item['attempts']:
                old = next(a for a in current['attempts'] if a['id'] == attempt['id'])
                if old.get('stop_requested'):
                    attempt['stop_requested'] = True
                seen = {v['request_id'] for v in attempt['interventions']}
                attempt['interventions'].extend(v for v in old['interventions'] if v['request_id'] not in seen)
                if old['status'] == 'cancelled' and attempt['status'] == 'queued':
                    attempt.update(status='cancelled', ended_at=old['ended_at'])
            self._aggregate(item)
            self.store.save('experiments', item['id'], item)

    def tick(self, adapter, capacity=4):
        """One worker process, bounded global admission; all slow I/O is outside the state lock."""
        from .resources import birth
        with self.store.lock('worker', blocking=False):
            write_json(self.store.root / 'worker.json', {'at': time.time(), 'session': adapter.session,
                       'caller_pane': adapter.caller, 'pid': os.getpid(), 'birth': birth(os.getpid()),
                       'capacity': capacity, 'dispatcher_build': self.dispatcher_build})
            items = self.store.list('experiments')
            active_total = sum(a['status'] in ACTIVE for i in items for a in i['attempts'])
            for item in items:
                if item['status'] == 'preparing':
                    try:
                        with self.store.lock('freeze-' + item['id'], blocking=False):
                            # A live enqueue holds this lock. A stale intent can only have created files/worktrees.
                            item.update(status='failed', error='Interrupted freeze; retained provisioning paths for recovery')
                            for a in item['attempts']: a['status'] = 'failed'
                            with self.store.lock(): self.store.save('experiments', item['id'], item)
                            self.store.event('experiment.failed', item['id'], detail={'reason': item['error']})
                    except BlockingIOError: pass
                    continue
                if item['status'] not in ('queued', 'running', 'needs_attention'):
                    continue
                if item.get('dispatcher_build') != self.dispatcher_build:
                    # Old frozen runs are historical evidence; never silently hot-upgrade them.
                    continue
                self._checkpoint(item)
                for attempt in item['attempts']:
                    if attempt['status'] in ACTIVE:
                        try:
                            self._observe(item, attempt, adapter)
                        except Exception as error:
                            record_issue(attempt, 'observe', error)
                            self.store.event('attempt.blocked', item['id'], attempt['id'], {'reason': attempt['error']})
                        if attempt['status'] not in ACTIVE:
                            active_total -= 1
                        self._checkpoint(item)
                active = sum(a['status'] in ACTIVE for a in item['attempts'])
                for attempt in item['attempts']:
                    if active >= item['config']['max_parallel'] or active_total >= capacity:
                        break
                    if attempt['status'] != 'queued':
                        continue
                    # Admission and the irreversible launch intent share one short transaction.
                    with self.store.lock():
                        current = self.store.read('experiments', item['id'])
                        latest = next(a for a in current['attempts'] if a['id'] == attempt['id'])
                        if latest['status'] == 'cancelled':
                            attempt.update(latest); continue
                        attempt.update(status='launching', started_at=time.time(), launch_attempted=True)
                        self.store.save('experiments', item['id'], item)
                    try:
                        context = json.loads(Path(attempt['context_path']).read_text())
                        target = adapter.create(attempt, context)
                        attempt['target'] = target
                        self._checkpoint(item)
                        adapter.start(target, attempt, context)
                        # Wait for a later deterministic tick to observe a ready agent.
                        # A slow first launch is normal, not permission to submit twice.
                        attempt['status'] = 'running'
                        self.store.event('attempt.started', item['id'], attempt['id'])
                    except Exception as error:
                        record_issue(attempt, 'launch', error)
                        self.store.event('attempt.blocked', item['id'], attempt['id'], {'reason': attempt['error']})
                    active += 1; active_total += 1
                    self._checkpoint(item)
                self._checkpoint(item)

    def _observe(self, item, attempt, adapter):
        if attempt.get('target') and attempt['target']['session'] != adapter.session:
            return
        if hasattr(adapter, 'track'):
            adapter.track(attempt)
        elapsed = time.time() - attempt.get('started_at', time.time())
        timed_out = elapsed >= item['config']['timeout_minutes'] * 60
        stop_due = attempt.get('stop_requested') or timed_out
        if attempt.get('result_status') or attempt.get('stop_attempted'):
            self._shutdown(item, attempt, adapter)
            return
        # Continue read-only observation after an uncertain send. Never resend it;
        # a delayed turn or a completed report may still resolve the uncertainty.
        if attempt['status'] == 'checking':
            attempt.update(status='needs_attention', error='Interrupted acceptance check; inspect evidence before resuming')
            return
        target = attempt.get('target')
        if not target:
            attempt.update(status='needs_attention', error='Interrupted launch without confirmed target; do not retry blindly')
            return
        if target['session'] != adapter.session:
            return
        try:
            state = adapter.inspect(target)
        except Exception as error:
            if not stop_due or not attempt.get('cleanup', {}).get('registered_supervisor'):
                raise
            record_issue(attempt, 'terminal_inspection', error)
            attempt.update(stop_attempted=True, stop_reason='timeout' if timed_out else 'requested')
            self._checkpoint(item)
            self._shutdown(item, attempt, adapter)
            return
        status = state.get('status') or state.get('state')
        attempt['herdr_status'] = status
        report = Path(attempt['outcome_path'])
        timely_report = report.is_file() and report.stat().st_mtime <= attempt['started_at'] + item['config']['timeout_minutes'] * 60
        if status in ('idle', 'done') and timely_report and not attempt.get('acceptance_attempted') and not attempt.get('stop_requested'):
            from .decisions import Mailbox
            if attempt['workflow'].get('decision_transport') == 'parent-v1' and any(
                    not v.get('consumed_at') for v in Mailbox(attempt).all()):
                record_issue(attempt, 'decision', 'Completion report has unresolved decisions; not accepted')
                if not stop_due:
                    return
            else:
                attempt.update(status='checking', acceptance_attempted=True)
                self._checkpoint(item)
                self._accept(item, attempt)
                if attempt.get('result_status'):
                    self._shutdown(item, attempt, adapter)
                return
        if stop_due:
            if not attempt.get('stop_attempted'):
                attempt.update(stop_attempted=True, stop_reason='timeout' if timed_out else 'requested', status='cancelling')
                if timed_out:
                    attempt.setdefault('error', 'Run deadline reached; stopping registered processes')
                self._checkpoint(item)
                # Legacy runs have no registry. Interrupt once but never infer PID ownership.
                if not attempt.get('cleanup', {}).get('registered_supervisor'):
                    adapter.stop(target)
                self._shutdown(item, attempt, adapter)
            return
        if attempt.get('prompt_attempted'):
            from .decisions import route
            if route(self, item, attempt, adapter):
                return
        if (attempt['status'] != 'needs_attention' and not attempt['prompt_attempted']
                and attempt.get('prompt_stage', 'not_started') == 'not_started' and status in ('idle', 'done')):
            attempt['prompt_stage'] = 'preflight'
            self._checkpoint(item)
            def before_submit():
                attempt.update(prompt_attempted=True, prompt_stage='submitting')
                self._checkpoint(item)
            adapter.prompt(target, task_prompt(item['config'], attempt), before_submit=before_submit)
            attempt.update(prompt_submitted=True, prompt_stage='submitted', prompt_submitted_at=time.time())
            self.store.event('attempt.dispatched', item['id'], attempt['id'])
            return
        for action in attempt['interventions']:
            if action['status'] != 'pending':
                continue
            if status not in ('idle', 'done'):
                continue  # Never type over a working agent, draft, question or permission dialog.
            action['status'] = 'attempted'
            self._checkpoint(item)
            try:
                def before_intervention():
                    action['submission_attempted'] = True
                    self._checkpoint(item)
                adapter.prompt(target, action['text'], before_submit=before_intervention); action['status'] = 'submitted'
                # An explicit intervention may replace a preflight-blocked first task;
                # never follow it by silently submitting the original task as a second turn.
                attempt.update(status='running', prompt_attempted=True)
                attempt.pop('error', None)
            except Exception as error:
                action.update(status='uncertain' if action.get('submission_attempted') else 'not_submitted', error=str(error)[:800])
            return
        if status == 'blocked':
            attempt['status'] = 'blocked'
            self.store.event('attempt.blocked', item['id'], attempt['id'], {'reason': 'Herdr recognized an approval/question; no keys sent'})
        elif status == 'working':
            attempt['status'] = 'running'
            attempt['activity_observed_at'] = time.time()
        elif status in ('idle', 'done') and attempt.get('prompt_attempted'):
            self._wake_completed(item, attempt, adapter)

    def _shutdown(self, item, attempt, adapter):
        self.services.stop(attempt['id'])
        complete = adapter.cleanup(attempt, lambda: self._checkpoint(item)) if hasattr(adapter, 'cleanup') else False
        if complete:
            result = attempt.get('result_status') or ('failed' if attempt.get('stop_reason') == 'timeout' else 'cancelled')
            attempt.update(status=result, ended_at=time.time())
            attempt.pop('shutdown_issue', None)
            self.store.event('attempt.' + result, item['id'], attempt['id'])
        else:
            cleanup = attempt.get('cleanup', {})
            attempt['status'] = 'finalizing' if cleanup.get('status') == 'stopping' else 'needs_attention'
            attempt['shutdown_issue'] = cleanup.get('reason', 'Owned descendant shutdown has not been confirmed')
            # Keep the original validation/dispatch error; cleanup is a separate dimension.

    def _wake_completed(self, item, attempt, adapter):
        from .lifecycle import completed_lanes
        if attempt['workflow'].get('completion_transport') != 'disk-v1':
            return  # Historical rule comparisons retain their explicitly selected transport.
        lanes = completed_lanes(attempt)
        if not lanes:
            return
        wakes = attempt.setdefault('completion_wakes', {})
        consumed = {(v['session'], v.get('kind', 'done')) for wake in wakes.values() for v in wake['lanes']}
        lanes = [v for v in lanes if (v['session'], v.get('kind', 'done')) not in consumed]
        if not lanes:
            return
        key = digest([(v['session'], v.get('kind', 'done')) for v in lanes])
        wake = wakes.setdefault(key, {'lanes': lanes, 'status': 'pending', 'created_at': time.time()})
        # An uncertain submission is consumed. Preflight failure sends nothing and
        # remains visible for an explicit user decision, never clears a composer.
        if wake['status'] != 'pending':
            return
        def intent():
            wake.update(status='attempted', attempted_at=time.time())
            self._checkpoint(item)
        try:
            prompt = ('/herdr-dispatch:dispatch-codex --resume\n仅恢复当前实验 ' + item['id'] +
                      ' 的本组 ' + attempt['id'] + '；会话 ' + attempt['supervisor_session'] +
                      '。已登记 lane 出现完成/空闲事件，请按原工作流做一次监督与验收。')
            adapter.prompt(attempt['target'], prompt, before_submit=intent)
            wake.update(status='submitted', submitted_at=time.time())
            self.store.event('attempt.completion_wake', item['id'], attempt['id'], {'key': key})
        except Exception as error:
            wake.update(status='uncertain' if wake['status'] == 'attempted' else 'not_submitted', error=str(error)[:800])
            record_issue(attempt, 'completion_wake', error)
            self.store.event('attempt.blocked', item['id'], attempt['id'], {'reason': attempt['error']})
        self._checkpoint(item)

    def _accept(self, item, attempt):
        path = Path(attempt['outcome_path'])
        if path.stat().st_size > 100000:
            raise ValueError('Completion report too large')
        report = json.loads(path.read_text())
        attempt['reported_summary'] = str(report.get('summary', ''))[:8000]
        attempt['business_status'] = report.get('status', 'unknown')
        integrity = [rel for rel, sha in item['baseline_test_hashes'].items()
                     if not (Path(attempt['checkout']) / rel).is_file()
                     or hashlib.sha256((Path(attempt['checkout']) / rel).read_bytes()).hexdigest() != sha]
        checks = []
        env = dict(os.environ) | attempt['runtime_env']
        service_env = Path(attempt['directory']) / 'service-environment.json'
        if service_env.is_file(): env.update(json.loads(service_env.read_text()))
        commands = item.get('acceptance_commands') or resolve_checks(item['config']['checks'], attempt['checkout'])
        for index, resolved in enumerate(commands):
            command = resolved['argv']
            log = Path(attempt['directory']) / f'acceptance-{index}.log'
            started = time.monotonic()
            # Keep the Popen handle: fast commands must not race a /proc or ps lookup.
            with log.open('wb') as output:
                execution_error = None
                try:
                    remaining = attempt['started_at'] + item['config']['timeout_minutes'] * 60 - time.time()
                    if remaining <= 0:
                        raise OSError('Acceptance deadline exhausted; command was not started')
                    process = subprocess.Popen(command, cwd=attempt['checkout'], env=env,
                        stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
                    code = process.wait(timeout=min(300, remaining))
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(); code = None
                except OSError as error:
                    code = None; execution_error = str(error)[:800]
            checks.append({'argv': command, 'requested_argv': resolved['requested_argv'],
                           'exit_code': code, 'passed': code == 0, 'execution_error': execution_error,
                           'wall_seconds': round(time.monotonic() - started, 3), 'log': str(log)})
        attempt['acceptance'] = {'checks': checks, 'modified_baseline_tests': integrity,
                                 'quality_review': 'not_automatically_scored'}
        attempt['head'] = git(attempt['checkout'], 'rev-parse', 'HEAD')
        attempt['diffstat'] = git(attempt['checkout'], 'diff', '--stat', item['base_commit'])
        attempt['working_tree_dirty'] = bool(git(attempt['checkout'], 'status', '--porcelain'))
        try:
            self._collect(item, attempt, report.get('dispatch_state_path'))
        except (OSError, ValueError, KeyError) as error:
            attempt.update(configuration_valid=None, evidence_gap=str(error)[:1200])
        attempt['comparison_eligible'] = attempt.get('configuration_valid') is True
        passed = report.get('status') == 'completed' and checks and all(c['passed'] for c in checks) and not integrity
        if passed and not attempt['working_tree_dirty']:
            attempt.update(status='finalizing', result_status='completed')
        elif report.get('status') == 'failed' or integrity or any(not c['passed'] for c in checks):
            attempt.update(status='finalizing', result_status='failed')
        else:
            attempt.update(status='needs_attention', error='Independent checks or clean commit are incomplete; actual-profile confidence is reported separately')
            self.store.event('attempt.blocked', item['id'], attempt['id'], {'reason': attempt['error']})

    def _collect(self, item, attempt, state_path):
        if not isinstance(state_path, str):
            attempt['configuration_valid'] = None
            attempt['evidence_gap'] = 'No exact dispatch state path supplied'; return
        path = Path(state_path).resolve()
        state_home = Path.home() / '.claude/dispatch-codex'
        if not path.is_relative_to(state_home.resolve()) or path.name != 'state.json' or path.stat().st_size > 500000:
            raise ValueError('Unexpected dispatch evidence path')
        state = json.loads(path.read_text())
        if state.get('orchestrator_session') != attempt['supervisor_session'] or state.get('telemetry_run_id') != attempt['telemetry_run_id']:
            raise ValueError('Dispatch identity does not match this group')
        from observability.collect_run import collect
        data = collect(str(path), str(Path.home() / '.codex'), str(Path.home() / '.claude'))
        write_json(Path(attempt['directory']) / 'run-summary.json', data)
        attempt['evidence_path'] = str(Path(attempt['directory']) / 'run-summary.json')
        coverage = data.get('coverage', {})
        observed_models = set(coverage.get('observed_models') or [])
        observed_efforts = set(coverage.get('observed_efforts') or [])
        from .evidence import descendants
        native = descendants(data)
        data['native_descendants'] = native
        write_json(Path(attempt['directory']) / 'run-summary.json', data)
        for thread in native['threads']:
            observed_models.update(thread['observed'].get('models') or [])
            observed_efforts.update(thread['observed'].get('efforts') or [])
        attempt['observed'] = {'models': sorted(observed_models), 'efforts': sorted(observed_efforts),
                               'coverage': coverage, 'native': native, 'unknowns': data.get('unknowns', [])}
        p = attempt['profile']
        mismatch = any(m != p['model'] for m in observed_models) or any(e != p['effort'] for e in observed_efforts)
        # Presence of one model/effort is insufficient to verify every descendant.
        attempt['configuration_valid'] = False if mismatch else None
        attempt['evidence_gap'] = ('Profile mismatch observed' if mismatch else
            'Recorded values agree where available; full descendant/effort coverage is not proven')
        attempt['usage'] = {'supervisor_and_lanes': coverage.get('claude_main_thread_usage_sum'),
                            'native_observed_sum': native['observed_usage_sum'],
                            'total_billable': None}

    def compare(self, eid):
        item = self.get(eid)
        return {'experiment_id': eid, 'status': item['status'], 'baseline': item['config']['baseline'],
                'base_commit': item.get('base_commit'), 'config_hash': item.get('config_hash'),
                'groups': [{k: a.get(k) for k in ('id', 'group_id', 'label', 'profile', 'status', 'branch',
                           'checkout', 'head', 'diffstat', 'acceptance', 'reported_summary', 'configuration_valid',
                           'observed', 'comparison_eligible', 'usage', 'error', 'evidence_gap', 'evidence_path', 'interventions',
                           'prompt_stage', 'prompt_attempted', 'prompt_submitted', 'prompt_submitted_at', 'stop_reason',
                           'business_status', 'result_status', 'cleanup', 'shutdown_issue', 'completion_wakes', 'decisions', 'issues')}
                           | {'elapsed_seconds': round(a.get('ended_at', time.time()) - a['started_at'], 2) if a.get('started_at') else None,
                              'services': self.services._read(a['id'])}
                           for a in item['attempts']],
                'limitations': ['No automatic winner or quality score; compare the common acceptance and actual artifacts.',
                                'Multiple changed factors compare whole configurations, not individual causes.',
                                'Unknown usage/effort/descendant coverage remains unknown; failed attempts are retained.',
                                'Completion and shutdown are separate. Exact registered processes are stopped; legacy ownership remains unknown.',
                                'This is not an account-wide or remote-provider token spending cap.']}
