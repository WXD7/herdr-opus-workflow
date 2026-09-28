#!/usr/bin/env python3
"""Read an explicitly identified Herdr run; collect bounded, local metadata only.

No LLM, network, subprocesses, credentials, global writes, or broad transcript scan.
state.agent_kind 'claude' reads exact Claude transcripts for the supervisor and each explicit
lane and never opens the Codex database; otherwise lanes are resolved as Codex threads.
Usage: python3 observability/collect_run.py --state PATH --out DIRECTORY
"""
import argparse
import collections
import datetime
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import sqlite3

RUN_ID_RULE = pathlib.Path(__file__).resolve().parent / 'langwatch' / 'instrumentation' / 'run_id.py'
spec = importlib.util.spec_from_file_location('herdr_run_id', RUN_ID_RULE)
run_ids = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_ids)

VERSION = 1
CODEX_KEYS = ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
              'output_tokens', 'reasoning_output_tokens', 'total_tokens')
CLAUDE_KEYS = ('input_tokens', 'cache_read_input_tokens',
               'cache_creation_input_tokens', 'output_tokens')
SESSION_PATTERN = r'[0-9a-fA-F-]{36}'
EFFORT_FIELDS = ('effort', 'effortLevel', 'perTurnEffort')
COMMON_UNKNOWNS = ('Actual bill and account-level usage are not determined.',
                   'Exact business-stage token attribution and exact waste rate are unknown.')
RECORDED_UNKNOWN = 'State operation counts and audit test results are historical records, not fresh reruns.'
CODEX_UNKNOWNS = COMMON_UNKNOWNS + (
    'Tool wrapper calls and nested static syntax sites are separate units; do not add them.',
    'Goal tokens are an independent runtime metric; do not add to raw or cached tokens.',
    'Codex cached input is a subset of input, and reasoning output a subset of output.',
    RECORDED_UNKNOWN)
CLAUDE_UNKNOWNS = COMMON_UNKNOWNS + (
    'Native Claude sub-agent transcripts are not mapped; descendant usage is not covered, so billing is not complete.',
    'Claude usage counts each message.id once per session; nested iterations and cache TTL subtotals are never added.',
    'Claude effort is listed only where a transcript row records it; null means not observed, not a default.',
    RECORDED_UNKNOWN)
LEGACY_LABEL_UNKNOWN = ('State telemetry_run_id is a legacy run-<32 hex> label. Processes launched before the '
                        'run-label fix keep sending it, and LangWatch 3.17 can store it redacted as run-[CRYPTO]; '
                        'launches through the fixed entry points send telemetry_run_id_mapping.normalized_candidate. '
                        'No platform label was read and nothing was backfilled.')


def sums(items, keys):
    items = list(items)
    return {key: sum(x.get(key, 0) for x in items) for key in keys}


def load_jsonl(path):
    raw = pathlib.Path(path).read_bytes()
    rows, errors = [], []
    for line, text in enumerate(raw.splitlines(), 1):
        try:
            rows.append((line, json.loads(text)))
        except (ValueError, UnicodeDecodeError):
            errors.append(line)
    return rows, {'path': str(path), 'bytes': len(raw),
                  'sha256': hashlib.sha256(raw).hexdigest(),
                  'line_count': len(raw.splitlines()), 'parse_error_lines': errors}


