#!/usr/bin/env node
// Project adapter around the verbatim LangWatch 1.18.0 rollout parser/OTLP builder.
// No HOME/CODEX_HOME changes, CLI installation, global state, or historical import.
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { spawn, execFileSync } = require('node:child_process');
const { parseRollout, turnsToOtlp } = require('./official-rollout.cjs');
const privateDir = path.resolve(__dirname, '../private');
const projectDir = path.resolve(__dirname, '../../..');

function inside(candidate, root) {
  const relative = path.relative(root, candidate);
  return relative === '' || (!relative.startsWith('..' + path.sep) && relative !== '..' && !path.isAbsolute(relative));
}

function appendStatus(record) {
  fs.mkdirSync(privateDir, { recursive: true, mode: 0o700 });
  const target = path.join(privateDir, 'notifications.jsonl');
  const fd = fs.openSync(target, 'a', 0o600);
  try { fs.fchmodSync(fd, 0o600); fs.writeSync(fd, JSON.stringify({ at: new Date().toISOString(), ...record }) + '\n'); }
  finally { fs.closeSync(fd); }
}

function findRollout(directory, sessionId, depth = 0) {
  let entries;
  try { entries = fs.readdirSync(directory, { withFileTypes: true }); } catch { return null; }
  // Match upstream traversal: newest-named file first, at most three subdirectories.
  entries.sort((a, b) => b.name.localeCompare(a.name));
  for (const entry of entries) {
    const target = path.join(directory, entry.name);
    if (entry.isDirectory() && depth < 3) {
      const found = findRollout(target, sessionId, depth + 1);
      if (found) return found;
    } else if (entry.isFile() && entry.name.startsWith('rollout-') && entry.name.endsWith(`-${sessionId}.jsonl`)) {
      return target;
    }
  }
  return null;
}

function chainOriginal(argv, payload) {
  if (!Array.isArray(argv) || !argv.length || !argv.every(item => typeof item === 'string')) return;
  if (argv.includes(__filename)) return;
  try {
    const child = spawn(argv[0], [...argv.slice(1), payload], { detached: true, stdio: 'ignore', env: process.env });
    child.on('error', () => {});
    child.unref();
  } catch {}
}

function snapshotSpan(sessionId, observation, traceId, attributes) {
  const ledgerHash = observation.metadata.ledger_sha256;
  const sourceMs = Date.parse(observation.metadata.source_timestamp);
  if (!ledgerHash || !Number.isFinite(sourceMs)) return null;
  const spanId = createHash('sha256').update(`herdr.observed.session_snapshot:${sessionId}:${ledgerHash}`)
    .digest('hex').slice(0, 16);
  const instant = (BigInt(sourceMs) * 1000000n).toString();
  return { traceId, spanId, name: 'Herdr observed session snapshot (no model)', kind: 1,
    startTimeUnixNano: instant, endTimeUnixNano: instant,
    // Sibling observation in the known trace: no invented causal parent or model usage.
    attributes: [{ key: 'langwatch.span.type', value: { stringValue: 'span' } },
      { key: 'herdr.observed.compatibility', value: { stringValue: 'LangWatch 3.17 existing-span attributes are not updated on replay' } },
      ...attributes], status: {} };
}

