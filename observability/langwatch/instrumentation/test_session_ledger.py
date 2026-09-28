import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('session_ledger', ROOT / 'session_ledger.py')
ledger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ledger)


class SessionLedgerTest(unittest.TestCase):
    def make_rollout(self, directory):
        usage1 = dict(input_tokens=10, cached_input_tokens=4, cache_write_input_tokens=0,
                      output_tokens=2, reasoning_output_tokens=0, total_tokens=12)
        usage2 = dict(input_tokens=20, cached_input_tokens=8, cache_write_input_tokens=0,
                      output_tokens=3, reasoning_output_tokens=0, total_tokens=23)
        rows = [{'type': 'session_meta', 'payload': {'id': 'fixture-session', 'cwd': str(ledger.PROJECT)}}]
        for response, usage in [('response-1', usage1), ('response-1', usage1), ('response-2', usage2)]:
            rows.append({'type': 'token_usage_record', 'payload': {
                'thread_id': 'fixture-session', 'response_id': response, 'usage': usage}})
        total = {key: usage1[key] + usage2[key] for key in usage1}
        for unused in range(2):
            rows.append({'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {'total_token_usage': total}}})
        rows.append({'type': 'response_item', 'payload': {'type': 'custom_tool_call',
                     'name': 'exec', 'call_id': 'outer-call', 'input': 'print("exit 99 is just text")'}})
        command = {'type': 'event_msg', 'payload': {'type': 'item_completed', 'thread_id': 'fixture-session',
                   'turn_id': 'turn-1', 'item': {'type': 'CommandExecution', 'id': 'command-1',
                                               'status': 'failed', 'exit_code': 7}}}
        rows.extend([command, command])
        path = Path(directory) / 'rollout-fixture-session.jsonl'
        path.write_text('\n'.join(json.dumps(row) for row in rows) + '\n')
        return path, rows

    def test_exact_usage_and_results_are_deduplicated_and_replay_stable(self):
        with tempfile.TemporaryDirectory() as directory:
            path, rows = self.make_rollout(directory)
            first = ledger.build_ledger(path, 'fixture-session')
            second = ledger.build_ledger(path, 'fixture-session')
        self.assertEqual(first, second)
        self.assertEqual(first['usage']['authoritative_observed_sum']['input_tokens'], 30)
        self.assertEqual(first['usage']['authoritative_observed_sum']['output_tokens'], 5)
        self.assertEqual(first['usage']['authoritative_observed_sum']['cached_input_tokens'], 12)
        self.assertEqual(len(first['response_records']), 2)
        self.assertTrue(first['completeness']['usage_complete'])
        self.assertEqual(first['failures']['exit_codes'], [7])
        self.assertEqual(first['tool_calls'][0]['tool_call_id'], 'outer-call')
        self.assertIsNone(first['command_results'][0]['call_id'])
        self.assertIsNone(first['usage']['cost'])
        attributes = ledger.observation_attributes(first)
        self.assertTrue(all(key.startswith('herdr.observed.') for key in attributes))
        self.assertFalse(any(key.startswith('gen_ai.usage') for key in attributes))

    def test_foreign_usage_is_rejected_and_completeness_is_false(self):
        with tempfile.TemporaryDirectory() as directory:
            path, rows = self.make_rollout(directory)
            rows.append({'type': 'token_usage_record', 'payload': {
                'thread_id': 'another-session', 'response_id': 'foreign', 'usage': {'input_tokens': 9000}}})
            path.write_text('\n'.join(json.dumps(row) for row in rows) + '\n')
            result = ledger.build_ledger(path, 'fixture-session')
            with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                ledger.build_ledger(path, 'another-session')
        self.assertEqual(result['usage']['authoritative_observed_sum']['input_tokens'], 30)
        self.assertFalse(result['completeness']['usage_complete'])


if __name__ == '__main__':
    unittest.main()
