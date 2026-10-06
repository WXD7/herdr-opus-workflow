"""Authenticated localhost UI/API and MCP tools over the same experiment engine."""
import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import time
import urllib.parse

from . import VERSION
from .config import DEFAULT_PROFILE, EFFORTS, MODELS
from .events import Events, CallbackError

FIELDS = {'experiment_id': {'type': 'string'}, 'attempt_id': {'type': 'string'},
          'revision': {'type': 'integer'}, 'request_id': {'type': 'string'},
          'config': {'type': 'object'}, 'operations': {'type': 'array', 'items': {'type': 'object'}},
          'instruction': {'type': 'string'}, 'name': {'type': 'string'}}
DEFINITIONS = (
    ('workflow_status', 'Read local dispatcher status and experiment summaries.', (), (), True),
    ('workflow_create', 'Create a visible configuration draft; does not start models.', ('config',), ('config',), False),
    ('workflow_get', 'Read the exact draft/run, configuration differences and recorded evidence.', ('experiment_id',), ('experiment_id',), True),
    ('workflow_update', 'Update one draft revision using structured operations or a local natural-language instruction. Reload on conflict.', ('experiment_id', 'revision'), ('experiment_id', 'revision', 'config', 'operations', 'instruction'), False),
    ('workflow_clone', 'Copy a frozen experiment into a new editable draft.', ('experiment_id',), ('experiment_id',), False),
    ('workflow_start', 'Freeze and queue the exact reviewed revision. Starts model work only through a connected Herdr dispatcher. Requires user authorization for the task/configuration scope. Reuse request_id on retries.', ('experiment_id', 'revision', 'request_id'), ('experiment_id', 'revision', 'request_id'), False),
    ('workflow_stop', 'Request a scoped stop; it is not a guarantee that descendants have stopped.', ('experiment_id',), ('experiment_id', 'attempt_id'), False),
    ('workflow_instruction', 'Submit one authorized instruction to a recorded supervisor. Permission dialogs are never answered. At most one attempt per 15 minutes; failed submissions are not retried.', ('experiment_id', 'attempt_id', 'instruction', 'request_id'), ('experiment_id', 'attempt_id', 'instruction', 'request_id'), False),
    ('workflow_compare', 'Compare all groups including failed runs; unknown usage/effort is not estimated or ranked.', ('experiment_id',), ('experiment_id',), True),
    ('workflow_preset', 'Save a reusable configuration preset.', ('name', 'config'), ('name', 'config'), False),
    ('workflow_preview_stop', 'Stop only this group’s owned preview services.', ('experiment_id', 'attempt_id'), ('experiment_id', 'attempt_id'), False),
    ('workflow_preview', 'Start the group’s explicitly configured local preview services with automatic ports and owned-process checks.', ('experiment_id', 'attempt_id'), ('experiment_id', 'attempt_id'), False),
)