async function capture(context, rawPayload, settingsDir = privateDir, writeStatus = appendStatus) {
  const payload = JSON.parse(rawPayload);
  const sessionId = payload['thread-id'];
  if (typeof sessionId !== 'string' || !/^[A-Za-z0-9_-]+$/.test(sessionId)) {
    throw new Error('invalid_thread_id');
  }
  const status = { run_id: context.run_id, role: context.role, launch_id: context.launch_id, session_id: sessionId };
  const exactRollout = context.rollout_path;
  if (exactRollout && (!path.basename(exactRollout).startsWith('rollout-') ||
      !path.basename(exactRollout).endsWith(`-${sessionId}.jsonl`))) throw new Error('invalid_exact_rollout');
  const rollout = exactRollout || findRollout(context.sessions_root, sessionId);
  if (!rollout) { writeStatus({ ...status, status: 'rollout_missing', posted_turns: 0 }); return; }
  const parsed = parseRollout(fs.readFileSync(rollout, 'utf8'));
  if (!parsed.meta || parsed.meta.sessionId !== sessionId || !parsed.meta.cwd ||
      !inside(fs.realpathSync(parsed.meta.cwd), projectDir)) {
    writeStatus({ ...status, status: 'out_of_scope', posted_turns: 0 }); return;
  }
  let observed;
  try {
    // Exact already-validated file only; collect_run.py owns response-id dedup.
    const ledgerPath = path.join(settingsDir, 'session-ledgers', sessionId + '.json');
    observed = JSON.parse(execFileSync('python3.13', [path.join(__dirname, 'session_ledger.py'),
      '--rollout', rollout, '--session-id', sessionId, '--output', ledgerPath],
      { encoding: 'utf8', timeout: 10000, maxBuffer: 1024 * 1024, stdio: ['ignore', 'pipe', 'pipe'] }));
  } catch {
    observed = { attributes: { 'herdr.observed.ledger_status': 'failed' }, metadata: { status: 'failed', cost: null } };
  }
  // Match `langwatch ingest codex --notify`: the latest three turns are idempotently merged.
  const turns = parsed.turns.slice(-3);
  if (!turns.length) { writeStatus({ ...status, status: 'no_turns', posted_turns: 0 }); return; }
  const body = turnsToOtlp(turns, Date.now());
  const attributes = { ...context.resource_attributes, ...observed.attributes, 'session.id': sessionId,
    'langwatch.metadata': JSON.stringify({ run_id: context.run_id, role: context.role,
      launch_id: context.launch_id, session_id: sessionId, observed: observed.metadata }) };
  const extra = Object.entries(attributes).filter(([key]) => key !== 'service.name')
    .map(([key, value]) => ({ key, value: typeof value === 'boolean' ? { boolValue: value } :
      Number.isInteger(value) ? { intValue: String(value) } : { stringValue: String(value) } }));
  for (const resource of body.resourceSpans) {
    resource.resource.attributes.push(...extra);
    for (const scope of resource.scopeSpans) for (const span of scope.spans) span.attributes.push(...extra);
  }
  // Older LangWatch accepts replay but retains an existing span's original attrs.
  // Add one explicit non-LLM observation to the latest trace instead of changing
  // native usage, inventing an LLM call, or attaching session totals to every trace.
  const snapshot = snapshotSpan(sessionId, observed, turns[turns.length - 1].traceId, extra);
  if (snapshot) body.resourceSpans[0].scopeSpans.push({
    scope: { name: 'herdr.observed.session_ledger', version: '1' }, spans: [snapshot],
  });
  const endpoint = fs.readFileSync(path.join(settingsDir, 'endpoint'), 'utf8').trim().replace(/\/$/, '');
  const address = new URL(endpoint);
  if (address.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(address.hostname) ||
      !['', '/'].includes(address.pathname)) throw new Error('nonlocal_endpoint');
  const keyPath = path.join(settingsDir, 'ingest-key');
  if (fs.statSync(keyPath).mode & 0o077) throw new Error('ingest_key_permissions');
  const key = fs.readFileSync(keyPath, 'utf8').trim();
  const response = await fetch(endpoint + '/api/otel/v1/traces', {
    method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${key}` },
    body: JSON.stringify(body), signal: AbortSignal.timeout(5000),
  });
  writeStatus({ ...status, status: response.ok ? 'posted' : 'ingest_rejected',
    http_status: response.status, posted_turns: response.ok ? turns.length : 0,
    trace_ids: turns.map(turn => turn.traceId), observed_ledger_sha256: observed.metadata.ledger_sha256 || null,
    observed_snapshot_span_id: snapshot ? snapshot.spanId : null });
}

async function main() {
  const [, , contextArg, rawPayload] = process.argv;
  const contextPath = fs.realpathSync(contextArg);
  if (!inside(contextPath, path.join(privateDir, 'contexts'))) throw new Error('invalid_context');
  const context = JSON.parse(fs.readFileSync(contextPath, 'utf8'));
  // Existing business notifications should not wait for telemetry network timeouts.
  chainOriginal(context.original_notify, rawPayload);
  try {
    await capture(context, rawPayload);
  } catch (error) {
    // Never log payload, response body, key, original command, or arbitrary exception text.
    appendStatus({ run_id: context.run_id, role: context.role, launch_id: context.launch_id,
      status: 'capture_failed', error_type: error.name || 'Error', posted_turns: 0 });
  }
}

if (require.main === module) main().catch(() => { process.exitCode = 0; });
module.exports = { capture, findRollout, inside, snapshotSpan };