def parse_claude(rows):
    messages, tools, conflicts = {}, {}, []
    observed_ids, missing_id_lines = set(), []
    assistant_rows = 0
    for line, row in rows:
        if row.get('type') != 'assistant':
            continue
        assistant_rows += 1
        msg = row.get('message', {})
        mid, usage = msg.get('id'), msg.get('usage')
        if mid:
            observed_ids.add(mid)
        else:
            missing_id_lines.append(line)
        if mid and isinstance(usage, dict):
            if mid in messages and messages[mid]['usage'] != usage:
                conflicts.append({'message_id': mid, 'line': line})
            messages[mid] = {'usage': usage, 'line': line,
                             'timestamp': row.get('timestamp')}
        content = msg.get('content', [])
        if not isinstance(content, list):
            continue
        for block in content:
            if block.get('type') != 'tool_use' or not block.get('id'):
                continue
            inp = block.get('input', {})
            command = inp.get('command', '')
            tags = []
            if re.search(r'herdr agent send-keys [^;\n]+ y(?:;|$)', command):
                tags.append('approval_granted_command')
            if re.search(r'herdr agent (?:list|get|read)\b', command):
                tags.append('contains_supervisor_inspection')
            if 'sleep' in command and ('for ' in command or 'while ' in command):
                tags.append('contains_polling_syntax')
            if re.search(r'\(cd .*?&& python3 -m unittest ', command):
                tags.append('supervisor_test_execution_command')
            tools[block['id']] = {'line': line, 'timestamp': row.get('timestamp'),
                                  'tool_call_id': block['id'], 'name': block.get('name'),
                                  'tags': tags}
    return {
        'usage': {'deduplicated_message_sum': sums((x['usage'] for x in messages.values()), CLAUDE_KEYS),
                  'method': 'One usage per message.id; latest observed if conflicting; nested iterations/cache TTL subtotals not added.',
                  'conflicts': conflicts},
        'counts': {'assistant_rows': assistant_rows, 'unique_message_ids': len(messages),
                   'tool_calls': len(tools), 'tools_by_name': dict(collections.Counter(x['name'] for x in tools.values()))},
        'event_index': list(tools.values()),
        'usage_index': [{'message_id': k, 'line': v['line'], 'timestamp': v['timestamp']} for k, v in messages.items()],
        'coverage': {'scope': 'Entire exact supervisor transcript including setup before run creation; no causal stage attribution.',
                     'usage_complete': bool(messages) and not conflicts and not missing_id_lines and observed_ids == set(messages),
                     'message_ids_without_usage': sorted(observed_ids - set(messages)),
                     'missing_message_id_lines': missing_id_lines,
                     'billing_amount': None}}


