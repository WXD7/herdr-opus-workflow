"""Optional real terminal protocol check, run ONLY inside a new genuine Herdr pane.

Starts a real Claude CLI, checks its empty composer, submits only local /help, then
closes the owned test pane. No business task or model prompt is submitted. This
does not establish account model availability, generation quality or billing.
All observations stay in the caller's private --directory.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import uuid
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from experiments.config import profile, digest
from experiments.herdr import Herdr, empty_input
from experiments.snapshots import git, workflow_snapshot
from experiments.store import write_json


def run(directory, session, trusted_cwd=None):
    if os.environ.get('HERDR_ENV') != '1' or not os.environ.get('HERDR_PANE_ID'):
        raise ValueError('Probe requires genuine Herdr pane context')
    if not session.startswith('herdr-probe-'):
        raise ValueError('Use a dedicated herdr-probe-* test session, never a user session')
    directory = Path(directory).resolve()
    repo = Path(trusted_cwd).resolve() if trusted_cwd else directory / 'synthetic-repo'
    if not trusted_cwd:
        repo.mkdir(); git(repo, 'init', '-q')
        git(repo, 'config', 'user.name', 'Local transport probe')
        git(repo, 'config', 'user.email', 'probe@example.invalid')
        (repo / 'README.md').write_text('Synthetic transport probe. No business data.\n')
        git(repo, 'add', 'README.md'); git(repo, 'commit', '-qm', 'Synthetic fixture')
    before_git = git(repo, 'status', '--porcelain')
    p = profile({'model': 'claude-opus-5-5', 'effort': 'max'})
    provenance = workflow_snapshot(directory / 'workflow', p)
    aid = 'probe-' + uuid.uuid4().hex[:16]
    context_path = directory / 'context.json'
    context = {'schema': 1, 'state_root': str(directory), 'experiment_id': 'transport-probe',
               'group_id': 'Probe', 'attempt_id': aid, 'profile': p, 'workflow': provenance,
               'supervisor_session': str(uuid.uuid4()), 'telemetry_run_id': 'probe-' + str(uuid.uuid4()),
               'title_line': 'TRANSPORT PROBE — local help only', 'allowed_cwds': [str(repo)], 'runtime_env': {}}
    write_json(context_path, context)
    write_json(directory / 'workflow/context-pointer.json', {'path': str(context_path), 'hash': digest(context)})
    attempt = {'id': aid, 'checkout': str(repo), 'context_path': str(context_path), 'supervisor_session': context['supervisor_session']}
    transport = Herdr(session)
    proof = {'session': session, 'caller': transport.caller, 'attempt_id': attempt['id'],
             'supervisor_session': attempt['supervisor_session'], 'business_prompt_sent': False,
             'account_model_availability_verified': False}
    target = None
    try:
        target = transport.create(attempt, context)
        proof['target'] = target
        proof['start_response'] = transport.start(target, attempt, context)
        proof['identity'] = transport.inspect(target)
        screen = transport.call('agent', 'read', target['name'], '--source', 'visible', '--format', 'text', output='text')
        (directory / 'before.txt').write_text(screen)
        ansi = transport.call('agent', 'read', target['name'], '--source', 'visible', '--format', 'ansi', output='text')
        (directory / 'before.ansi.txt').write_text(ansi)
        proof['empty_composer'] = empty_input(screen)
        if not proof['empty_composer']:
            raise ValueError('Real composer not confirmed empty; no local command sent')
        def intent():
            proof['local_help_attempted'] = True
            write_json(directory / 'result.json', proof)
        proof['local_help_response'] = transport.prompt(target, '/help', before_submit=intent, require_activity=False)
        proof['local_help_submitted'] = True
        # Herdr confirms ordered keystrokes, not that Claude has rendered the command yet.
        time.sleep(1)
        after = transport.read(target)
        (directory / 'after.txt').write_text(after)
        proof['terminal_text_read'] = isinstance(after, str) and bool(after)
        proof['local_help_ui_verified'] = 'General' in after and 'Commands' in after
        proof['passed'] = proof['local_help_ui_verified']
    except Exception as error:
        proof.update(passed=False, error=str(error))
        if target is not None:
            try:
                screen = transport.call('pane', 'read', target['pane'], '--source', 'visible', '--format', 'text', output='text')
                (directory / 'failure-screen.txt').write_text(screen)
            except Exception as read_error:
                proof['read_error'] = str(read_error)
    finally:
        if target is not None:
            try:
                transport.call('pane', 'close', target['pane'])
                proof['test_pane_closed'] = True
            except Exception as error:
                proof['cleanup_error'] = str(error)
        write_json(directory / 'result.json', proof)
        proof['checkout_unchanged'] = git(repo, 'status', '--porcelain') == before_git
        write_json(directory / 'result.json', proof)
        print(json.dumps(proof, ensure_ascii=False), flush=True)
        # The session was explicitly created for this probe; stop only this server.
        transport.call('server', 'stop')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', required=True)
    parser.add_argument('--session', required=True)
    parser.add_argument('--trusted-cwd', help='Existing approved checkout; trust prompts are never answered by the probe')
    args = parser.parse_args()
    run(args.directory, args.session, args.trusted_cwd)
