import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
import urllib.parse
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('project_launch', ROOT / 'launch.py')
launch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launch)

SCRIPTS = launch.PROJECT / 'scripts'
# tm0ivs supervisor: its state recorded LEGACY, which LangWatch 3.17 stored as run-[CRYPTO].
SESSION = '18b2d22b-75ce-4444-8e9b-89e8735e951f'
LEGACY = 'run-18b2d22b75ce44448e9b89e8735e951f'
CANONICAL = 'run-' + SESSION
# Sources one prepare script, then reports its status and the environment it exported.
SOURCE_AND_DUMP = ('cd "$1" || exit 1; py=$2; shift 2; source "$@"; "$py" -c '
                   "'import json, os, sys; print(json.dumps(dict(os.environ, PREPARE_RC=sys.argv[1])))' $?")


def clean_env(**extra):
    """The test runner's own run label, telemetry destination and pane identity stay out."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(('HERDR_', 'OTEL_'))}
    env.update(extra)
    return env


def entry(*args, script='start-claude.sh', **env):
    return subprocess.run([str(SCRIPTS / script), *args], capture_output=True, text=True,
                          env=clean_env(**env), timeout=60)


def entry_check(*args, script='start-claude.sh', **env):
    out = entry('--check', *args, script=script, **env)
    if out.returncode:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


def source(script, *args, pane=True, cwd=None, **env):
    """Source a prepare script in a throwaway zsh; return (status, exported env, output).

    With pane=True, HERDR_ENV/HERDR_PANE_ID are synthetic guard values: no herdr command runs and
    nothing is launched, so this covers the script's own logic, not a Herdr launch."""
    extra = {'HERDR_ENV': '1', 'HERDR_PANE_ID': 'synthetic-test-pane'} if pane else {}
    out = subprocess.run(['zsh', '-f', '-c', SOURCE_AND_DUMP, 'zsh', str(cwd or launch.PROJECT / 'demo'),
                          sys.executable, str(SCRIPTS / script), *args],
                         capture_output=True, text=True, env=clean_env(**extra, **env), timeout=60)
    exported = json.loads(out.stdout.splitlines()[-1])
    return int(exported.pop('PREPARE_RC')), exported, out


def resource_attributes(env):
    return {key: urllib.parse.unquote(value) for key, value in
            (item.split('=', 1) for item in env['OTEL_RESOURCE_ATTRIBUTES'].split(','))}


def span_attributes(argv):
    """Codex's `-c otel = {...}` override, parsed as the TOML Codex reads."""
    return tomllib.loads(next(value for value in argv if value.startswith('otel = ')))['otel']['span_attributes']


class PrivateFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.private = Path(self.temporary.name)
        (self.private / 'ingest-key').write_text('ik-lw-synthetic-test-only')
        (self.private / 'ingest-key').chmod(0o600)
        (self.private / 'endpoint').write_text('http://127.0.0.1:5560\n')
        self.private_patch = patch.object(launch, 'PRIVATE', self.private)
        self.private_patch.start()

    def tearDown(self):
        self.private_patch.stop()
        self.temporary.cleanup()


