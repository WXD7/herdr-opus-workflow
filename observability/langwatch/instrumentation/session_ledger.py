#!/usr/bin/env python3.13
"""An exact-session observation ledger; reuses collect_run's usage deduplication.

No model call, network request, broad scan, cost estimate, or native span mutation.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
COLLECTOR = PROJECT / 'observability/collect_run.py'
spec = importlib.util.spec_from_file_location('existing_run_collector', COLLECTOR)
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


def build_ledger(rollout_path, session_id, project_root=PROJECT):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', session_id):
        raise ValueError('Invalid session id')
    rollout_path = Path(rollout_path).resolve()
    # Reject the wrong session/project before reading the complete transcript.
    with rollout_path.open() as source:
        first = json.loads(source.readline())
    meta = first.get('payload', {})
    if first.get('type') != 'session_meta' or meta.get('id') != session_id:
        raise ValueError('Rollout identity mismatch')
    if not meta.get('cwd') or not Path(meta['cwd']).resolve().is_relative_to(Path(project_root).resolve()):
        raise ValueError('Rollout outside project')
    rows, provenance = collector.load_jsonl(rollout_path)
    parsed = collector.parse_codex(rows, session_id)
    by_line = dict(rows)
    records = [{**entry, 'usage': by_line[entry['line']]['payload'].get('usage', {})}
               for entry in parsed['usage_index']]

    # This selects explicit command result records only. It is not a second
    # conversation parser and never guesses an exit code from source code/text.
    commands, duplicate_command_lines, conflicting_command_lines = {}, [], []
    for line, row in rows:
        payload = row.get('payload', {})
        if row.get('type') != 'event_msg' or payload.get('type') != 'item_completed':
            continue
        if payload.get('thread_id') != session_id:
            continue
        item = payload.get('item', {})
        if item.get('type') not in ('CommandExecution', 'command_execution') or not item.get('id'):
            continue
        exit_code = item.get('exit_code')
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            exit_code = None
        result = {'item_id': item['id'], 'call_id': item.get('call_id'),
                  'turn_id': payload.get('turn_id'), 'line': line,
                  'timestamp': row.get('timestamp'), 'exit_code': exit_code,
                  'status': item.get('status'), 'source': 'event_msg.item_completed.CommandExecution',
                  'outer_call_link': 'explicit' if item.get('call_id') else 'not_emitted'}
        if item['id'] in commands:
            previous = commands[item['id']]
            duplicate_command_lines.append(line)
            if (previous['exit_code'], previous['status']) != (result['exit_code'], result['status']):
                conflicting_command_lines.append(line)
        commands[item['id']] = result
    results = list(commands.values())
    failures = [item for item in results if item['exit_code'] is not None and item['exit_code'] != 0]
    known = [item for item in results if item['exit_code'] is not None]
    complete = (parsed['coverage']['identity_matches'] and parsed['coverage']['usage_records_complete']
                and not provenance['parse_error_lines'])
    ledger = {'schema_version': 1, 'session_id': session_id, 'scope': 'entire_exact_session_snapshot',
              'source': provenance,
              'source_timestamp': rows[-1][1].get('timestamp') if rows else None,
              'parser': {'path': str(COLLECTOR), 'function': 'parse_codex',
                         'sha256': hashlib.sha256(COLLECTOR.read_bytes()).hexdigest()},
              'usage': {**parsed['usage'], 'authoritative_observed_sum': parsed['usage']['response_sum'],
                        'cost': None, 'cost_status': 'unknown; subscription bill not inferred'},
              'response_records': records, 'tool_calls': parsed['event_index'],
              'command_results': results,
              'failures': {'nonzero_exit_count': len(failures),
                           'exit_codes': [item['exit_code'] for item in failures],
                           'command_item_ids': [item['item_id'] for item in failures]},
              'completeness': {**parsed['coverage'], 'usage_complete': complete,
                               'usage_status': 'complete_at_observed_snapshot' if complete else 'incomplete_or_ambiguous',
                               'known_command_exit_count': len(known), 'unknown_command_exit_count': len(results) - len(known),
                               'duplicate_command_result_lines': duplicate_command_lines,
                               'conflicting_command_result_lines': conflicting_command_lines,
                               'all_nested_shell_calls_visible': None,
                               'tool_call_to_command_mapping_complete': all(item['call_id'] for item in results) if results else None},
              'semantics': {'cached_input_is_subset_of_input': True,
                            'reasoning_output_is_subset_of_output': True,
                            'counts_are_session_snapshots_not_additive_span_metrics': True,
                            'native_platform_usage_modified': False,
                            'native_rpc_success_does_not_imply_shell_exit_zero': True}}
    canonical = json.dumps(ledger, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    ledger['ledger_sha256'] = hashlib.sha256(canonical).hexdigest()
    return ledger


def observation_attributes(ledger):
    values = {'herdr.observed.scope': ledger['scope'],
              'herdr.observed.source': 'codex_rollout_token_usage_record+CommandExecution',
              'herdr.observed.ledger_sha256': ledger['ledger_sha256'],
              'herdr.observed.usage_complete': ledger['completeness']['usage_complete'],
              'herdr.observed.response_count': len(ledger['response_records']),
              'herdr.observed.nonzero_exit_count': ledger['failures']['nonzero_exit_count'],
              'herdr.observed.exit_codes': json.dumps(ledger['failures']['exit_codes']),
              'herdr.observed.known_command_exit_count': ledger['completeness']['known_command_exit_count'],
              'herdr.observed.unknown_command_exit_count': ledger['completeness']['unknown_command_exit_count'],
              'herdr.observed.cost_known': False}
    if ledger['source_timestamp'] is not None:
        values['herdr.observed.source_timestamp'] = ledger['source_timestamp']
    usage = ledger['usage']['authoritative_observed_sum']
    if usage is not None:
        for key, value in usage.items():
            values['herdr.observed.' + key] = value
    return values


def save_ledger(ledger, output):
    output = Path(output)
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.ledger-', dir=output.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(ledger, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rollout', required=True)
    parser.add_argument('--session-id', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    ledger = build_ledger(args.rollout, args.session_id)
    save_ledger(ledger, args.output)
    print(json.dumps({'attributes': observation_attributes(ledger),
                      'metadata': {'scope': ledger['scope'], 'ledger_sha256': ledger['ledger_sha256'],
                                   'source_timestamp': ledger['source_timestamp'],
                                   'usage': ledger['usage']['authoritative_observed_sum'],
                                   'usage_complete': ledger['completeness']['usage_complete'],
                                   'cost': None, 'failure_exit_codes': ledger['failures']['exit_codes'],
                                   'source': ledger['source']['path'], 'source_sha256': ledger['source']['sha256']}}))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        # Source content and argument values never enter diagnostics.
        print('Session ledger failed: ' + type(error).__name__, file=__import__('sys').stderr)
        raise SystemExit(1)