def parse_codex(rows, thread_id):
    records, tokens, tools, conflicts, rejected = {}, [], {}, [], []
    meta = next((row.get('payload', {}) for _, row in rows if row.get('type') == 'session_meta'), {})
    for line, row in rows:
        payload = row.get('payload', {})
        if row.get('type') == 'token_usage_record':
            rid = payload.get('response_id')
            if payload.get('thread_id') != thread_id or not rid:
                rejected.append(line)
                continue
            usage = payload.get('usage', {})
            if rid in records and records[rid]['usage'] != usage:
                conflicts.append(line)
            records[rid] = {'line': line, 'timestamp': row.get('timestamp'), 'usage': usage,
                            'root_turn_id': payload.get('root_turn_id')}
        elif row.get('type') == 'event_msg' and payload.get('type') == 'token_count' and payload.get('info'):
            total = payload['info'].get('total_token_usage')
            if isinstance(total, dict):
                tokens.append((line, total))
        if row.get('type') != 'response_item' or payload.get('type') not in ('function_call', 'custom_tool_call'):
            continue
        cid = payload.get('call_id') or payload.get('id')
        if not cid:
            continue
        text = payload.get('input', '')
        nested = dict(collections.Counter(re.findall(r'tools\.([A-Za-z0-9_]+)\s*\(', text)))
        tags = []
        if re.search(r'cmd\s*:\s*[\"\']python3 -m unittest ', text):
            tags.append('worker_unittest_command')
        if re.search(r'cmd\s*:\s*[\"\']herdr agent prompt ', text):
            tags.append('notify_back_command')
        tools[cid] = {'line': line, 'timestamp': row.get('timestamp'), 'tool_call_id': cid,
                      'name': payload.get('name'), 'tags': tags,
                      'nested_static_syntax_counts': nested}
    delta, previous, resets, duplicates = {k: 0 for k in CODEX_KEYS}, None, [], []
    for line, cumulative in tokens:
        if previous == cumulative:
            duplicates.append(line)
        reset = previous is not None and cumulative.get('total_tokens', 0) < previous.get('total_tokens', 0)
        if reset:
            resets.append(line)
        for key in CODEX_KEYS:
            delta[key] += cumulative.get(key, 0) - (0 if previous is None or reset else previous.get(key, 0))
        previous = cumulative
    response_sum = sums((x['usage'] for x in records.values()), CODEX_KEYS) if records else None
    usage_matches = response_sum == delta if response_sum is not None and tokens else None
    return {'thread_id': thread_id, 'parent_thread_id': meta.get('parent_thread_id'),
            'usage': {'response_sum': response_sum, 'latest_cumulative': previous,
                      'cumulative_delta_sum': delta if tokens else None,
                      'goal_tokens': None,
                      'method': 'Prefer response_id-deduplicated usage records. Cumulative delta assumes zero before first event and starts a new epoch after a decreasing total. Never sum repeated last_token_usage.'},
            'counts': {'usage_records': sum(1 for _, x in rows if x.get('type') == 'token_usage_record'),
                       'unique_response_ids': len(records), 'token_count_events': len(tokens),
                       'duplicate_cumulative_events': len(duplicates), 'tool_calls': len(tools),
                       'tools_by_name': dict(collections.Counter(x['name'] for x in tools.values()))},
            'coverage': {'identity_matches': meta.get('id') == thread_id,
                         'response_sum_matches_cumulative_delta': usage_matches,
                         'conflicting_response_lines': conflicts, 'rejected_usage_lines': rejected,
                         'reset_lines': resets, 'duplicate_cumulative_lines': duplicates,
                         'first_cumulative_baseline': 'zero_assumed_full_file; truncated source may overcount',
                         'usage_records_complete': bool(records) and usage_matches is True and not conflicts and not rejected,
                         'billing_amount': None},
            'event_index': list(tools.values()),
            'usage_index': [{'response_id': k, 'line': v['line'], 'timestamp': v['timestamp'],
                             'root_turn_id': v['root_turn_id']} for k, v in records.items()]}


def telemetry_run_mapping(recorded):
    """The state's label kept as recorded, beside the label the fixed entry points derive from it."""
    if not isinstance(recorded, str) or not recorded:
        return None
    candidate = run_ids.normalize_run_id(recorded)
    return {'state_value': recorded, 'state_value_format': run_ids.label_format(recorded),
            'normalized_candidate': candidate, 'candidate_differs': candidate != recorded,
            'candidate_basis': 'derived locally by ' + str(RUN_ID_RULE) + '; not read from LangWatch',
            'platform_values_observed': None, 'history_backfilled': False}


def allowed_identity(meta, expected_id, parent, allowed_cwds):
    return (meta.get('id') == expected_id
            and (parent is None or meta.get('parent_thread_id') == parent)
            and str(pathlib.Path(meta.get('cwd', '')).resolve()) in allowed_cwds)


def linked_threads(connection, roots, limit=64):
    """Follow actual edges only. No title/content/cwd search or unrelated row reads."""
    queue, seen, edges = list(roots), set(roots), []
    for parent in queue:
        for row in connection.execute('SELECT parent_thread_id, child_thread_id FROM thread_spawn_edges WHERE parent_thread_id=?', (parent,)):
            child = row[1]
            edges.append({'parent_thread_id': parent, 'child_thread_id': child})
            if child not in seen:
                if len(seen) >= limit:
                    raise ValueError('Descendant limit exceeded; inspect scope explicitly.')
                seen.add(child)
                queue.append(child)
    return queue, edges


def claude_project_dir(directory):
    """Claude Code project folder name: every character except ASCII letters/digits becomes '-'."""
    return re.sub(r'[^A-Za-z0-9]', '-', str(directory))