class LaunchBoundaryTest(PrivateFixture):
    def test_codex_keeps_auth_provider_and_notify_without_key_in_argv(self):
        original_home = os.environ.get('HOME')
        original_codex_home = os.environ.get('CODEX_HOME')
        with patch.dict(os.environ, {'HERDR_LANGWATCH_ROLE': 'worker:fixture',
                                    'ANTHROPIC_BASE_URL': 'unchanged-provider-value',
                                    'NO_PROXY': 'existing.internal', 'no_proxy': 'lower.internal',
                                    'HERDR_LANGWATCH_REAL_CODEX': '/usr/bin/true'}):
            _, args, env, context, chained = launch.prepare('codex', [
                'exec', '--ignore-user-config', '-c', 'notify=["/usr/bin/true", "existing"]'], check=True)
        self.assertEqual(env.get('HOME'), original_home)
        self.assertEqual(env.get('CODEX_HOME'), original_codex_home)
        self.assertEqual(env['ANTHROPIC_BASE_URL'], 'unchanged-provider-value')
        self.assertNotIn('ik-lw-synthetic-test-only', json.dumps(args))
        self.assertIn('ik-lw-synthetic-test-only', env['OTEL_EXPORTER_OTLP_HEADERS'])
        self.assertEqual(context['original_notify'], ['/usr/bin/true', 'existing'])
        self.assertTrue(chained)
        self.assertNotIn('HERDR_LANGWATCH_ROLE', env)
        self.assertEqual(context['role'], 'worker:fixture')
        self.assertIn('127.0.0.1', env['NO_PROXY'].split(','))
        self.assertIn('localhost', env['no_proxy'].split(','))
        self.assertIn('existing.internal', env['NO_PROXY'].split(','))
        self.assertIn('lower.internal', env['no_proxy'].split(','))
        self.assertFalse((self.private / 'contexts').exists())

    def test_claude_content_and_worker_role_are_scoped(self):
        with patch.dict(os.environ, {'HERDR_LANGWATCH_REAL_CLAUDE': '/usr/bin/true'}):
            _, _, env, context, _ = launch.prepare('claude', [], check=True)
        self.assertEqual(context['role'], 'supervisor')
        self.assertEqual(env['OTEL_LOG_RAW_API_BODIES'], '0')
        self.assertEqual(env['OTEL_LOG_ASSISTANT_RESPONSES'], '1')
        self.assertEqual(env['OTEL_LOG_TOOL_CONTENT'], '1')

    def test_claude_preserves_dispatch_identity_and_native_subagent_profile(self):
        profile = {'HERDR_LANGWATCH_REAL_CLAUDE': '/usr/bin/true',
                   'HERDR_LANGWATCH_RUN_ID': 'run-fixture',
                   'HERDR_DISPATCH_SUPERVISOR_SESSION': '11111111-2222-4333-8444-555555555555',
                   'HERDR_LANGWATCH_ROLE': 'worker:fixture',
                   'CLAUDE_CODE_SUBAGENT_MODEL': 'claude-opus-5-5',
                   'CLAUDE_CODE_SUBAGENT_MODEL_FORCE': '1',
                   'CLAUDE_CODE_EFFORT_LEVEL': 'max',
                   'CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH': '3',
                   'CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS': '20'}
        with patch.dict(os.environ, profile):
            _, _, env, context, _ = launch.prepare('claude', [], check=True)
        for key, value in profile.items():
            if key != 'HERDR_LANGWATCH_ROLE':
                self.assertEqual(env[key], value)
        self.assertEqual(context['run_id'], 'run-fixture')
        self.assertEqual(context['role'], 'worker:fixture')
        self.assertNotIn('HERDR_LANGWATCH_ROLE', env)

    def test_rejects_another_project(self):
        with patch.dict(os.environ, {'HERDR_LANGWATCH_REAL_CODEX': '/usr/bin/true'}):
            with self.assertRaisesRegex(ValueError, 'limited to'):
                launch.prepare('codex', ['exec', '-C', '/tmp'], check=True)

    def test_rejects_world_readable_key(self):
        (self.private / 'ingest-key').chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'mode 600'):
            launch.read_settings()


