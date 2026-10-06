"""Exercise the actual adapter, not just the queue's FakeHerdr abstraction."""
import json
import subprocess
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from experiments.herdr import Herdr, empty_input
from experiments.tests.test_experiments import Fixture, FakeHerdr


def adapter():
    # Unit fixture only. Live probes must construct Herdr in a genuine injected pane.
    value = object.__new__(Herdr)
    value.session = 'synthetic-session'
    value.caller = 'w1:p1'
    return value


TARGET = {'session': 'synthetic-session', 'pane': 'w2:p1', 'name': 'synthetic-agent'}
IDENTITY = json.dumps({'result': {'agent': {'pane_id': 'w2:p1', 'name': 'synthetic-agent', 'status': 'idle'}}})
SCREEN = 'Claude Code\n────────\n❯\n────────\n? for shortcuts\n'


def result(text):
    return subprocess.CompletedProcess([], 0, text, '')


class TransportTests(unittest.TestCase):
    def test_actual_2_1_290_footer_and_nonbreaking_prompt_space(self):
        screen = 'Claude Code v2.1.290\n◈ max · /effort\n──────\n❯\u00a0\n──────\n  ⏵⏵ auto mode on (shift+tab to cycle) · ← for age…\n'
        self.assertTrue(empty_input(screen))
        self.assertFalse(empty_input(screen.replace('❯\u00a0', '❯ Try "how does ipc.ts work?"')))
        self.assertFalse(empty_input(screen.replace('❯\u00a0', '❯ user draft')))

    def test_realistic_text_screen_then_single_structured_submission(self):
        intent = Mock()
        with patch('experiments.herdr.subprocess.run', side_effect=[result(IDENTITY), result(SCREEN), result('{"result":{"submitted":true}}')]) as run:
            value = adapter().prompt(TARGET, 'synthetic task', before_submit=intent)
        self.assertTrue(value['submitted'])
        intent.assert_called_once()
        self.assertEqual([c.args[0][3:5] for c in run.call_args_list], [['agent', 'get'], ['agent', 'read'], ['agent', 'prompt']])
        self.assertIn('--format', run.call_args_list[1].args[0])

    def test_draft_or_permission_never_records_send_intent(self):
        for screen in (SCREEN.replace('❯', '❯ unfinished draft'), 'Allow this action?\n❯'):
            intent = Mock()
            with self.subTest(screen=screen), patch('experiments.herdr.subprocess.run', side_effect=[result(IDENTITY), result(screen)]) as run:
                with self.assertRaisesRegex(ValueError, 'no text sent'):
                    adapter().prompt(TARGET, 'task', before_submit=intent)
            self.assertEqual(run.call_count, 2)
            intent.assert_not_called()

    def test_json_read_content_stays_text_and_structured_errors_stay_errors(self):
        with patch('experiments.herdr.subprocess.run', return_value=result('{"a":"user text"}')):
            self.assertEqual(adapter().call('agent', 'read', TARGET['name'], output='text'), '{"a":"user text"}')
        with patch('experiments.herdr.subprocess.run', return_value=result('private prompt token=secret')):
            with self.assertRaisesRegex(RuntimeError, 'Herdr agent prompt: expected JSON') as error:
                adapter().call('agent', 'prompt', 'synthetic-agent', 'confidential prompt')
        self.assertNotIn('secret', str(error.exception))
        self.assertNotIn('confidential', str(error.exception))
        with self.assertRaises(ValueError):
            adapter().call('agent', 'prompt', 'synthetic-agent', 'task', output='text')

    def test_read_failure_cannot_submit_and_timeout_names_operation(self):
        intent = Mock()
        with patch('experiments.herdr.subprocess.run', side_effect=[result(IDENTITY), subprocess.TimeoutExpired('hidden prompt', 2)]):
            with self.assertRaisesRegex(RuntimeError, 'Herdr agent read: timed out') as error:
                adapter().prompt(TARGET, 'task', before_submit=intent)
        self.assertNotIn('hidden', str(error.exception))
        intent.assert_not_called()


class DispatchFailureTests(Fixture):
    def test_supervisor_environment_reaches_workspace(self):
        attempt = self.run_config()['attempts'][0]
        context = json.loads(Path(attempt['context_path']).read_text())
        transport = adapter()
        transport.call = Mock(return_value={'root_pane': {'pane_id': 'w2:p1'}})
        transport.create(attempt, context)
        args = transport.call.call_args.args
        env = dict(args[i+1].split('=', 1) for i, value in enumerate(args) if value == '--env')
        self.assertEqual(env['HERDR_DISPATCH_SUPERVISOR_SESSION'], attempt['supervisor_session'])
        self.assertEqual(env['HERDR_LANGWATCH_ROLE'], 'supervisor')
        self.assertEqual(env['CLAUDE_CODE_EFFORT_LEVEL'], attempt['profile']['effort'])
        self.assertEqual(env['CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION'], 'false')

    def test_preflight_failure_does_not_claim_submitted_or_repeat(self):
        item = self.run_config(); transport = FakeHerdr(self.engine)
        def fail(*args, **kwargs): raise RuntimeError('Herdr agent read: synthetic failure')
        transport.prompt = Mock(side_effect=fail)
        self.engine.tick(transport); self.engine.tick(transport); self.engine.tick(transport)
        updated = self.engine.get(item['id'])
        self.assertEqual(updated['status'], 'needs_attention')
        self.assertEqual(transport.prompt.call_count, 2)
        for attempt in updated['attempts'][:2]:
            self.assertEqual(attempt['prompt_stage'], 'preflight')
            self.assertFalse(attempt['prompt_attempted'])
            self.assertFalse(attempt['prompt_submitted'])

    def test_uncertain_submission_keeps_intent_and_never_resends(self):
        item = self.run_config(); transport = FakeHerdr(self.engine)
        def uncertain(*args, before_submit=None):
            before_submit()
            raise RuntimeError('Herdr agent prompt: synthetic uncertain result')
        transport.prompt = Mock(side_effect=uncertain)
        self.engine.tick(transport); self.engine.tick(transport); self.engine.tick(transport)
        for attempt in self.engine.get(item['id'])['attempts'][:2]:
            self.assertTrue(attempt['prompt_attempted'])
            self.assertFalse(attempt['prompt_submitted'])
            self.assertEqual(attempt['prompt_stage'], 'submitting')
        self.assertEqual(transport.prompt.call_count, 2)

    def test_attention_still_reaches_deadline_without_auto_retry(self):
        item = self.run_config(); transport = FakeHerdr(self.engine)
        self.engine.tick(transport)
        item = self.engine.get(item['id'])
        for attempt in item['attempts'][:2]:
            attempt.update(status='needs_attention', started_at=time.time()-item['config']['timeout_minutes']*60-1)
        self.engine.store.save('experiments', item['id'], item)
        self.engine.tick(transport); self.engine.tick(transport)
        updated = self.engine.get(item['id'])
        self.assertEqual(len(transport.stops), 2)
        self.assertEqual(transport.prompts, [])
        self.assertEqual(len(transport.created), 2)  # Unknown descendants retain their slots.
        self.assertEqual(updated['status'], 'needs_attention')
        self.assertTrue(all(a.get('stop_reason') == 'timeout' for a in updated['attempts'][:2]))


if __name__ == '__main__':
    unittest.main()
