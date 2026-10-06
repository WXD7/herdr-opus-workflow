"""Read native children only beneath exact, already verified Claude session paths."""
from pathlib import Path
from observability.collect_run import load_jsonl, parse_claude, claude_observed, sums, CLAUDE_KEYS


def descendants(data, max_files=500, max_bytes=50_000_000):
    threads, unknowns, seen, total = [], [], set(), 0
    roots = ([data['claude']] if data.get('claude') else []) + [
        x for x in data.get('claude_threads', []) if x.get('status') == 'resolved']
    for root in roots:
        seen.update(v['message_id'] for v in root.get('usage_index', []))
        source = root.get('source') or {}
        path = Path(source.get('path', ''))
        if not path.is_file():
            unknowns.append('Verified parent source path unavailable'); continue
        parent_session = path.stem
        folder = path.with_suffix('') / 'subagents'
        if not folder.is_dir():
            continue
        for child in sorted(folder.rglob('agent-*.jsonl')):
            if len(threads) >= max_files or total + child.stat().st_size > max_bytes:
                unknowns.append('Bounded descendant evidence limit reached'); break
            if not child.resolve().is_relative_to(folder.resolve()):
                unknowns.append('Rejected descendant symlink outside exact parent'); continue
            rows, meta = load_jsonl(child)
            total += meta['bytes']
            rows = [(n, row) for n, row in rows if isinstance(row, dict)]
            identities = {row['sessionId'] for _, row in rows if isinstance(row.get('sessionId'), str)}
            if identities != {parent_session}:
                unknowns.append('Rejected native file with missing/foreign session identity: ' + child.name); continue
            parsed = parse_claude(rows)
            ids = {v['message_id'] for v in parsed['usage_index']}
            overlap = sorted(ids & seen); seen.update(ids)
            parent_ids = {row.get('parentAgentId') for _, row in rows if isinstance(row.get('parentAgentId'), str)}
            threads.append({'agent_id': child.stem.removeprefix('agent-'), 'root_session': parent_session,
                'parent_agent_id': next(iter(parent_ids)) if len(parent_ids) == 1 else None,
                'source': str(child), 'observed': claude_observed(rows),
                'usage': parsed['usage']['deduplicated_message_sum'],
                'usage_complete': parsed['coverage']['usage_complete'] and not meta['parse_error_lines'],
                'duplicate_message_ids': overlap})
    complete = bool(threads) and not unknowns and all(t['usage_complete'] and not t['duplicate_message_ids'] for t in threads)
    return {'threads': threads, 'unknowns': unknowns, 'bytes_read': total,
        'observed_usage_sum': sums((t['usage'] for t in threads), CLAUDE_KEYS) if complete else None,
        'coverage': 'Exact parent session subagent directories only; missing children and unrecorded recursion edges remain unknown.',
        'complete': None, 'llm_calls': 0}
