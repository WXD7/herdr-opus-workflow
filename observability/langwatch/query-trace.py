#!/usr/bin/env python3
"""Read a bounded trace summary for a human or Astra. No model call or polling."""
import importlib.util
import json
from pathlib import Path
import re
import sys
import urllib.parse

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('local_bootstrap', ROOT / 'bootstrap-local.py')
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


def inspect_trace(trace_id):
    if not re.fullmatch(r'[0-9a-f]{32}', trace_id):
        raise ValueError('Expected a 32-character trace ID')
    project = json.loads((ROOT / 'project.json').read_text())
    endpoint = project['endpoint']
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.username or parsed.password:
        raise ValueError('Only this local deployment is supported')
    key = (ROOT / 'private/read-key').read_text().strip()
    trace = bootstrap.Client(endpoint).request('GET', '/api/traces/' + trace_id,
        token=key, project_id=project['project']['id'])
    unique_spans = {span['span_id']: span for span in trace.get('spans', [])}
    snapshots = []
    for span in unique_spans.values():
        params = span.get('params') or {}
        observed = params.get('herdr', {}).get('observed')
        if span.get('name') == 'Herdr observed session snapshot (no model)' and observed:
            snapshots.append({'span_id': span['span_id'], **observed})
    snapshots.sort(key=lambda row: (row.get('source_timestamp', ''), row['span_id']))
    return {'trace_id': trace_id, 'platform_url': trace.get('platformUrl'),
        'metadata': {key: trace.get('metadata', {}).get(key) for key in ['run_id', 'role', 'session_id']},
        'unique_span_count': len(unique_spans),
        'platform_metrics_unverified_for_decisions': trace.get('metrics'),
        'observed_session_snapshot': snapshots[-1] if snapshots else None,
        'observed_snapshot_count': len(snapshots),
        'note': 'Select the latest snapshot per session; never sum snapshots or add them to native usage. Codex cached input is a subset of input. Usage completeness describes recorded evidence, not business acceptance. Missing observation is unknown, not zero. Platform cost is not a subscription bill.'}


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python3 query-trace.py TRACE_ID')
    try:
        print(json.dumps(inspect_trace(sys.argv[1]), ensure_ascii=False, indent=2))
    except (ValueError, OSError, bootstrap.ProvisionError) as error:
        raise SystemExit(str(error))