class RunLabelChainTest(PrivateFixture):
    """One herdr.run_id from the entry through state to every worker; nothing starts a model."""

    def setUp(self):
        super().setUp()
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(launch.PROJECT / 'demo')

    def launch_from(self, tool, pane_env, args=()):
        env = dict(pane_env, HERDR_LANGWATCH_REAL_CLAUDE='/usr/bin/true', HERDR_LANGWATCH_REAL_CODEX='/usr/bin/true')
        with patch.dict(os.environ, env, clear=True):
            _, argv, child, context, _ = launch.prepare(tool, list(args), check=True)
        return argv, child, context

    def assert_sent(self, label, role, child, context):
        attributes = resource_attributes(child)
        self.assertEqual((context['run_id'], context['resource_attributes']['herdr.run_id'],
                          attributes['herdr.run_id'], child['HERDR_LANGWATCH_RUN_ID']), (label,) * 4)
        self.assertEqual((context['role'], attributes['herdr.role']), (role, role))

    def supervisor(self, check):
        """The supervisor process env; driver §5a records its label as state.telemetry_run_id."""
        _, child, context = self.launch_from('claude', clean_env(
            HERDR_LANGWATCH_RUN_ID=check['langwatch']['run_id'], HERDR_LANGWATCH_ROLE=check['langwatch']['role'],
            HERDR_DISPATCH_SUPERVISOR_SESSION=check['orchestrator_session']))
        self.assert_sent(check['langwatch']['run_id'], 'supervisor', child, context)
        return child['HERDR_LANGWATCH_RUN_ID']

    def test_new_run_label_is_identical_from_entry_to_state_to_workers(self):
        check = entry_check()
        label = check['langwatch']['run_id']
        self.assertEqual(label, 'run-' + check['orchestrator_session'])
        self.assertEqual(launch.run_ids.label_format(label), 'canonical')
        state_value = self.supervisor(check)
        self.assertEqual(state_value, label)
        for script, tool, args in (('prepare-claude-observed.zsh', 'claude', ()),
                                   ('prepare-codex-observed.zsh', 'codex', ('exec', '--ignore-user-config'))):
            with self.subTest(script=script):
                status, pane_env, out = source(script, state_value, 'worker:lane-a')
                self.assertEqual(status, 0, out.stderr)
                self.assertNotIn('Legacy run id', out.stdout)
                argv, child, context = self.launch_from(tool, pane_env, args)
                self.assert_sent(label, 'worker:lane-a', child, context)
                self.assertNotIn('legacy_run_id', context)

    def test_resuming_the_redacted_session_keeps_one_label_on_every_path(self):
        labels = [entry_check(*args)['langwatch']['run_id'] for args in (
            ('--resume', SESSION), ('--resume', SESSION), ('-r', SESSION), ('--resume=' + SESSION,),
            ('--resume', SESSION.upper()), ('--session-id', SESSION))]
        labels.append(entry_check('--resume', SESSION, script='start-claude-observed.sh')['langwatch']['run_id'])
        self.assertEqual(set(labels), {CANONICAL})
        # The old supervisor recorded LEGACY; a resumed supervisor and its new workers still agree.
        self.assertEqual(launch.run_ids.normalize_run_id(LEGACY), CANONICAL)
        status, pane_env, _ = source('prepare-claude-observed.zsh', LEGACY, 'worker:lane-a')
        self.assertEqual((status, pane_env['HERDR_LANGWATCH_RUN_ID']), (0, CANONICAL))
        self.assertEqual(self.supervisor(entry_check('--resume', SESSION)), CANONICAL)

    def test_legacy_label_is_sent_as_the_same_uuid_on_every_path(self):
        other = LEGACY[:-1] + 'e'
        for legacy, label in ((LEGACY, CANONICAL), (other, 'run-18b2d22b-75ce-4444-8e9b-89e8735e951e')):
            with self.subTest(legacy=legacy):
                self.assertEqual(entry_check(HERDR_LANGWATCH_RUN_ID=legacy)['langwatch']['run_id'], label)
                for script, tool, args in (('prepare-claude-observed.zsh', 'claude', ()),
                                           ('prepare-codex-observed.zsh', 'codex', ('exec', '--ignore-user-config'))):
                    status, pane_env, out = source(script, legacy, 'worker:lane-a')
                    self.assertEqual(status, 0, out.stderr)
                    self.assertIn('Legacy run id %s is labelled %s (same UUID).' % (legacy, label), out.stdout)
                    self.assertIn('run=%s role=worker:lane-a' % label, out.stdout)
                    argv, child, context = self.launch_from(tool, pane_env, args)
                    self.assert_sent(label, 'worker:lane-a', child, context)
                    if tool == 'codex':
                        self.assertIn('"herdr.run_id" = "%s"' % label, ' '.join(argv))
                        self.assertNotIn(legacy, ' '.join(argv))
                # A pane exported before the fix still holds the legacy label: the launcher sends
                # the same UUID and keeps the mapping in its private launch context.
                _, child, context = self.launch_from('claude', clean_env(HERDR_LANGWATCH_RUN_ID=legacy))
                self.assert_sent(label, 'supervisor', child, context)
                self.assertEqual(context['legacy_run_id'], legacy)

    def test_custom_labels_pass_every_entry_verbatim(self):
        # Trailing newlines belong to a custom label. A bare $(...) in the zsh entries dropped them,
        # so paths disagreed; an all-newline label became '' and the launcher drew a random one.
        # A legacy id plus a newline is not an exact legacy match, so it is custom and kept as is.
        for custom in ('tm0ivs', 'herdr-demo-20260926', LEGACY + '-retry', 'herdr demo 开发',
                       'tm0ivs\n', 'tm0ivs\n\n\n', '\n', '\n\n', LEGACY + '\n', LEGACY + '\n\n'):
            with self.subTest(custom=custom):
                check = entry_check(HERDR_LANGWATCH_RUN_ID=custom)
                self.assertEqual(check['langwatch']['run_id'], custom)
                state_value = self.supervisor(check)
                self.assertEqual(state_value, custom)
                for script, tool, args in (('prepare-claude-observed.zsh', 'claude', ()),
                                           ('prepare-codex-observed.zsh', 'codex', ('exec', '--ignore-user-config'))):
                    status, pane_env, out = source(script, state_value)
                    self.assertEqual((status, pane_env['HERDR_LANGWATCH_RUN_ID']), (0, custom))
                    self.assertNotIn('Legacy run id', out.stdout)
                    argv, child, context = self.launch_from(tool, pane_env, args)
                    self.assert_sent(custom, 'worker', child, context)
                    self.assertNotIn('legacy_run_id', context)
                    if tool == 'codex':
                        self.assertEqual(span_attributes(argv)['herdr.run_id'], custom)

    def test_entry_and_launcher_checks_start_no_model(self):
        with tempfile.TemporaryDirectory() as directory:
            executed, fake = Path(directory) / 'executed', Path(directory) / 'bin'
            fake.mkdir()
            for name in ('claude', 'codex', 'herdr'):
                (fake / name).write_text('#!/bin/sh\necho "$0" >> "%s"\nexit 97\n' % executed)
                (fake / name).chmod(0o755)
            sentinels = {'PATH': str(fake) + os.pathsep + os.environ['PATH'],
                         'HERDR_LANGWATCH_REAL_CLAUDE': str(fake / 'claude')}
            check = entry_check('--resume', SESSION, **sentinels)
            self.assertIs(check['model_invoked'], False)
            self.assertEqual((check['langwatch']['run_id'], check['command'][0]), (CANONICAL, str(launch.BIN / 'claude')))
            refused = entry('--resume', SESSION, **sentinels)
            self.assertEqual(refused.returncode, 1)
            self.assertIn('inside the fresh Herdr pane', refused.stderr)
            with patch.dict(os.environ, clean_env(HERDR_LANGWATCH_RUN_ID=LEGACY, **sentinels), clear=True), \
                    patch.object(sys, 'argv', ['launch.py', '--check', 'claude']), \
                    patch.object(launch.os, 'execvpe', side_effect=AssertionError('exec')), \
                    contextlib.redirect_stdout(io.StringIO()) as printed:
                launch.main()
            shown = json.loads(printed.getvalue())
            self.assertEqual((shown['run_id'], shown['legacy_run_id'], shown['key']), (CANONICAL, LEGACY, '[redacted]'))
            self.assertNotIn('ik-lw-synthetic-test-only', printed.getvalue())
            # The zero-model --version probe records the label a real launch would send.
            with patch.dict(os.environ, clean_env(HERDR_LANGWATCH_RUN_ID=LEGACY, HERDR_LANGWATCH_PROBE='1',
                                                  **sentinels), clear=True), \
                    patch.object(sys, 'argv', ['launch.py', 'claude', '--version']), \
                    patch.object(launch.os, 'execv', side_effect=RuntimeError('exec')):
                with self.assertRaisesRegex(RuntimeError, 'exec'):
                    launch.main()
            self.assertEqual(json.loads((self.private / 'wrapper-probe.json').read_text())['run_id'], CANONICAL)
            self.assertFalse(executed.exists())

    def test_existing_entry_and_pane_boundaries_still_hold(self):
        for args in (('--model', 'other'), ('--effort=low',), ('--settings', '{}'), ('--plugin-dir', '/tmp'),
                     ('--permission-mode', 'bypassPermissions'), ('--dangerously-skip-permissions',),
                     ('--continue',), ('-c',), ('--fork-session',), ('--resume',), ('--resume', 'bad-id'),
                     ('--session-id', SESSION, '--resume', SESSION)):
            with self.subTest(args=args):
                out = entry('--check', *args)
                self.assertEqual((out.returncode, out.stdout), (2, ''))
        with tempfile.TemporaryDirectory() as directory:
            broken = Path(directory) / 'python3'
            broken.write_text('#!/bin/sh\nexit 3\n')
            broken.chmod(0o755)
            # Only the shared rule fails: the entry stops rather than export or print a label.
            rule_fails = Path(directory) / 'rule-fails' / 'python3'
            rule_fails.parent.mkdir()
            rule_fails.write_text('#!/bin/sh\ncase "$1" in */run_id.py) exit 3 ;; esac\nexec "%s" "$@"\n' % sys.executable)
            rule_fails.chmod(0o755)
            out = entry('--check', PATH=str(rule_fails.parent) + os.pathsep + os.environ['PATH'])
            self.assertEqual((out.returncode, out.stdout), (3, ''))
            for script in ('prepare-claude-observed.zsh', 'prepare-codex-observed.zsh'):
                executed = subprocess.run(['zsh', '-f', str(SCRIPTS / script), LEGACY], capture_output=True, text=True,
                                          env=clean_env(HERDR_ENV='1', HERDR_PANE_ID='synthetic-test-pane'))
                self.assertEqual(executed.returncode, 2)
                for label, kwargs, message in (
                        (LEGACY, {'pane': False}, 'genuine Herdr pane'), ('', {}, 'run id is required'),
                        (LEGACY, {'cwd': Path(directory)}, 'cd to this workflow project'),
                        (LEGACY, {'PATH': directory + os.pathsep + os.environ['PATH']}, 'could not be normalized')):
                    with self.subTest(script=script, message=message):
                        status, pane_env, out = source(script, label, **kwargs)
                        self.assertEqual(status, 2)
                        self.assertIn(message, out.stderr)
                        self.assertNotIn('HERDR_LANGWATCH_RUN_ID', pane_env)


if __name__ == '__main__':
    unittest.main()