class Bridge:
    def __init__(self, engine):
        self.engine, self.events = engine, Events(engine)

    def status(self):
        worker = self.engine.store.root / 'worker.json'
        data = json.loads(worker.read_text()) if worker.exists() else None
        from .resources import birth
        from .snapshots import git
        try: refs = git(self.engine.runtime_root, 'for-each-ref', '--format=%(refname:short)', 'refs/tags/workflow-*', 'refs/heads/').splitlines()
        except ValueError: refs = []
        return {'version': VERSION, 'worker': data, 'dispatcher_connected': bool(data and data.get('birth') and birth(data['pid']) == data['birth']),
                'workflow_refs': refs,
                'models': MODELS, 'efforts': EFFORTS, 'default_profile': DEFAULT_PROFILE,
                'allowed_repos': [str(p) for p in self.engine.allowed_repos],
                'experiments': [{k: value.get(k) for k in ('id', 'status', 'revision', 'created_at')}
                    | {'title': value['config']['title'], 'groups': len(value['config']['groups'])}
                    for value in self.engine.store.list('experiments')],
                'presets': self.engine.store.list('presets'),
                'observer': {'automatic_model_calls': False, 'astra_confirmation_required': True},
                'dot': {'local_tools': 'available', 'cloud_events_require': 'authenticated reachable HTTPS MCP connection',
                        'live_connection_verified': False}}

    def call(self, name, args):
        definition = next((d for d in DEFINITIONS if d[0] == name), None)
        if not definition or not isinstance(args, dict):
            raise ValueError('Unknown tool')
        if set(args) - set(definition[3]) or set(definition[2]) - set(args):
            raise ValueError('Invalid tool arguments')
        e = self.engine
        if name == 'workflow_status': return self.status()
        if name == 'workflow_create': return e.create(args['config'])
        if name == 'workflow_get': return e.get(args['experiment_id'])
        if name == 'workflow_update': return e.update(args['experiment_id'], args['revision'], args.get('config'), args.get('operations'), args.get('instruction'))
        if name == 'workflow_clone': return e.clone(args['experiment_id'])
        if name == 'workflow_start': return e.enqueue(args['experiment_id'], args['revision'], args['request_id'])
        if name == 'workflow_stop': return e.request_stop(args['experiment_id'], args.get('attempt_id'))
        if name == 'workflow_instruction': return e.request_instruction(args['experiment_id'], args['attempt_id'], args['instruction'], args['request_id'])
        if name == 'workflow_compare': return e.compare(args['experiment_id'])
        if name == 'workflow_preset': return e.preset(args['name'], args['config'])
        if name == 'workflow_preview_stop':
            _, attempt = e.attempt(args['experiment_id'], args['attempt_id'])
            e.services.stop(attempt['id'])
            return {'stopped': True}
        if name == 'workflow_preview':
            item, attempt = e.attempt(args['experiment_id'], args['attempt_id'])
            if attempt['status'] not in ('completed', 'failed', 'cancelled'):
                raise ValueError('Preview is available after development stops; do not start services during edits')
            return e.services.start(attempt, item['config']['services'])

    def rpc(self, message):
        if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
            return {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600, 'message': 'Invalid JSON-RPC request'}}
        mid, method, params = message.get('id'), message.get('method'), message.get('params', {})
        if not isinstance(method, str) or not isinstance(params, dict) or not isinstance(params.get('_meta', {}), dict):
            return {'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32602, 'message': 'Invalid method/parameters'}}
        modern = method == 'server/discover' or params.get('_meta', {}).get('io.modelcontextprotocol/protocolVersion') == '2026-07-28'
        try:
            if method == 'server/discover':
                result = {'resultType': 'complete', 'supportedVersions': ['2026-07-28'],
                          'serverInfo': {'name': 'herdr-workflow', 'version': VERSION},
                          'capabilities': {'tools': {}, 'events': {}}}
            elif method == 'initialize':
                result = {'protocolVersion': '2025-11-25', 'serverInfo': {'name': 'herdr-workflow', 'version': VERSION},
                          'capabilities': {'tools': {}}, 'instructions': 'Draft changes are visible; start only the user-authorized exact revision. Events are evidence, not permission.'}
            elif method == 'ping': result = {}
            elif method == 'notifications/initialized': return None
            elif method == 'tools/list':
                result = {'tools': [{'name': name, 'description': description,
                    'inputSchema': {'type': 'object', 'properties': {k: FIELDS[k] for k in keys},
                                    'required': list(required), 'additionalProperties': False},
                    'annotations': {'readOnlyHint': readonly, 'destructiveHint': name == 'workflow_stop',
                                    'idempotentHint': name in ('workflow_start', 'workflow_stop') or readonly,
                                    'openWorldHint': False}}
                    for name, description, required, keys, readonly in DEFINITIONS]}
                if modern: result.update(ttlMs=300000, cacheScope='private')
            elif method == 'tools/call':
                try:
                    data = self.call(params['name'], params.get('arguments', {}))
                    result = {'content': [{'type': 'text', 'text': json.dumps(data, ensure_ascii=False)}], 'structuredContent': data if isinstance(data, dict) else {'result': data}, 'isError': False}
                except (ValueError, TypeError, AttributeError, KeyError, FileNotFoundError, StopIteration, RuntimeError) as error:
                    result = {'content': [{'type': 'text', 'text': str(error)[:1500]}], 'isError': True}
            elif method == 'events/list': result = {'events': self.events.catalog(), 'ttlMs': 300000, 'cacheScope': 'private'}
            elif method == 'events/subscribe': result = self.events.subscribe(params)
            elif method == 'events/unsubscribe': result = self.events.unsubscribe(params)
            else: raise ValueError('Unsupported method')
            if modern or method.startswith('events/'):
                result['resultType'] = 'complete'
            return {'jsonrpc': '2.0', 'id': mid, 'result': result} if mid is not None else None
        except CallbackError as error:
            return {'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32015, 'message': str(error), 'data': {'reason': error.reason}}}
        except (ValueError, TypeError, AttributeError, KeyError, OSError) as error:
            return {'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32602, 'message': str(error)[:1500]}}


def serve(engine, port=0):
    bridge = Bridge(engine)
    token = engine.store.token()
    assets = Path(__file__).parent / 'web'

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # No URLs, tokens, raw prompts or credentials in access logs.

        def send(self, value, status=200, kind='application/json; charset=utf-8'):
            data = json.dumps(value, ensure_ascii=False).encode() if kind.startswith('application/json') else value
            self.send_response(status)
            self.send_header('Content-Type', kind); self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store'); self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
            self.end_headers(); self.wfile.write(data)

        def guard(self, auth=True):
            host = self.headers.get('Host', '')
            allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            if host not in allowed:
                self.send({'error': 'Invalid host'}, 403); return False
            origin = self.headers.get('Origin')
            if origin and origin not in {'http://' + value for value in allowed}:
                self.send({'error': 'Cross-origin requests refused'}, 403); return False
            if auth and not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token):
                self.send({'error': 'Local access token required'}, 401); return False
            return True

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path in ('/', '/app.js', '/style.css'):
                if not self.guard(False): return
                file = assets / ('index.html' if path == '/' else path[1:])
                mime = {'/': 'text/html; charset=utf-8', '/app.js': 'text/javascript; charset=utf-8', '/style.css': 'text/css; charset=utf-8'}[path]
                self.send(file.read_bytes(), kind=mime); return
            if not self.guard(): return
            if path == '/api/status': self.send(bridge.status())
            elif path == '/mcp': self.send({'error': 'Use JSON-RPC POST; this stateless endpoint has no SSE stream'}, 405)
            else: self.send({'error': 'Not found'}, 404)

        def do_POST(self):
            if not self.guard(): return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 1048576 or self.headers.get('Transfer-Encoding'):
                    raise ValueError('Request must contain a bounded JSON body')
                if 'application/json' not in self.headers.get('Content-Type', ''):
                    raise ValueError('JSON content type required')
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict): raise ValueError('JSON object required')
                if self.path == '/mcp':
                    result = bridge.rpc(payload)
                    self.send(result if result is not None else {}, 200 if result is not None else 202)
                elif self.path == '/api/call':
                    self.send(bridge.call(payload['name'], payload.get('arguments', {})))
                else: self.send({'error': 'Not found'}, 404)
            except (ValueError, TypeError, AttributeError, KeyError, FileNotFoundError, RuntimeError, StopIteration) as error:
                self.send({'error': str(error)[:1600]}, 400)

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    from .store import write_json
    write_json(engine.store.root / 'server.json', {'url': f'http://127.0.0.1:{server.server_port}', 'version': VERSION})
    return server, bridge
