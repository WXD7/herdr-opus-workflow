"""Thin adapter to the installed Herdr CLI; no shell focus or synthetic pane context."""
import json
import os
import re
import shlex
import subprocess
from pathlib import Path

from .snapshots import session_command


class Herdr:
    def __init__(self, session=None):
        if os.environ.get('HERDR_ENV') != '1' or not os.environ.get('HERDR_PANE_ID'):
            raise ValueError('Connect the dispatcher from a genuine Herdr shell pane; configuration UI needs no pane')
        self.session = session or os.environ.get('HERDR_SESSION')
        if not self.session:
            raise ValueError('An explicit Herdr session is required')
        self.caller = os.environ['HERDR_PANE_ID']
        self.call('pane', 'current', '--pane', self.caller)
        self.call('agent', 'list')

    def call(self, *args, timeout=40, output='json'):
        operation = ' '.join(args[:2])
        if output not in ('json', 'text') or output == 'text' and args[:2] not in (('agent', 'read'), ('pane', 'read')):
            raise ValueError('Text output is only supported for terminal reads')
        # Do not put arguments, prompts, environment values or terminal contents in errors.
        # A mutation may have succeeded before a transport/decoding failure: never retry it here.
        try:
            out = subprocess.run(['herdr', '--session', self.session, *args],
                                 capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f'Herdr {operation}: timed out; command outcome is unknown') from None
        if out.returncode:
            code = ''
            try:
                failure = json.loads(out.stderr.strip() or out.stdout.strip())
                candidate = failure.get('error', {}).get('code', '')
                if isinstance(candidate, str) and re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', candidate):
                    code = ' (' + candidate + ')'
            except (ValueError, AttributeError):
                pass
            raise RuntimeError(f'Herdr {operation}: exit {out.returncode}{code}; inspect the exact target, do not retry blindly')
        if output == 'text':
            return out.stdout
        try:
            value = json.loads(out.stdout)
            if not isinstance(value, dict):
                raise ValueError('Structured object required')
            return value.get('result', value)
        except ValueError:
            raise RuntimeError(f'Herdr {operation}: expected JSON, received {len(out.stdout)} characters; '
                               'command outcome must be inspected, not retried') from None

    def create(self, attempt, context):
        # Supplying env when the shell is created avoids races from typing setup then a command.
        _, env = session_command(context, attempt['context_path'])
        env['PATH'] = str(Path(context['workflow']['runtime_root']) / 'observability/langwatch/instrumentation/bin') + os.pathsep + os.environ['PATH']
        args = ['workspace', 'create', '--cwd', attempt['checkout'], '--label', context['title_line'], '--no-focus']
        for key, value in env.items():
            args.extend(['--env', key + '=' + value])
        result = self.call(*args)
        pane = result['root_pane']['pane_id']
        return {'session': self.session, 'pane': pane, 'name': 'exp-' + attempt['id'][-16:]}

    def start(self, target, attempt, context):
        command, _ = session_command(context, attempt['context_path'])
        # Herdr resolves canonical claude through the wrapper PATH supplied to this pane.
        return self.call('agent', 'start', target['name'], '--kind', 'claude', '--pane', target['pane'],
                         '--timeout', '30000', '--', *command[1:])

    def inspect(self, target):
        if target['session'] != self.session:
            raise ValueError('Herdr target session mismatch')
        result = self.call('agent', 'get', target['name'])
        agent = result.get('agent', result)
        actual_pane = agent.get('pane_id') or (agent.get('pane') or {}).get('pane_id')
        actual_name = agent.get('name') or agent.get('agent_name')
        if actual_pane != target['pane'] or actual_name != target['name']:
            raise RuntimeError('Herdr target identity changed; refusing to control this pane')
        agent['status'] = agent.get('status') or agent.get('agent_status') or agent.get('state')
        return agent

    def prompt(self, target, prompt, before_submit=None, require_activity=True):
        current = self.inspect(target)
        status = current.get('status') or current.get('state')
        if status not in ('idle', 'done'):
            raise ValueError('Agent is not at a verified ready input; pending input/approval must be inspected')
        visible = self.call('agent', 'read', target['name'], '--source', 'visible', '--format', 'text', output='text')
        if not empty_input(visible):
            raise ValueError('Visible input is not confirmed empty; no text sent')
        if before_submit is not None:
            before_submit()  # Persist the send intent only after successful read-only preflight.
        # Herdr rejects recognized approval dialogs again at submission time.
        flags = ('--wait', '--until', 'working', '--timeout', '5000') if require_activity else ()
        return self.call('agent', 'prompt', target['name'], prompt, *flags, timeout=10)

    def stop(self, target):
        self.inspect(target)
        return self.call('agent', 'send-keys', target['name'], 'ctrl+c')

    def track(self, attempt):
        from .lifecycle import track
        return track(attempt)

    def cleanup(self, attempt, checkpoint):
        from .lifecycle import shutdown
        return shutdown(attempt, checkpoint)

    def read(self, target):
        self.inspect(target)
        return self.call('agent', 'read', target['name'], '--source', 'recent-unwrapped', '--lines', '35',
                         '--format', 'text', output='text')


def task_prompt(spec, attempt):
    """Task parameters for the existing Skill, not a replacement supervisor/system prompt."""
    return ('/herdr-dispatch:dispatch-codex ' + spec['task'] + '\n'
            '本次已批准验收：' + json.dumps(spec['acceptance'], ensure_ascii=False) + '\n'
            '本组配置与所有权在 HERDR_EXPERIMENT_CONTEXT；使用其明确模型/effort/子代设置。'
            '本组 lane checkout 只放在 ' + attempt['lanes_dir'] + '，创建 worktree 时显式使用 --path。'
            '如果需要本任务配置的服务，在准备好依赖后调用冻结版本 scripts/experiment.py service-start；'
            '它按本组确定性分配端口、检查进程所有权和健康，不自行猜端口或改杀其他服务。'
            '分配后的环境位于本组 service-environment.json，按它设置应用连接。'
            '本地验收后将各 lane 快进或普通合并至本组分支 ' + attempt['branch'] +
            '（已授权），不 push、不建 PR、不触碰其他组。'
            '完成时将现有最终报告另写到 ' + attempt['outcome_path'] +
            '，JSON 字段为 status(completed/failed)、summary、dispatch_state_path（本 run 的原 state.json 绝对路径）。'
            '此文件只触发独立检查，不代替验收。 --base ' + shlex.quote(attempt['branch']) +
            ' --lanes ' + str(min(3, attempt['profile']['max_subagents'])))


def empty_input(value):
    """Conservative Claude composer guard; an unknown screen never authorizes keystrokes."""
    if isinstance(value, dict):
        for key in ('text', 'output', 'content', 'screen'):
            if key in value and isinstance(value[key], (str, dict)):
                return empty_input(value[key])
        return False
    if not isinstance(value, str): return False
    value = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', value)
    lines = [x.strip() for x in value.splitlines() if x.strip()]
    candidates = [i for i,line in enumerate(lines) if line.startswith('❯')]
    if not candidates: return False
    i = candidates[-1]
    if lines[i] != '❯' or len(lines) - i > 6: return False
    if re.search(r'(?i)trust (?:this|the) (?:folder|workspace)|allow this|do you want to proceed|permission required', value):
        return False
    # Only known decorative/status footer lines may follow an empty composer.
    return all(re.fullmatch(r'[─━╭╮╰╯│\s]+', line) or
        re.match(r'(?i)(?:⏵⏵\s+)?(?:\? for shortcuts|shift\+tab|auto mode|accept edits|bypass permissions|\d+% context)',line)
        for line in lines[i+1:])