def claude_first_identity(path):
    """Stream only to the first row carrying sessionId and cwd, so a foreign file is declined unread."""
    with pathlib.Path(path).open('rb') as handle:
        for line, text in enumerate(handle, 1):
            try:
                row = json.loads(text)
            except ValueError:
                continue
            if isinstance(row, dict) and 'sessionId' in row and 'cwd' in row:
                return [(line, row)]
    return []


def claude_identity_problem(rows, session, directory):
    """None only if rows show the expected main-thread session starting in, and staying under, directory."""
    resolved, started, main = {}, False, False
    for line, row in rows:
        if 'sessionId' in row and row['sessionId'] != session:
            return 'sessionId mismatch at line %d' % line
        if 'cwd' not in row:
            continue
        if not isinstance(row['cwd'], str) or not os.path.isabs(row['cwd']):
            return 'invalid cwd at line %d' % line
        if row['cwd'] not in resolved:
            resolved[row['cwd']] = pathlib.Path(row['cwd']).resolve()
        cwd = resolved[row['cwd']]
        if not started and cwd != directory:
            return 'session did not start in the expected directory (line %d)' % line
        started = True
        if cwd != directory and directory not in cwd.parents:
            return 'cwd outside the expected directory at line %d' % line
        main = main or ('sessionId' in row and row.get('isSidechain') is not True)
    return None if main else 'no main-thread row carries the expected sessionId and cwd'


def claude_observed(rows):
    """Model and effort values literally recorded in rows; null when none were observed."""
    models, efforts = set(), set()
    for _, row in rows:
        message = row.get('message') if isinstance(row.get('message'), dict) else {}
        if row.get('type') == 'assistant' and isinstance(message.get('model'), str):
            models.add(message['model'])
        efforts.update(x[k] for x in (row, message) for k in EFFORT_FIELDS if isinstance(x.get(k), str))
    return {'models': sorted(models) or None, 'efforts': sorted(efforts) or None}


def resolve_claude(claude_home, session, directory, explicit=None):
    """Locate one exact transcript, verify identity, parse main-thread rows only; never search."""
    record = {'status': 'rejected', 'reason': None, 'paths': [], 'bytes_read': 0, 'parsed': None}
    if not isinstance(session, str) or not re.fullmatch(SESSION_PATTERN, session):
        record['reason'] = 'invalid session id'
    elif not isinstance(directory, str) or not os.path.isabs(directory):
        record['reason'] = 'expected directory is not an absolute path'
    elif explicit is not None and not (isinstance(explicit, str) and os.path.isabs(explicit)
                                       and explicit.endswith('.jsonl')):
        record['reason'] = 'transcript_path must be an absolute .jsonl path'
    if record['reason']:
        return record
    if explicit is not None:
        candidates = [pathlib.Path(explicit)]
    else:
        # Logical and symlink-resolved spellings of the same checkout; exact file names only.
        names = {claude_project_dir(os.path.normpath(directory)),
                 claude_project_dir(pathlib.Path(directory).resolve())}
        candidates = [pathlib.Path(claude_home) / 'projects' / name / (session + '.jsonl') for name in sorted(names)]
    record['paths'] = [str(x) for x in candidates]
    found = sorted({x.resolve() for x in candidates if x.is_file()})
    if len(found) != 1:
        record.update(status='missing' if not found else 'rejected',
                      reason='exact transcript not found' if not found else 'ambiguous exact transcript paths')
        return record
    expected = pathlib.Path(directory).resolve()
    problem = claude_identity_problem(claude_first_identity(found[0]), session, expected)
    if not problem:
        rows, source = load_jsonl(found[0])
        record['bytes_read'] = source['bytes']
        source['non_object_lines'] = [line for line, row in rows if not isinstance(row, dict)]
        rows = [(line, row) for line, row in rows if isinstance(row, dict)]
        problem = claude_identity_problem(rows, session, expected)
    if problem:
        record['reason'] = 'identity mismatch: ' + problem
        return record
    main = [(line, row) for line, row in rows if row.get('isSidechain') is not True]
    parsed = parse_claude(main)
    source['mtime'] = datetime.datetime.fromtimestamp(found[0].stat().st_mtime, datetime.timezone.utc).isoformat()
    parsed.update(source=source, observed=claude_observed(main))
    if source['parse_error_lines'] or source['non_object_lines']:
        parsed['coverage']['usage_complete'] = False
    parsed['coverage'].update(identity_verified=True, sidechain_rows_excluded=len(rows) - len(main),
                              descendants='not_covered', billable_usage_complete=None)
    record.update(status='resolved', parsed=parsed)
    return record


