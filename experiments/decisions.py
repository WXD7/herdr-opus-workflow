"""Bounded lane -> parent -> lane mail within the existing dispatcher. No LLM/approval calls.

This is coordination evidence, not a security boundary against a malicious local process.
Native Agent children use their harness result channel to their immediate caller; they are
not separate registered Herdr lanes and are never guessed from names or recent transcripts.
"""
import os
from pathlib import Path
import time

from .config import digest, identifier, text
from .lifecycle import processes, registrations, same_process, current_session
from .store import Store

TERMINAL = {'completed', 'failed', 'cancelled'}
REQUEST_FIELDS = ('id', 'key', 'experiment_id', 'attempt_id', 'context_hash', 'child_session',
                  'parent_session', 'kind', 'question', 'created_at', 'expires_at')


class Mailbox:
    def __init__(self, attempt):
        self.attempt = attempt
        self.store = Store(Path(attempt['directory']) / 'decisions')

    def validate(self, value):
        a = self.attempt
        if (value['attempt_id'] != a['id'] or value['context_hash'] != a['context_hash']
                or value['parent_session'] != a['supervisor_session']
                or value['request_hash'] != digest({k: value[k] for k in REQUEST_FIELDS})):
            raise ValueError('Decision identity/content changed; refusing delivery')
        if value.get('answer') and (value.get('answered_by') != value['parent_session'] or
                value.get('answer_hash') != digest([value['request_hash'], value['answer'], value['parent_session']])):
            raise ValueError('Parent answer identity/content changed')
        return value

    def read(self, rid):
        path = self.store.path('requests', rid)
        if path.is_symlink() or path.stat().st_size > 32000:
            raise ValueError('Invalid decision record')
        return self.validate(self.store.read('requests', rid))

    def all(self):
        return [self.read(p.stem) for p in sorted((self.store.root / 'requests').glob('*.json'))]

    def actor(self, table=None, pid=None):
        table = processes() if table is None else table
        pid = os.getpid() if pid is None else pid
        records = {r['pid']: r for r in registrations(self.attempt)}
        seen = set()
        while pid in table and pid not in seen:
            seen.add(pid)
            record = records.get(pid)
            if record and same_process(record, table[pid]):
                current_session(record)
                return record  # Exact wrapper process ancestry, never a caller-supplied UUID.
            pid = table[pid]['ppid']
        raise ValueError('Decision commands must run under a registered live group session')

    def _open(self, value):
        if (value['expires_at'] <= time.time() or self.attempt.get('result_status')
                or self.attempt.get('stop_attempted') or self.attempt.get('stop_requested')):
            raise ValueError('Decision expired or group closing; do not resume it')
        if self.attempt['status'] in TERMINAL:
            raise ValueError('Decision group ended')

    def request(self, actor, eid, key, question, kind, expires_at):
        identifier(key); question = text(question, 'question', 4000)
        if actor['role'] != 'lane' or actor.get('parent_session') != self.attempt['supervisor_session']:
            raise ValueError('Only a registered lane may request its recorded parent')
        if kind not in ('routine', 'authority'):
            raise ValueError('Use routine for in-scope decisions; authority for permission/scope/budget')
        rid = 'dec-' + digest([self.attempt['id'], actor['session'], key])[:24]
        with self.store.lock():
            if self.store.path('requests', rid).exists():
                previous = self.read(rid)
                if previous['question'] != question or previous['kind'] != kind:
                    raise ValueError('Decision key already used for different content')
                return previous
            for existing in self.all():
                if existing['child_session'] == actor['session'] and not existing.get('consumed_at') and existing['expires_at'] > time.time():
                    raise ValueError('One unresolved decision per lane; reuse its key, do not repeat')
            value = {'id': rid, 'key': key, 'experiment_id': eid, 'attempt_id': self.attempt['id'],
                     'context_hash': self.attempt['context_hash'], 'child_session': actor['session'],
                     'parent_session': actor['parent_session'], 'kind': kind, 'question': question,
                     'created_at': time.time(), 'expires_at': expires_at}
            self._open(value)
            value['request_hash'] = digest(value)
            self.store.save('requests', rid, value)
            return value

    def inbox(self, actor):
        values = []
        with self.store.lock():
            for value in self.all():
                if actor['session'] not in (value['parent_session'], value['child_session']):
                    continue
                if actor['session'] == value['parent_session'] and not value.get('parent_received_at'):
                    value['parent_received_at'] = time.time()
                    self.store.save('requests', value['id'], value)
                values.append(value)
        return values

    def answer(self, actor, rid, expected, answer):
        answer = text(answer, 'answer', 4000)
        with self.store.lock():
            value = self.read(rid); self._open(value)
            if actor['session'] != value['parent_session'] or expected != value['request_hash']:
                raise ValueError('Only the exact parent may answer the unchanged request')
            if value['kind'] != 'routine':
                raise ValueError('Authority request needs the authorized owner/native interface; not auto-approved')
            if not value.get('parent_received_at'):
                raise ValueError('Read the parent inbox before deciding')
            if value.get('answer'):
                if value['answer'] != answer:
                    raise ValueError('Decision already answered; no replacement')
                return value
            value.update(answer=answer, answered_at=time.time(), answered_by=actor['session'])
            value['answer_hash'] = digest([value['request_hash'], answer, actor['session']])
            self.store.save('requests', rid, value)
            return value

    def consume(self, actor, rid, expected):
        with self.store.lock():
            value = self.read(rid); self._open(value)
            if actor['session'] != value['child_session'] or expected != value['request_hash']:
                raise ValueError('Only the exact child may consume the unchanged request')
            if (not value.get('answer') or value['answer_hash'] !=
                    digest([value['request_hash'], value['answer'], value['parent_session']])):
                raise ValueError('Missing or changed parent answer')
            if value.get('consumed_at'):
                return {'id': rid, 'already_consumed': True}
            value['consumed_at'] = time.time()
            self.store.save('requests', rid, value)
            return value

    def delivery(self, rid, direction, status, **detail):
        with self.store.lock():
            value = self.read(rid)
            value.setdefault('delivery', {})[direction] = {'status': status, 'at': time.time(), **detail}
            self.store.save('requests', rid, value)


