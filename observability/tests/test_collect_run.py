import importlib.util
import pathlib
import sqlite3
import tempfile
import unittest

MODULE = pathlib.Path(__file__).parents[1] / 'collect_run.py'
spec = importlib.util.spec_from_file_location('collect_run', MODULE)
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


def usage(n):
    return {'input_tokens': n, 'cached_input_tokens': 0, 'cache_write_input_tokens': 0,
            'output_tokens': 0, 'reasoning_output_tokens': 0, 'total_tokens': n}


def token(n):
    return {'type': 'event_msg', 'payload': {'type': 'token_count',
            'info': {'total_token_usage': usage(n), 'last_token_usage': usage(n)}}}


def record(n, rid, tid='lane'):
    return {'type': 'token_usage_record', 'payload': {'thread_id': tid,
            'response_id': rid, 'usage': usage(n)}}


class CollectorTests(unittest.TestCase):
    def test_claude_duplicate_message_usage_and_nested_subtotals(self):
        message = {'type': 'assistant', 'message': {'id': 'm1', 'usage': {
            'input_tokens': 2, 'cache_read_input_tokens': 7,
            'cache_creation_input_tokens': 3, 'output_tokens': 4,
            'iterations': [{'input_tokens': 2}],
            'cache_creation': {'ephemeral_1h_input_tokens': 3}},
            'content': [{'type': 'tool_use', 'id': 'c1', 'name': 'Bash', 'input': {}}]}}
        result = collector.parse_claude([(1, message), (2, message)])
        self.assertEqual(result['usage']['deduplicated_message_sum']['input_tokens'], 2)
        self.assertEqual(result['usage']['deduplicated_message_sum']['cache_creation_input_tokens'], 3)
        self.assertEqual(result['counts']['tool_calls'], 1)
        self.assertEqual(result['counts']['unique_message_ids'], 1)

    def test_codex_repeated_cumulative_not_added_twice(self):
        result = collector.parse_codex(list(enumerate([
            record(10, 'r1'), token(10), token(10), record(7, 'r2'), token(17),
            record(7, 'r2')], 1)), 'lane')
        self.assertEqual(result['usage']['response_sum']['total_tokens'], 17)
        self.assertEqual(result['usage']['cumulative_delta_sum']['total_tokens'], 17)
        self.assertEqual(result['counts']['duplicate_cumulative_events'], 1)
        self.assertTrue(result['coverage']['usage_records_complete'])

    def test_codex_reset_creates_new_epoch(self):
        result = collector.parse_codex(list(enumerate([
            record(10, 'r1'), token(10), record(4, 'r2'), token(4)], 1)), 'lane')
        self.assertEqual(result['usage']['latest_cumulative']['total_tokens'], 4)
        self.assertEqual(result['usage']['cumulative_delta_sum']['total_tokens'], 14)
        self.assertEqual(result['coverage']['reset_lines'], [4])
        self.assertTrue(result['coverage']['response_sum_matches_cumulative_delta'])

    def test_codex_missing_records_and_wrong_thread_are_explicit(self):
        result = collector.parse_codex([(1, record(10, 'r1')), (2, token(20)),
                                        (3, record(10, 'other', 'unrelated'))], 'lane')
        self.assertFalse(result['coverage']['usage_records_complete'])
        self.assertEqual(result['coverage']['rejected_usage_lines'], [3])
        self.assertEqual(result['usage']['response_sum']['total_tokens'], 10)
        missing = collector.parse_codex([(1, token(20))], 'lane')
        self.assertIsNone(missing['usage']['response_sum'])
        self.assertFalse(missing['coverage']['usage_records_complete'])

    def test_follow_edges_only_and_cycle_is_bounded(self):
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE thread_spawn_edges(parent_thread_id TEXT, child_thread_id TEXT)')
            db.executemany('INSERT INTO thread_spawn_edges VALUES (?,?)',
                           [('lane', 'child'), ('child', 'lane'), ('unrelated', 'private')])
            ids, edges = collector.linked_threads(db, ['lane'])
            self.assertEqual(ids, ['lane', 'child'])
            self.assertNotIn('private', ids)
            with self.assertRaises(ValueError):
                collector.linked_threads(db, ['lane'], limit=1)

    def test_identity_parent_and_checkout_must_match(self):
        with tempfile.TemporaryDirectory() as directory:
            good = {'id': 'child', 'parent_thread_id': 'lane', 'cwd': directory}
            allowed = {str(pathlib.Path(directory).resolve())}
            self.assertTrue(collector.allowed_identity(good, 'child', 'lane', allowed))
            self.assertFalse(collector.allowed_identity(good, 'child', 'other', allowed))
            self.assertFalse(collector.allowed_identity(good, 'wrong-id', 'lane', allowed))
            self.assertFalse(collector.allowed_identity(good, 'child', 'lane', {'/unrelated'}))


if __name__ == '__main__':
    unittest.main()