def collect_claude(state, result, claude_home, roots):
    """Exact supervisor (cwd=repo) plus explicit lanes (cwd=checkout); never opens the Codex database."""
    supervisor = state['orchestrator_session']
    records = [resolve_claude(claude_home, supervisor, state.get('repo'))]
    if records[0]['parsed']:
        result['claude'] = records[0]['parsed']
        result['claude']['coverage']['scope'] = ('Main thread of the exact supervisor transcript (sidechain rows excluded), '
                                                 'including setup before run creation; no causal stage attribution.')
    else:
        result['unknowns'].append('Supervisor transcript %s (%s); supervisor usage unknown.'
                                  % (records[0]['status'], records[0]['reason']))
    unmapped = 0
    for index, lane in enumerate(state['lanes']):
        if not lane.get('session'):
            unmapped += 1
            result['unknowns'].append('State lane %d names no session; not collected.' % index)
            continue
        record = resolve_claude(claude_home, lane['session'], lane.get('checkout'), lane.get('transcript_path'))
        records.append(record)
        # parent is a native spawn edge; herdr lanes are root sessions, and sub-agents are not mapped yet.
        thread = {'session': lane['session'], 'lane': index, 'checkout': lane.get('checkout'),
                  'parent': None, 'status': record['status']}
        if record['parsed']:
            thread.update(record['parsed'])
            thread['coverage']['scope'] = 'Main thread of the exact lane transcript (sidechain rows excluded); no causal stage attribution.'
        else:
            thread.update(source=None, usage=None, counts=None, observed={'models': None, 'efforts': None},
                          coverage={'reason': record['reason'], 'expected_paths': record['paths'],
                                    'identity_verified': False, 'usage_complete': None,
                                    'descendants': 'not_covered', 'billable_usage_complete': None,
                                    'billing_amount': None})
            result['unknowns'].append('Claude lane %d transcript %s (%s); usage unknown.'
                                      % (index, record['status'], record['reason']))
        result['claude_threads'].append(thread)
    parsed = ([(supervisor, result['claude'])] if result['claude'] else []) + \
        [(x['session'], x) for x in result['claude_threads'] if x['status'] == 'resolved']
    owners = collections.defaultdict(list)
    for session, item in parsed:
        for event in item['usage_index']:
            owners[event['message_id']].append(session)
    overlaps = {k: v for k, v in owners.items() if len(v) > 1}
    for _, item in parsed:
        item['coverage']['cross_session_duplicate_message_ids'] = sorted(
            {x['message_id'] for x in item['usage_index']} & set(overlaps))
    if overlaps:
        result['unknowns'].append('%d Claude message.id values repeat across sessions; the cross-session total is unknown, '
                                  'do not add per-session sums.' % len(overlaps))
    complete = (not unmapped and not overlaps and all(x['parsed'] for x in records)
                and all(item['coverage']['usage_complete'] for _, item in parsed))
    result['coverage'] = {'requested_roots': roots,
                          'resolved_thread_count': sum(x['status'] == 'resolved' for x in result['claude_threads']),
                          'linked_thread_count': None, 'cross_thread_duplicate_response_ids': {},
                          'cross_session_duplicate_message_ids': overlaps,
                          'unrelated_transcripts_read': sum(bool(x['bytes_read']) and not x['parsed'] for x in records),
                          'llm_calls': 0,
                          'scope': 'Exact supervisor plus explicit Claude lanes, main threads only; native sub-agent descendants not mapped; Codex database not opened.',
                          'descendants': 'not_covered', 'codex_database_opened': False,
                          'observed_models': sorted({v for _, item in parsed for v in item['observed']['models'] or ()}) or None,
                          'observed_efforts': sorted({v for _, item in parsed for v in item['observed']['efforts'] or ()}) or None,
                          'claude_main_thread_usage_sum': sums((item['usage']['deduplicated_message_sum'] for _, item in parsed), CLAUDE_KEYS) if complete else None,
                          'claude_main_thread_usage_sum_rule': 'Null unless the supervisor and every state lane resolved with complete usage and no message.id repeats across sessions; excludes descendants; not a bill.',
                          'billable_usage_complete': None}
    return sum(x['bytes_read'] for x in records)


