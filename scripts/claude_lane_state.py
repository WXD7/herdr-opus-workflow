#!/usr/bin/env python3
"""Probe one Claude lane from its own transcript tail (herdr-dispatch §6a probe contract).

Reads only <= 2 MiB from the end of one exact transcript file. The main thread's
assistant stop_reason == "end_turn" is the only completion signal; missing files, bad
JSON and identity mismatches report probe "unavailable", never a finished turn.
Business completion (.dispatch/DONE) is verified separately by the shared supervise flow.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import re
import stat

ROOT = Path(__file__).resolve().parents[1]
TAIL_BYTES = 2 * 1024 * 1024
SESSION_RE = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
INTERRUPT_PREFIX = '[Request interrupted by user'


def default_transcript(home, checkout, session):
    project = re.sub(r'[^A-Za-z0-9]', '-', str(checkout))
    return Path(home) / '.claude' / 'projects' / project / (session + '.jsonl')


def read_tail(path, tail_bytes):
    with open(path, 'rb') as fh:
        st = os.fstat(fh.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise OSError('not a regular file')
        start = max(0, st.st_size - tail_bytes)
        fh.seek(start)
        data = fh.read(st.st_size - start)
    info = {'file_bytes': st.st_size, 'read_bytes': len(data), 'complete': start == 0,
            'first_line_dropped': False, 'last_line_incomplete': False}
    if start:
        # The window starts inside a line; that first line is truncated.
        cut = data.find(b'\n')
        data = data[cut + 1:] if cut >= 0 else b''
        info['first_line_dropped'] = True
    lines = data.split(b'\n')
    last = lines.pop()
    if last.strip():
        # An unterminated line is usable only if it is already a whole JSON object.
        try:
            complete = isinstance(json.loads(last), dict)
        except ValueError:
            complete = False
        if complete:
            lines.append(last)
        else:
            info['last_line_incomplete'] = True
    return st, lines, info


def texts(content):
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [b.get('text') or '' for b in content if isinstance(b, dict) and b.get('type') == 'text']
    return []


def classify(rec):
    """Return (turn_state, last_event, out_of_room) for one main-thread user/assistant record."""
    msg = rec.get('message') if isinstance(rec.get('message'), dict) else {}
    content = msg.get('content')
    if rec['type'] == 'user':
        if any(t.startswith(INTERRUPT_PREFIX) for t in texts(content)):
            return 'unknown', 'user:interrupt', False
        if isinstance(content, list) and any(isinstance(b, dict) and b.get('type') == 'tool_result' for b in content):
            return 'working', 'user:tool_result', False
        return 'working', 'user:prompt', False
    reason = msg.get('stop_reason')
    if rec.get('isApiErrorMessage') or msg.get('model') == '<synthetic>':
        too_long = any('prompt is too long' in t.lower() for t in texts(content))
        return 'unknown', 'assistant:api_error', too_long
    if reason == 'end_turn':
        return 'complete', 'assistant:end_turn', False
    if reason in (None, 'tool_use', 'pause_turn'):
        return 'working', 'assistant:' + (reason or 'in_progress'), False
    return 'unknown', 'assistant:' + str(reason), reason == 'model_context_window_exceeded'


def within(cwd, checkout, cache):
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        return False
    if cwd not in cache:
        path = Path(cwd).resolve()
        cache[cwd] = path == checkout or checkout in path.parents
    return cache[cwd]


def probe(session, checkout, transcript=None, home=None, tail_bytes=TAIL_BYTES):
    result = {'session': session, 'turn_state': 'unknown', 'used_pct': None, 'compactions': None,
              'compactions_partial': None, 'mtime': None, 'probe': 'unavailable', 'reason': None,
              'last_event': None, 'last_event_at': None, 'goal_status': None, 'out_of_room': None,
              'source_path': None, 'source': 'override' if transcript else 'default', 'tail': None}

    def unavailable(reason):
        result['reason'] = reason
        return result

    session = (session or '').lower()
    if not SESSION_RE.fullmatch(session):
        return unavailable('session must be a canonical UUID')
    result['session'] = session
    if not checkout or not os.path.isabs(checkout):
        return unavailable('checkout must be an absolute path')
    checkout = Path(checkout).resolve()
    if checkout != ROOT and ROOT not in checkout.parents:
        return unavailable('checkout is outside this project: ' + str(checkout))
    if transcript:
        if not os.path.isabs(transcript) or Path(transcript).name != session + '.jsonl':
            return unavailable('transcript must be an absolute path named <session>.jsonl')
        path = Path(transcript)
    else:
        path = default_transcript(home or Path.home(), checkout, session)
    result['source_path'] = str(path)

    try:
        st, lines, info = read_tail(path, tail_bytes)
    except FileNotFoundError:
        return unavailable('transcript not found')
    except OSError as exc:
        return unavailable('transcript unreadable: ' + (exc.strerror or str(exc)))
    result['mtime'] = datetime.datetime.fromtimestamp(st.st_mtime, datetime.timezone.utc).isoformat()
    info.update(records=0, sidechain_records=0)
    result['tail'] = info

    state, event, event_at, out_of_room = 'unknown', None, None, False
    compactions, identity_seen, cwd_cache = 0, False, {}
    for number, raw in enumerate(lines, 1):
        if not raw.strip():
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            rec = None
        if not isinstance(rec, dict):
            return unavailable('bad JSON at tail line %d' % number)
        info['records'] += 1
        kind = rec.get('type')
        main_message = kind in ('user', 'assistant') and rec.get('isSidechain') is not True
        if 'sessionId' in rec or main_message:
            if rec.get('sessionId') != session:
                return unavailable('sessionId mismatch at tail line %d: %r' % (number, rec.get('sessionId')))
            identity_seen = True
        if rec.get('isSidechain') is True:
            # Subagent records never speak for the lane's main thread.
            info['sidechain_records'] += 1
            continue
        if ('cwd' in rec or main_message) and not within(rec.get('cwd'), checkout, cwd_cache):
            return unavailable('cwd mismatch at tail line %d: %r' % (number, rec.get('cwd')))
        if kind == 'system' and rec.get('subtype') == 'compact_boundary':
            compactions += 1
        elif main_message:
            state, event, out_of_room = classify(rec)
            event_at = rec.get('timestamp')
        # Everything else (system notices, snapshots, titles, attachments, progress) is
        # metadata and must not overwrite the last turn event.

    if not identity_seen:
        return unavailable('no record in the tail confirms sessionId')
    if state == 'complete' and info['last_line_incomplete']:
        # A write is still in flight after end_turn; its content is not known yet.
        state = 'unknown'
    result.update(probe='ok', turn_state=state, compactions=compactions,
                  compactions_partial=not info['complete'], last_event=event,
                  last_event_at=event_at, out_of_room=out_of_room)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--session', required=True, help='Claude session UUID of the lane')
    parser.add_argument('--checkout', required=True, help='absolute lane checkout inside this project')
    parser.add_argument('--transcript', help='absolute path to <session>.jsonl (overrides the default path)')
    args = parser.parse_args(argv)
    try:
        result = probe(args.session, args.checkout, args.transcript)
    except Exception as exc:  # Report, never crash into a default.
        result = {'session': args.session, 'turn_state': 'unknown', 'probe': 'unavailable',
                  'reason': 'internal error: %s' % type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
