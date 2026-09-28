import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('claude_lane_state', HERE / 'claude_lane_state.py')
lane = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lane)

SESSION = '0f8a2c1e-3b4d-4e5f-8a9b-0c1d2e3f4a5b'
OTHER_SESSION = '11111111-2222-4333-8444-555555555555'
CHECKOUT = str(lane.ROOT / 'worktrees' / 'lane-a')
OTHER_CHECKOUT = str(lane.ROOT / 'worktrees' / 'lane-b')


def msg(kind, content=None, stop=None, **extra):
    rec = {'type': kind, 'sessionId': SESSION, 'cwd': CHECKOUT, 'isSidechain': False,
           'timestamp': '2026-09-27T00:00:00Z'}
    body = {'role': kind, 'content': content if content is not None else [{'type': 'text', 'text': 'x'}]}
    if kind == 'assistant':
        body.update(model='claude-opus-5-5', stop_reason=stop)
    rec['message'] = body
    rec.update(extra)
    return rec


def end_turn(**extra):
    return msg('assistant', stop='end_turn', **extra)


def tool_use():
    return msg('assistant', [{'type': 'tool_use', 'id': 't1', 'name': 'Bash', 'input': {}}], 'tool_use')


def tool_result():
    return msg('user', [{'type': 'tool_result', 'tool_use_id': 't1', 'content': 'ok'}])


def system(subtype):
    return {'type': 'system', 'subtype': subtype, 'sessionId': SESSION, 'cwd': CHECKOUT, 'isSidechain': False}


class LaneStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / (SESSION + '.jsonl')

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, records, tail=''):
        self.path.write_text(''.join(json.dumps(r) + '\n' for r in records) + tail)

    def probe(self, records=None, **kwargs):
        if records is not None:
            self.write(records)
        return lane.probe(SESSION, kwargs.pop('checkout', CHECKOUT), str(self.path), **kwargs)

    def test_end_turn_completes(self):
        result = self.probe([msg('user'), tool_use(), tool_result(), end_turn()])
        self.assertEqual((result['probe'], result['turn_state']), ('ok', 'complete'))
        self.assertEqual(result['last_event'], 'assistant:end_turn')
        self.assertIsNone(result['used_pct'])
        self.assertIsNone(result['goal_status'])
        self.assertFalse(result['out_of_room'])
        self.assertEqual(result['source_path'], str(self.path))
        self.assertTrue(result['tail']['complete'])
        self.assertFalse(result['compactions_partial'])

    def test_tool_use_and_following_user_records_are_working(self):
        self.assertEqual(self.probe([msg('user'), tool_use()])['turn_state'], 'working')
        self.assertEqual(self.probe([msg('user'), tool_use(), tool_result()])['last_event'], 'user:tool_result')
        result = self.probe([msg('user'), end_turn(), msg('user', 'next task')])
        self.assertEqual((result['turn_state'], result['last_event']), ('working', 'user:prompt'))
        self.assertEqual(self.probe([msg('user'), msg('assistant', stop=None)])['turn_state'], 'working')

    def test_interrupt_error_and_unknown_stop_are_not_complete(self):
        interrupt = msg('user', [{'type': 'text', 'text': '[Request interrupted by user]'}])
        api_error = end_turn(isApiErrorMessage=True)
        api_error['message']['content'] = [{'type': 'text', 'text': 'Prompt is too long'}]
        for records, event in [([msg('user'), tool_use(), interrupt], 'user:interrupt'),
                               ([msg('user'), msg('assistant', stop='max_tokens')], 'assistant:max_tokens'),
                               ([msg('user'), api_error], 'assistant:api_error')]:
            with self.subTest(event=event):
                result = self.probe(records)
                self.assertEqual((result['probe'], result['turn_state'], result['last_event']), ('ok', 'unknown', event))
        self.assertTrue(self.probe([msg('user'), api_error])['out_of_room'])

    def test_other_session_or_cwd_is_unavailable(self):
        foreign = end_turn(sessionId=OTHER_SESSION)
        moved = end_turn(cwd=OTHER_CHECKOUT)
        missing_cwd = end_turn()
        del missing_cwd['cwd']
        for records in ([msg('user'), foreign], [msg('user'), moved], [msg('user'), missing_cwd],
                        [msg('user'), end_turn(), dict(system('turn_duration'), sessionId=OTHER_SESSION)]):
            with self.subTest(records=records[-1]):
                result = self.probe(records)
                self.assertEqual((result['probe'], result['turn_state']), ('unavailable', 'unknown'))
        subdir = end_turn(cwd=CHECKOUT + '/src')
        self.assertEqual(self.probe([msg('user'), subdir])['turn_state'], 'complete')

    def test_sidechain_cannot_complete_main_thread(self):
        side = end_turn(isSidechain=True, cwd='/elsewhere/agent-worktree')
        result = self.probe([msg('user'), tool_use(), side])
        self.assertEqual((result['probe'], result['turn_state']), ('ok', 'working'))
        self.assertEqual(result['tail']['sidechain_records'], 1)
        foreign_side = end_turn(isSidechain=True, sessionId=OTHER_SESSION)
        self.assertEqual(self.probe([msg('user'), tool_use(), foreign_side])['probe'], 'unavailable')

    def test_late_metadata_keeps_end_turn(self):
        late = [system('turn_duration'), system('stop_hook_summary'),
                {'type': 'file-history-snapshot', 'messageId': 'm1', 'snapshot': {}},
                {'type': 'summary', 'summary': 's', 'leafUuid': 'u1'},
                {'type': 'custom-title', 'customTitle': 't', 'sessionId': SESSION},
                {'type': 'attachment', 'sessionId': SESSION, 'cwd': CHECKOUT, 'attachment': {}},
                {'type': 'progress', 'sessionId': SESSION, 'cwd': CHECKOUT,
                 'data': {'message': {'type': 'user', 'message': {'role': 'user', 'content': 'nested'}}}}]
        result = self.probe([msg('user'), end_turn()] + late)
        self.assertEqual((result['turn_state'], result['last_event']), ('complete', 'assistant:end_turn'))

    def test_missing_transcript_and_bad_json_are_unavailable(self):
        result = lane.probe(SESSION, CHECKOUT, str(self.path))
        self.assertEqual((result['probe'], result['turn_state'], result['reason']),
                         ('unavailable', 'unknown', 'transcript not found'))
        self.write([msg('user')], tail='{not json}\n' + json.dumps(end_turn()) + '\n')
        result = lane.probe(SESSION, CHECKOUT, str(self.path))
        self.assertEqual((result['probe'], result['turn_state']), ('unavailable', 'unknown'))
        self.write([{'type': 'file-history-snapshot', 'snapshot': {}}])
        self.assertEqual(lane.probe(SESSION, CHECKOUT, str(self.path))['probe'], 'unavailable')

    def test_incomplete_last_line_is_dropped_and_blocks_completion(self):
        self.write([msg('user'), end_turn()], tail='{"type": "user", "sessionId": "' + SESSION)
        result = lane.probe(SESSION, CHECKOUT, str(self.path))
        self.assertEqual((result['probe'], result['turn_state']), ('ok', 'unknown'))
        self.assertTrue(result['tail']['last_line_incomplete'])
        self.write([msg('user'), tool_use()], tail=json.dumps(end_turn()))
        result = lane.probe(SESSION, CHECKOUT, str(self.path))
        self.assertEqual(result['turn_state'], 'complete')
        self.assertFalse(result['tail']['last_line_incomplete'])

    def test_truncated_tail_drops_first_line_and_marks_counts_partial(self):
        filler = msg('user', 'y' * 5000)
        records = [system('compact_boundary')] + [filler] * 600 + [system('compact_boundary'), end_turn()]
        self.write(records)
        result = lane.probe(SESSION, CHECKOUT, str(self.path))
        self.assertGreater(result['tail']['file_bytes'], lane.TAIL_BYTES)
        self.assertEqual(result['tail']['read_bytes'], lane.TAIL_BYTES)
        self.assertTrue(result['tail']['first_line_dropped'])
        self.assertFalse(result['tail']['complete'])
        self.assertEqual((result['probe'], result['turn_state']), ('ok', 'complete'))
        self.assertEqual((result['compactions'], result['compactions_partial']), (1, True))
        small = self.probe(tail_bytes=len(json.dumps(end_turn())) + 10)
        self.assertEqual((small['probe'], small['tail']['records']), ('ok', 1))

    def test_checkout_and_session_are_restricted(self):
        self.write([msg('user'), end_turn()])
        for session, checkout, transcript in [(SESSION, '/tmp/outside-lane', str(self.path)),
                                              (SESSION, 'worktrees/lane-a', str(self.path)),
                                              ('../../etc/passwd', CHECKOUT, None),
                                              (SESSION, CHECKOUT, str(self.path.with_name('other.jsonl')))]:
            with self.subTest(checkout=checkout, session=session):
                result = lane.probe(session, checkout, transcript)
                self.assertEqual((result['probe'], result['turn_state']), ('unavailable', 'unknown'))

    def test_cli_uses_exact_default_path(self):
        home = Path(self.tmp.name)
        expected = lane.default_transcript(home, Path(CHECKOUT), SESSION)
        self.assertEqual(expected.parent.name, '-' + ''.join(
            c if c.isascii() and c.isalnum() else '-' for c in CHECKOUT[1:]))
        expected.parent.mkdir(parents=True)
        expected.write_text(''.join(json.dumps(r) + '\n' for r in [msg('user'), end_turn()]))
        out = subprocess.run([sys.executable, str(HERE / 'claude_lane_state.py'), '--session', SESSION,
                              '--checkout', CHECKOUT], capture_output=True, text=True,
                             env=dict(os.environ, HOME=str(home)), check=True)
        result = json.loads(out.stdout)
        self.assertEqual((result['probe'], result['turn_state'], result['source']), ('ok', 'complete', 'default'))
        self.assertEqual(result['source_path'], str(expected))


if __name__ == '__main__':
    unittest.main()