def collect_codex(state, result, codex_home, claude_home, roots):
    supervisor = state['orchestrator_session']
    # Only exact basename resolution; never open unrelated transcripts.
    paths = list((pathlib.Path(claude_home) / 'projects').glob('*/' + supervisor + '.jsonl'))
    if len(paths) == 1:
        rows, source = load_jsonl(paths[0]); result['claude'] = parse_claude(rows)
        result['claude']['source'] = source
    else:
        result['unknowns'].append('Exact supervisor transcript missing or ambiguous.')
    database = pathlib.Path(codex_home) / 'state_5.sqlite'
    uri = database.resolve().as_uri() + '?mode=ro'
    with sqlite3.connect(uri, uri=True) as connection:
        connection.execute('PRAGMA query_only=ON')
        schema = {r[0]: r[1] for r in connection.execute("SELECT name, sql FROM sqlite_master WHERE name IN ('threads','thread_spawn_edges')")}
        if not all(k in schema for k in ('threads', 'thread_spawn_edges')):
            raise ValueError('Unsupported database schema; required identity/edge tables missing.')
        # Inspect columns before exact-ID reads; never SELECT * on a conversation table.
        columns = {r[1] for r in connection.execute('PRAGMA table_info(threads)')}
        if not {'id', 'rollout_path', 'tokens_used'} <= columns:
            raise ValueError('Unsupported thread columns.')
        ids, edges = linked_threads(connection, roots)
        result['spawn_edges'] = edges
        parents = {x['child_thread_id']: x['parent_thread_id'] for x in edges}
        lane_by_id = {x['session']: x for x in state['lanes']}
        allowed_cwds = {str(pathlib.Path(x['checkout']).resolve()) for x in state['lanes'] if x.get('checkout')}
        for tid in ids:
            row = connection.execute('SELECT rollout_path, tokens_used FROM threads WHERE id=?', (tid,)).fetchone()
            if not row or not pathlib.Path(row[0]).is_file():
                result['unknowns'].append('Missing exact rollout for ' + tid); continue
            path = pathlib.Path(row[0])
            # Read only session metadata first; decline a mismatching file before full content.
            with path.open() as handle:
                first = json.loads(handle.readline())
            if first.get('type') != 'session_meta' or not allowed_identity(first.get('payload', {}), tid, parents.get(tid), allowed_cwds):
                result['unknowns'].append('Rollout identity/cwd/parent mismatch; skipped ' + tid); continue
            rows, source = load_jsonl(path)
            parsed = parse_codex(rows, tid); parsed['source'] = source
            parsed['usage']['database_tokens_used'] = row[1]
            parsed['usage']['goal_tokens'] = lane_by_id.get(tid, {}).get('agent_final', {}).get('goal_tokens')
            result['codex_threads'].append(parsed)
    response_owners = collections.defaultdict(list)
    for thread in result['codex_threads']:
        for event in thread['usage_index']:
            response_owners[event['response_id']].append(thread['thread_id'])
    overlaps = {k: v for k, v in response_owners.items() if len(v) > 1}
    result['coverage'] = {'requested_roots': roots, 'resolved_thread_count': len(result['codex_threads']),
                          'linked_thread_count': len(ids), 'cross_thread_duplicate_response_ids': overlaps,
                          'unrelated_transcripts_read': 0, 'llm_calls': 0,
                          'scope': 'Exact supervisor plus explicit lanes and edge-linked descendants; no installation-agent transcript.',
                          'billable_usage_complete': None}
    return sum(x['source']['bytes'] for x in result['codex_threads']) + (result['claude']['source']['bytes'] if result['claude'] else 0)


