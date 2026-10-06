import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from experiments.cli import main
from experiments.engine import Engine
from experiments.launch import prepare


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.engine = Engine(self.root / "space with 'quote' $(ignored)")

    @patch('experiments.launch.shutil.which', return_value='/a path/herdr')
    def test_native_startup_is_isolated_quoted_and_repeatable(self, _):
        with patch('experiments.launch.os.environ', {'PATH': '/test/bin:/usr/bin:/bin'}):
            result = prepare(self.engine, 2)
        self.assertFalse(result['model_invoked'])
        self.assertFalse(result['live_start_verified'])
        self.assertEqual(result, prepare(self.engine, 2))
        other = prepare(Engine(self.root / 'other'), 2)
        self.assertNotEqual(result['session'], other['session'])
        config = tomllib.loads(Path(result['herdr_config']).read_text())
        self.assertEqual(config['terminal']['default_shell'], result['pane_shell'])
        self.assertFalse(config['session']['resume_agents_on_restore'])
        shell = Path(result['pane_shell']).read_text()
        self.assertIn('"${HERDR_ENV:-}" = 1', shell)
        self.assertIn('"${HERDR_PANE_ID:-}"', shell)
        self.assertIn(shlex.quote(result['coordinator']), shell)
        self.assertNotIn('export HERDR_ENV', shell)
        self.assertIn('exec /bin/zsh -l', shell)
        entry = Path(result['launch_command']).read_text()
        self.assertIn(shlex.join(['/a path/herdr', '--session', result['session']]), entry)
        self.assertNotIn('--remote', entry)
        for key in ('pane_shell', 'launch_command', 'dashboard_command'):
            path = Path(result[key])
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)
            subprocess.run(['/bin/zsh', '-n', str(path)], check=True, capture_output=True)
        with self.assertRaises(ValueError):
            prepare(self.engine, 4)

    @patch('experiments.launch.shutil.which', return_value=None)
    def test_missing_herdr_does_not_claim_prepared(self, _):
        with self.assertRaises(ValueError):
            prepare(self.engine)
        self.assertFalse((self.engine.store.root / 'launch/launch.json').exists())

    def test_duplicate_dispatcher_cannot_steal_session_between_ticks(self):
        with patch('experiments.cli.Engine', return_value=self.engine), \
             patch('experiments.herdr.Herdr') as adapter, \
             patch.object(self.engine, 'tick') as tick, \
             patch.object(self.engine.services, 'recover') as recover:
            adapter.return_value.session = 'synthetic-session'
            adapter.return_value.caller = 'synthetic-pane'
            with self.engine.store.lock('dispatcher'):
                with self.assertRaises(BlockingIOError):
                    main(['worker', '--session', 'synthetic-session', '--once'])
            tick.assert_not_called()
            recover.assert_not_called()
            main(['worker', '--session', 'synthetic-session', '--once'])
            tick.assert_called_once()


if __name__ == '__main__':
    unittest.main()