def command(args):
    from .snapshots import load_context
    context = load_context()
    if context['workflow'].get('decision_transport') != 'parent-v1':
        raise ValueError('These frozen four documents do not enable parent decision routing')
    item = Store(context['state_root']).read('experiments', context['experiment_id'])
    attempt = next(a for a in item['attempts'] if a['id'] == context['attempt_id'])
    if attempt['context_hash'] != digest(context):
        raise ValueError('Frozen decision context mismatch')
    mail = Mailbox(attempt); actor = mail.actor()
    if args.action == 'inbox': return mail.inbox(actor)
    if args.action == 'request':
        if not args.key or not args.text: raise ValueError('request needs --key and --text')
        deadline = attempt['started_at'] + item['config']['timeout_minutes'] * 60
        return mail.request(actor, item['id'], args.key, args.text, args.kind, min(time.time()+900, deadline))
    if not args.id or not args.request_hash: raise ValueError('answer/consume needs --id and --request-hash')
    if args.action == 'answer': return mail.answer(actor, args.id, args.request_hash, args.text)
    return mail.consume(actor, args.id, args.request_hash)


def route(engine, item, attempt, adapter):
    """One bounded pass in the existing local dispatcher. No repeated uncertain sends."""
    if attempt['workflow'].get('decision_transport') != 'parent-v1': return False
    mail = Mailbox(attempt)
    records = {r['session']: r for r in registrations(attempt)}
    changed = False
    for value in mail.all():
        rid = value['id']
        if value.get('consumed_at'): continue
        if value['expires_at'] <= time.time():
            engine.store.event('attempt.blocked', item['id'], attempt['id'], {'decision_id': rid, 'reason': 'decision_expired'})
            continue
        if value['kind'] == 'authority':
            engine.store.event('attempt.blocked', item['id'], attempt['id'], {'decision_id': rid, 'reason': 'authority_required'})
        direction = 'child' if value.get('answer') else 'parent'
        if direction == 'parent' and value.get('parent_received_at'): continue
        if value.get('delivery', {}).get(direction): continue
        sid = value[direction + '_session']
        record = records.get(sid)
        if not record:
            engine.store.event('attempt.blocked', item['id'], attempt['id'], {'decision_id': rid, 'reason': 'exact_recipient_missing'})
            continue
        try:
            target = adapter.decision_target(record, attempt)
            if target is None: continue  # Recipient busy. No model call, input or retry counter consumed.
            def intent(): mail.delivery(rid, direction, 'attempted')
            prefix = '/herdr-dispatch:dispatch-codex --resume\n' if direction == 'parent' else ''
            prompt = (prefix + '当前组 ' + attempt['id'] + ' 的决策 ' + rid +
                      ' 已就绪；仅按原四文档 driver §6g，用冻结 scripts/experiment.py decision inbox 读取。' +
                      ('按已授权范围作答，权限/新范围问题交给授权者。' if direction == 'parent' else
                       '先对该 ID 和 request_hash 执行 decision consume；成功后按答复恢复原任务，不重复执行已完成操作。'))
            adapter.prompt(target, prompt, before_submit=intent)
            mail.delivery(rid, direction, 'submitted')
            engine.store.event('attempt.decision_delivery', item['id'], attempt['id'], {'decision_id': rid, 'recipient': sid, 'direction': direction})
        except Exception as error:
            latest = mail.read(rid).get('delivery', {}).get(direction, {})
            mail.delivery(rid, direction, 'uncertain' if latest else 'not_submitted', error=str(error)[:800])
            engine.store.event('attempt.blocked', item['id'], attempt['id'], {'decision_id': rid, 'reason': str(error)[:800]})
        changed = True
        break  # At most one decision wake per group per dispatcher tick.
    attempt['decisions'] = mail.all()
    return changed or any(not v.get('consumed_at') for v in attempt['decisions'])