def collect(state_path, codex_home, claude_home):
    import time
    start = time.monotonic()
    state_path = pathlib.Path(state_path).resolve()
    state = json.loads(state_path.read_text())
    roots = [x['session'] for x in state.get('lanes', []) if x.get('session')]
    if not roots or not state.get('orchestrator_session'):
        raise ValueError('State must explicitly identify lane sessions and orchestrator_session.')
    kind = state.get('agent_kind') or 'codex'
    if kind not in ('claude', 'codex'):
        raise ValueError('Unsupported agent_kind; expected claude or codex.')
    result = {'version': VERSION, 'run_id': state.get('run_id'), 'executor_kind': kind,
              'telemetry_run_id': state.get('telemetry_run_id'),
              'telemetry_run_id_mapping': telemetry_run_mapping(state.get('telemetry_run_id')),
              'generated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'sources': {'state': str(state_path)}, 'claude': None, 'claude_threads': [], 'codex_threads': [],
              'spawn_edges': [], 'coverage': {}, 'operations': {},
              'unknowns': list(CLAUDE_UNKNOWNS if kind == 'claude' else CODEX_UNKNOWNS)}
    if (result['telemetry_run_id_mapping'] or {}).get('candidate_differs'):
        result['unknowns'].append(LEGACY_LABEL_UNKNOWN)
    if not re.fullmatch(SESSION_PATTERN, state['orchestrator_session']):
        raise ValueError('Invalid supervisor UUID.')
    if kind == 'claude':
        bytes_read = collect_claude(state, result, claude_home, roots)
    else:
        bytes_read = collect_codex(state, result, codex_home, claude_home, roots)
    lanes = state['lanes']
    result['operations'] = {'recorded_lane_sweeps': sum(len(x.get('sweeps', [])) for x in lanes),
        'recorded_sweep_times': sorted({s['at'] for x in lanes for s in x.get('sweeps', []) if s.get('at')}),
        'recorded_approvals': sum(len(x.get('approvals', [])) for x in lanes),
        'recorded_notifications_received': sum(bool(x.get('notify_back', {}).get('received_by_orchestrator')) for x in lanes),
        'recorded_commits': sum(x.get('commit_count', 0) for x in lanes),
        'recorded_lane_tests': [{'session': x.get('session'), 'expected_tests': x.get('expected_tests'),
                                 'phase': x.get('phase'), 'head': x.get('head_sha'),
                                 'impl_file': x.get('impl_file')} for x in lanes],
        'actual_poll_iterations': None, 'actual_timer_firing': (state.get('loop') or {}).get('observed_firing')}
    result['collection'] = {'wall_ms': round((time.monotonic() - start) * 1000, 3),
                             'bytes_read': bytes_read, 'llm_calls': 0, 'network_calls': 0}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--codex-home', default=str(pathlib.Path.home() / '.codex'))
    parser.add_argument('--claude-home', default=str(pathlib.Path.home() / '.claude'))
    args = parser.parse_args()
    result = collect(args.state, args.codex_home, args.claude_home)
    out = pathlib.Path(args.out).resolve(); out.mkdir(parents=True, exist_ok=True)
    target = out / 'run-summary.json'
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'output': str(target), 'run_id': result['run_id'], 'executor_kind': result['executor_kind'],
                      'threads': result['coverage']['resolved_thread_count'],
                      'wall_ms': result['collection']['wall_ms'], 'llm_calls': 0}, ensure_ascii=False))


if __name__ == '__main__':
    main()
