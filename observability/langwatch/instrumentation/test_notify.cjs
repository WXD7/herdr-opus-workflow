'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { capture, snapshotSpan } = require('./notify.cjs');
const projectDir = path.resolve(__dirname, '../../..');

async function main() {
  const observation = { metadata: { ledger_sha256: 'a'.repeat(64), source_timestamp: '2026-09-27T00:00:00Z' } };
  const observedAttrs = [{ key: 'herdr.observed.input_tokens', value: { intValue: '30' } }];
  const snapshot1 = snapshotSpan('fixture', observation, '1'.repeat(32), observedAttrs);
  const snapshot2 = snapshotSpan('fixture', observation, '1'.repeat(32), observedAttrs);
  assert.deepEqual(snapshot1, snapshot2);
  assert.equal(snapshot1.name, 'Herdr observed session snapshot (no model)');
  assert.equal(snapshot1.attributes[0].value.stringValue, 'span');
  assert.equal(snapshot1.startTimeUnixNano, snapshot1.endTimeUnixNano);
  assert.ok(!snapshot1.attributes.some(x => x.key.startsWith('gen_ai.usage') || x.key.includes('cost')));
  assert.ok(!('parentSpanId' in snapshot1));
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'langwatch-notify-test-'));
  const sessions = path.join(temporary, 'sessions');
  fs.mkdirSync(sessions);
  const received = [];
  const server = http.createServer((request, response) => {
    let body = '';
    request.on('data', chunk => body += chunk);
    request.on('end', () => {
      received.push({ headers: request.headers, path: request.url, body: JSON.parse(body) });
      response.writeHead(200, { 'content-type': 'application/json' }); response.end('{}');
    });
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    fs.writeFileSync(path.join(temporary, 'endpoint'), `http://127.0.0.1:${server.address().port}`);
    fs.writeFileSync(path.join(temporary, 'ingest-key'), 'ik-lw-synthetic-test-only', { mode: 0o600 });
    const sessionId = 'synthetic-notify-fixture';
    const lines = [{ type: 'session_meta', payload: { id: sessionId, cwd: projectDir } }];
    for (let index = 1; index <= 4; index++) lines.push(
      { type: 'event_msg', payload: { type: 'task_started', trace_id: String(index).repeat(32), turn_id: `fixture-${index}`, started_at: 1 } },
      { type: 'response_item', payload: { type: 'message', role: 'user', content: [{ text: `fixture ${index}` }] } },
      { type: 'response_item', payload: { type: 'function_call', name: 'shell', call_id: `tool-${index}`, arguments: '{"command":"false"}' } },
      { type: 'response_item', payload: { type: 'function_call_output', call_id: `tool-${index}`, output: 'synthetic failure: exit 1' } },
      { type: 'event_msg', payload: { type: 'agent_message', phase: 'final_answer', message: `synthetic answer ${index}` } },
      { type: 'event_msg', payload: { type: 'task_complete' } },
    );
    fs.writeFileSync(path.join(sessions, `rollout-fixture-${sessionId}.jsonl`), lines.map(JSON.stringify).join('\n'));
    const statuses = [];
    const context = { sessions_root: sessions, run_id: 'langwatch-synthetic-validation', role: 'synthetic',
      launch_id: 'fixture-launch', resource_attributes: { 'herdr.run_id': 'langwatch-synthetic-validation',
        'herdr.role': 'synthetic', 'herdr.synthetic': 'true' } };
    await capture(context, JSON.stringify({ 'thread-id': sessionId }), temporary, value => statuses.push(value));
    assert.equal(received.length, 1);
    assert.equal(received[0].headers.authorization, 'Bearer ik-lw-synthetic-test-only');
    assert.equal(received[0].path, '/api/otel/v1/traces');
    const spans = received[0].body.resourceSpans[0].scopeSpans[0].spans;
    assert.equal(spans.length, 3);
    assert.equal(spans[0].traceId, '2'.repeat(32));
    assert.match(JSON.stringify(spans), /synthetic failure/);
    assert.equal(spans[0].attributes.find(x => x.key === 'session.id').value.stringValue, sessionId);
    assert.equal(statuses[0].status, 'posted');
    assert.equal(statuses[0].posted_turns, 3);
    lines[0].payload.cwd = os.tmpdir();
    fs.writeFileSync(path.join(sessions, `rollout-fixture-${sessionId}.jsonl`), lines.map(JSON.stringify).join('\n'));
    await capture(context, JSON.stringify({ 'thread-id': sessionId }), temporary, value => statuses.push(value));
    assert.equal(statuses[1].status, 'out_of_scope');
    assert.equal(received.length, 1);
    console.log('PASS: official parser, last-three-turn behavior, local auth header, failure content, metadata, scope rejection');
  } finally {
    await new Promise(resolve => server.close(resolve));
    fs.rmSync(temporary, { recursive: true, force: true });
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
