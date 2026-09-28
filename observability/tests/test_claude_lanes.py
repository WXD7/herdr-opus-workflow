import contextlib
import importlib.util
import io
import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


collector = load('collect_run')
packet_builder = load('make_review_packet')

SUPERVISOR = '00000000-0000-4000-8000-000000000000'
LANE_A = 'aaaaaaaa-0000-4000-8000-00000000000a'
LANE_B = 'bbbbbbbb-0000-4000-8000-00000000000b'
LANE_C = 'cccccccc-0000-4000-8000-00000000000c'
OTHER = 'dddddddd-0000-4000-8000-00000000000d'
MODEL = 'claude-opus-5-5'


def usage(n):
    # Nested iterations / cache TTL breakdowns repeat top-level counters and must never be added.
    return {'input_tokens': n, 'cache_read_input_tokens': 10 * n, 'cache_creation_input_tokens': 2,
            'output_tokens': 1, 'iterations': [{'input_tokens': n, 'output_tokens': 1}],
            'cache_creation': {'ephemeral_5m_input_tokens': 2, 'ephemeral_1h_input_tokens': 0}}


def user(session, cwd, sidechain=False):
    return {'type': 'user', 'sessionId': session, 'cwd': str(cwd), 'isSidechain': sidechain,
            'message': {'role': 'user', 'content': 'go'}}


def assistant(session, cwd, mid, n, sidechain=False, model=MODEL):
    return {'type': 'assistant', 'sessionId': session, 'cwd': str(cwd), 'isSidechain': sidechain,
            'message': {'id': mid, 'model': model, 'usage': usage(n), 'content': [
                {'type': 'tool_use', 'id': 'tool-' + mid, 'name': 'Bash', 'input': {'command': 'true'}}]}}


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows))
    return path


class ClaudeLaneTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = pathlib.Path(temp.name).resolve()
        self.home = self.root / 'claude-home'
        self.repo = self.root / 'repo'
        # Non-ASCII, space and dot exercise the exact project-folder encoding.
        self.a, self.b = self.root / '开发 lanes' / 'wt.a', self.root / '开发 lanes' / 'wt_b'
        for directory in (self.repo, self.a, self.b):
            directory.mkdir(parents=True)

    def transcript(self, directory, session):
        return self.home / 'projects' / collector.claude_project_dir(directory) / (session + '.jsonl')

    def state(self, lanes):
        path = self.root / 'state.json'
        path.write_text(json.dumps({'run_id': 'run-claude', 'telemetry_run_id': 'telemetry-fixture',
                                    'agent_kind': 'claude', 'repo': str(self.repo),
                                    'orchestrator_session': SUPERVISOR, 'lanes': lanes}, ensure_ascii=False))
        return path

    def collect(self, lanes):
        # Missing Codex home plus a failing connect proves Claude mode never opens the database.
        with mock.patch.object(collector.sqlite3, 'connect', side_effect=AssertionError('Codex DB opened')):
            return collector.collect(self.state(lanes), self.root / 'no-codex-home', self.home)

    def standard(self):
        write(self.transcript(self.repo, SUPERVISOR),
              [user(SUPERVISOR, self.repo), assistant(SUPERVISOR, self.repo, 'msg_s1', 5)])
        write(self.transcript(self.a, LANE_A), [user(LANE_A, self.a), assistant(LANE_A, self.a, 'msg_a1', 3),
                                                assistant(LANE_A, self.a / 'src', 'msg_a2', 4)])
        write(self.transcript(self.b, LANE_B), [user(LANE_B, self.b), assistant(LANE_B, self.b, 'msg_b1', 7)])
        return [{'session': LANE_A, 'checkout': str(self.a)}, {'session': LANE_B, 'checkout': str(self.b)}]

    def build_packet(self, summary, out):
        with mock.patch.object(sys, 'argv', ['make_review_packet.py', str(summary), '--out', str(out)]), \
                contextlib.redirect_stdout(io.StringIO()) as printed:
            packet_builder.main()
        return json.loads(out.read_text()), json.loads(printed.getvalue())

    def test_project_folder_encoding_replaces_each_non_ascii_alnum(self):
        self.assertEqual(collector.claude_project_dir('/Users/x/ChatGPT/开发/herdr_wt.a'),
                         '-Users-x-ChatGPT----herdr-wt-a')

    def test_launching_run_without_timer_collects_with_unknown_firing(self):
        path = self.state(self.standard())
        state = json.loads(path.read_text())
        state.update(status='dispatching', loop=None)
        path.write_text(json.dumps(state))
        result = collector.collect(path, self.root / 'no-codex-home', self.home)
        self.assertIsNone(result['operations']['actual_timer_firing'])
        self.assertEqual(len(result['claude_threads']), 2)

    def test_incomplete_json_cannot_claim_complete_usage(self):
        lanes = self.standard()
        with self.transcript(self.a, LANE_A).open('a') as handle:
            handle.write('{"type":"assistant",')
        result = self.collect(lanes)
        self.assertFalse(result['claude_threads'][0]['coverage']['usage_complete'])
        self.assertIsNone(result['coverage']['claude_main_thread_usage_sum'])

    def test_two_claude_lanes_collect_without_codex_database(self):
        lanes = self.standard()
        decoy = write(self.transcript(self.a, OTHER), [user(OTHER, self.a)])
        decoy.chmod(0)  # any read of an unrequested session in the same folder would fail
        read, real = [], collector.load_jsonl

        def spy(path):
            read.append(pathlib.Path(path).name)
            return real(path)
        with mock.patch.object(collector, 'load_jsonl', side_effect=spy):
            result = self.collect(lanes)
        self.assertEqual(sorted(read), sorted(x + '.jsonl' for x in (SUPERVISOR, LANE_A, LANE_B)))
        self.assertEqual(result['executor_kind'], 'claude')
        self.assertEqual((result['codex_threads'], result['spawn_edges']), ([], []))
        lane_a, lane_b = result['claude_threads']
        self.assertEqual([(x['session'], x['lane'], x['parent'], x['status']) for x in (lane_a, lane_b)],
                         [(LANE_A, 0, None, 'resolved'), (LANE_B, 1, None, 'resolved')])
        self.assertEqual(lane_a['usage']['deduplicated_message_sum'],
                         {'input_tokens': 7, 'cache_read_input_tokens': 70,
                          'cache_creation_input_tokens': 4, 'output_tokens': 2})
        self.assertEqual(lane_a['observed'], {'models': [MODEL], 'efforts': None})
        self.assertTrue(lane_a['coverage']['identity_verified'])
        self.assertEqual(lane_a['coverage']['descendants'], 'not_covered')
        self.assertIsNone(lane_a['coverage']['billable_usage_complete'])
        self.assertIn('mtime', lane_a['source'])
        self.assertEqual(result['claude']['usage']['deduplicated_message_sum']['input_tokens'], 5)
        coverage = result['coverage']
        self.assertEqual(coverage['resolved_thread_count'], 2)
        self.assertFalse(coverage['codex_database_opened'])
        self.assertIsNone(coverage['billable_usage_complete'])
        self.assertIsNone(coverage['linked_thread_count'])
        self.assertEqual(coverage['observed_models'], [MODEL])
        self.assertEqual(coverage['claude_main_thread_usage_sum'],
                         {'input_tokens': 19, 'cache_read_input_tokens': 190,
                          'cache_creation_input_tokens': 8, 'output_tokens': 4})
        sizes = sum(self.transcript(d, s).stat().st_size
                    for d, s in ((self.repo, SUPERVISOR), (self.a, LANE_A), (self.b, LANE_B)))
        self.assertEqual(result['collection']['bytes_read'], sizes)
        argv = ['collect_run.py', '--state', str(self.state(lanes)), '--out', str(self.root / 'out'),
                '--codex-home', str(self.root / 'no-codex-home'), '--claude-home', str(self.home)]
        with mock.patch.object(sys, 'argv', argv), \
                mock.patch.object(collector.sqlite3, 'connect', side_effect=AssertionError('Codex DB opened')), \
                contextlib.redirect_stdout(io.StringIO()) as printed:
            collector.main()
        self.assertEqual(json.loads(printed.getvalue())['threads'], 2)

    def test_rejects_cross_session_cwd_and_non_transcript_paths(self):
        write(self.transcript(self.repo, SUPERVISOR),
              [user(SUPERVISOR, self.a), assistant(SUPERVISOR, self.a, 'msg_s1', 5)])
        foreign_row = write(self.transcript(self.a, LANE_A),
                            [user(LANE_A, self.a), assistant(OTHER, self.a, 'msg_x', 9)])
        write(self.transcript(self.b, LANE_B), [user(LANE_B, self.a), assistant(LANE_B, self.a, 'msg_b1', 7)])
        wandered = write(self.transcript(self.b, LANE_C),
                         [user(LANE_C, self.b), assistant(LANE_C, self.root, 'msg_c1', 1)])
        other = write(self.root / 'elsewhere' / (OTHER + '.jsonl'),
                      [user(OTHER, self.a), assistant(OTHER, self.a, 'msg_o1', 1)])
        secret = self.root / '.credentials.json'
        secret.write_text('{"token": "never-read"}')
        secret.chmod(0)
        result = self.collect([
            {'session': LANE_A, 'checkout': str(self.a)},  # a later row belongs to another session
            {'session': LANE_B, 'checkout': str(self.b)},  # started in the other lane's checkout
            {'session': LANE_C, 'checkout': str(self.b)},  # later cwd leaves the checkout
            {'session': LANE_A, 'checkout': str(self.a), 'transcript_path': str(other)},
            {'session': LANE_B, 'checkout': str(self.b), 'transcript_path': str(secret)},
            {'session': '../' * 12, 'checkout': str(self.a)}])
        self.assertIsNone(result['claude'])
        threads = result['claude_threads']
        self.assertEqual([x['status'] for x in threads], ['rejected'] * 6)
        expected = ['sessionId mismatch at line 2', 'did not start', 'outside the expected directory',
                    'sessionId mismatch at line 1', 'absolute .jsonl', 'invalid session id']
        for thread, text in zip(threads, expected):
            self.assertIn(text, thread['coverage']['reason'])
            self.assertEqual((thread['usage'], thread['counts'], thread['source']), (None, None, None))
        self.assertEqual(result['coverage']['resolved_thread_count'], 0)
        self.assertIsNone(result['coverage']['claude_main_thread_usage_sum'])
        # Only the two files whose first identity row matched were read in full, then rejected.
        self.assertEqual(result['coverage']['unrelated_transcripts_read'], 2)
        self.assertEqual(result['collection']['bytes_read'], foreign_row.stat().st_size + wandered.stat().st_size)
        self.assertTrue(any(x.startswith('Supervisor transcript rejected') for x in result['unknowns']))

    def test_sidechain_rows_stay_out_of_main_thread_usage(self):
        write(self.transcript(self.repo, SUPERVISOR),
              [user(SUPERVISOR, self.repo), assistant(SUPERVISOR, self.repo, 'msg_s1', 5)])
        write(self.transcript(self.a, LANE_A), [
            user(LANE_A, self.a), assistant(LANE_A, self.a, 'msg_a1', 3), user(LANE_A, self.a, sidechain=True),
            assistant(LANE_A, self.a, 'msg_side', 100, sidechain=True, model='claude-haiku-4-5-20251001')])
        subagent = write(self.root / 'subagents' / 'agent-1.jsonl', [
            user(LANE_B, self.b, sidechain=True), assistant(LANE_B, self.b, 'msg_sub', 50, sidechain=True)])
        result = self.collect([{'session': LANE_A, 'checkout': str(self.a)},
                               {'session': LANE_B, 'checkout': str(self.b), 'transcript_path': str(subagent)}])
        lane, sidechain_only = result['claude_threads']
        self.assertEqual(lane['usage']['deduplicated_message_sum']['input_tokens'], 3)
        self.assertEqual(lane['counts']['unique_message_ids'], 1)
        self.assertEqual(lane['coverage']['sidechain_rows_excluded'], 2)
        self.assertEqual(lane['observed']['models'], [MODEL])
        self.assertEqual(lane['coverage']['descendants'], 'not_covered')
        self.assertEqual(sidechain_only['status'], 'rejected')
        self.assertIn('main-thread', sidechain_only['coverage']['reason'])
        self.assertEqual(result['coverage']['descendants'], 'not_covered')
        self.assertIn('descendant usage is not covered', '\n'.join(result['unknowns']))

    def test_missing_transcripts_stay_unknown_not_zero(self):
        lanes = self.standard() + [{'checkout': str(self.b)}]
        self.transcript(self.b, LANE_B).unlink()
        self.transcript(self.repo, SUPERVISOR).unlink()
        result = self.collect(lanes)
        self.assertIsNone(result['claude'])
        lane_a, lane_b = result['claude_threads']
        self.assertEqual((lane_a['status'], lane_b['status']), ('resolved', 'missing'))
        self.assertEqual((lane_b['usage'], lane_b['counts'], lane_b['source']), (None, None, None))
        self.assertIsNone(lane_b['coverage']['usage_complete'])
        self.assertEqual(lane_b['observed'], {'models': None, 'efforts': None})
        self.assertEqual(lane_b['coverage']['expected_paths'], [str(self.transcript(self.b, LANE_B))])
        self.assertEqual(result['coverage']['resolved_thread_count'], 1)
        self.assertIsNone(result['coverage']['claude_main_thread_usage_sum'])
        unknowns = '\n'.join(result['unknowns'])
        for text in ('Supervisor transcript missing', 'Claude lane 1 transcript missing', 'State lane 2 names no session'):
            self.assertIn(text, unknowns)

    def test_duplicate_message_ids_counted_once_and_cross_session_repeat_unknown(self):
        write(self.transcript(self.repo, SUPERVISOR),
              [user(SUPERVISOR, self.repo), assistant(SUPERVISOR, self.repo, 'msg_s1', 5)])
        partial = assistant(LANE_A, self.a, 'msg_a1', 3)
        del partial['message']['usage']
        write(self.transcript(self.a, LANE_A), [
            user(LANE_A, self.a), partial, assistant(LANE_A, self.a, 'msg_a1', 3),
            assistant(LANE_A, self.a, 'msg_a1', 3), assistant(LANE_A, self.a, 'msg_shared', 4)])
        write(self.transcript(self.b, LANE_B), [
            user(LANE_B, self.b), assistant(LANE_B, self.b, 'msg_shared', 4), assistant(LANE_B, self.b, 'msg_b1', 7)])
        result = self.collect([{'session': LANE_A, 'checkout': str(self.a)},
                               {'session': LANE_B, 'checkout': str(self.b)}])
        lane_a, lane_b = result['claude_threads']
        self.assertEqual(lane_a['usage']['deduplicated_message_sum'],
                         {'input_tokens': 7, 'cache_read_input_tokens': 70,
                          'cache_creation_input_tokens': 4, 'output_tokens': 2})
        self.assertEqual((lane_a['counts']['assistant_rows'], lane_a['counts']['unique_message_ids']), (4, 2))
        self.assertTrue(lane_a['coverage']['usage_complete'])
        self.assertEqual(result['coverage']['cross_session_duplicate_message_ids'], {'msg_shared': [LANE_A, LANE_B]})
        self.assertEqual(lane_b['coverage']['cross_session_duplicate_message_ids'], ['msg_shared'])
        self.assertEqual(result['claude']['coverage']['cross_session_duplicate_message_ids'], [])
        self.assertIsNone(result['coverage']['claude_main_thread_usage_sum'])
        self.assertIn('repeat across sessions', '\n'.join(result['unknowns']))

    def test_review_packet_carries_claude_lanes_and_executor_kind(self):
        summary = self.root / 'run-summary.json'
        summary.write_text(json.dumps(self.collect(self.standard()), ensure_ascii=False))
        packet, printed = self.build_packet(summary, self.root / 'packet.json')
        self.assertEqual(packet['executor_kind'], 'claude')
        self.assertEqual(packet['telemetry_run_id'], 'telemetry-fixture')
        self.assertEqual(packet['telemetry_run_id_mapping']['state_value'], 'telemetry-fixture')
        self.assertEqual([x['session'] for x in packet['claude_threads']], [LANE_A, LANE_B])
        lane = packet['claude_threads'][0]
        self.assertEqual((lane['lane'], lane['parent'], lane['status']), (0, None, 'resolved'))
        self.assertEqual(lane['observed']['models'], [MODEL])
        self.assertEqual(lane['coverage']['descendants'], 'not_covered')
        self.assertNotIn('event_index', lane)
        self.assertEqual(packet['claude']['usage']['deduplicated_message_sum']['input_tokens'], 5)
        self.assertEqual(printed['llm_calls'], 0)
        self.assertLessEqual(printed['packet_chars'], printed['limit_chars'])
        legacy = self.root / 'legacy-summary.json'
        legacy.write_text(json.dumps({'run_id': 'old', 'coverage': {}, 'claude': None, 'codex_threads': [],
                                      'spawn_edges': [], 'operations': {}, 'unknowns': []}))
        old_packet, _ = self.build_packet(legacy, self.root / 'legacy-packet.json')
        self.assertEqual((old_packet['executor_kind'], old_packet['claude_threads']), ('codex', []))
        self.assertIsNone(old_packet['telemetry_run_id'])
        self.assertIsNone(old_packet['telemetry_run_id_mapping'])

    def test_state_telemetry_label_is_kept_beside_an_explicit_mapping(self):
        lanes = self.standard()
        legacy, canonical = 'run-18b2d22b75ce44448e9b89e8735e951f', 'run-18b2d22b-75ce-4444-8e9b-89e8735e951f'
        for recorded, label_format, candidate in ((legacy, 'legacy_32_hex', canonical),
                                                  (legacy[:-1] + 'e', 'legacy_32_hex', canonical[:-1] + 'e'),
                                                  (canonical, 'canonical', canonical),
                                                  ('telemetry-fixture', 'custom', 'telemetry-fixture'), (None, None, None)):
            with self.subTest(recorded=recorded):
                path = self.state(lanes)
                state = json.loads(path.read_text())
                state['telemetry_run_id'] = recorded
                path.write_text(json.dumps(state, ensure_ascii=False))
                before = path.read_bytes()
                with mock.patch.object(collector.sqlite3, 'connect', side_effect=AssertionError('Codex DB opened')):
                    result = collector.collect(path, self.root / 'no-codex-home', self.home)
                self.assertEqual(path.read_bytes(), before)
                # The recorded fact is reported as recorded, never replaced by the candidate.
                self.assertEqual(result['telemetry_run_id'], recorded)
                self.assertEqual(collector.LEGACY_LABEL_UNKNOWN in result['unknowns'], label_format == 'legacy_32_hex')
                self.assertEqual(result['collection']['network_calls'], 0)
                mapping = result['telemetry_run_id_mapping']
                if recorded is None:
                    self.assertIsNone(mapping)
                    continue
                self.assertEqual((mapping['state_value'], mapping['state_value_format'], mapping['normalized_candidate'],
                                  mapping['candidate_differs']), (recorded, label_format, candidate, recorded != candidate))
                self.assertEqual((mapping['platform_values_observed'], mapping['history_backfilled']), (None, False))
                self.assertIn('not read from LangWatch', mapping['candidate_basis'])

    def test_per_turn_effort_is_observed_without_inferring_missing_values(self):
        rows = [(1, {'type': 'user', 'perTurnEffort': 'max'}),
                (2, {'type': 'assistant', 'message': {'model': MODEL}})]
        self.assertEqual(collector.claude_observed(rows), {'models': [MODEL], 'efforts': ['max']})
        self.assertIsNone(collector.claude_observed(rows[1:])['efforts'])

    def test_state_without_agent_kind_keeps_codex_path(self):
        rollout = write(self.root / 'rollout.jsonl', [
            {'type': 'session_meta', 'payload': {'id': LANE_A, 'cwd': str(self.a)}},
            {'type': 'token_usage_record', 'payload': {'thread_id': LANE_A, 'response_id': 'r1',
                                                       'usage': {'input_tokens': 10, 'total_tokens': 10}}},
            {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
                'total_token_usage': {'input_tokens': 10, 'total_tokens': 10}}}}])
        codex_home = self.root / 'codex-home'
        codex_home.mkdir()
        db = sqlite3.connect(str(codex_home / 'state_5.sqlite'))
        db.execute('CREATE TABLE threads(id TEXT, rollout_path TEXT, tokens_used INTEGER)')
        db.execute('CREATE TABLE thread_spawn_edges(parent_thread_id TEXT, child_thread_id TEXT)')
        db.execute('INSERT INTO threads VALUES (?,?,?)', (LANE_A, str(rollout), 10))
        db.commit()
        db.close()
        supervisor = write(self.home / 'projects' / 'legacy' / (SUPERVISOR + '.jsonl'),
                           [assistant(SUPERVISOR, self.repo, 'msg_s1', 5)])
        state = self.root / 'legacy-state.json'
        state.write_text(json.dumps({'run_id': 'legacy', 'orchestrator_session': SUPERVISOR,
                                     'lanes': [{'session': LANE_A, 'checkout': str(self.a)}]}))
        result = collector.collect(state, codex_home, self.home)
        self.assertEqual((result['executor_kind'], result['claude_threads']), ('codex', []))
        self.assertEqual([x['thread_id'] for x in result['codex_threads']], [LANE_A])
        self.assertEqual(result['codex_threads'][0]['usage']['response_sum']['total_tokens'], 10)
        self.assertEqual(result['coverage']['resolved_thread_count'], 1)
        self.assertEqual(result['claude']['usage']['deduplicated_message_sum']['input_tokens'], 5)
        self.assertEqual(result['unknowns'], list(collector.CODEX_UNKNOWNS))
        self.assertEqual(result['collection']['bytes_read'], rollout.stat().st_size + supervisor.stat().st_size)


if __name__ == '__main__':
    unittest.main()
