import argparse
import json
import os
from pathlib import Path
import shlex
import sys
import threading
import time

from .bridge import Bridge, serve
from .engine import Engine
from .snapshots import ROOT, context_environment, load_context, session_command


def main(argv=None):
    parser = argparse.ArgumentParser(description='Existing workflow entry: configuration, isolated runs, evidence and Dot tools')
    parser.add_argument('--state-dir', default=str(ROOT / 'runtime/experiments'))
    parser.add_argument('--allow-repo', action='append', default=[])
    commands = parser.add_subparsers(dest='command', required=True)
    serve_parser = commands.add_parser('serve', help='Open the shared local configuration API/UI (no model calls)')
    serve_parser.add_argument('--port', type=int, default=0)
    worker = commands.add_parser('worker', help='Attach a deterministic dispatcher from a genuine Herdr pane')
    worker.add_argument('--capacity', type=int, default=4); worker.add_argument('--session'); worker.add_argument('--once', action='store_true')
    launch = commands.add_parser('prepare-launch', help='Prepare a new native Herdr session entry; does not launch Herdr or models')
    launch.add_argument('--capacity', type=int, default=2)
    commands.add_parser('status')
    commands.add_parser('mcp', help='Local stdio tools for a connected Codex/Dot task')
    call = commands.add_parser('call'); call.add_argument('tool'); call.add_argument('arguments', nargs='?', default='{}')
    session = commands.add_parser('session'); session.add_argument('--check', action='store_true')
    session.add_argument('agent_args', nargs=argparse.REMAINDER)
    commands.add_parser('profile-env')
    decision = commands.add_parser('decision', help='Current registered session: exchange scoped decisions with its parent')
    decision.add_argument('action', choices=['request', 'inbox', 'answer', 'consume'])
    decision.add_argument('--key'); decision.add_argument('--id'); decision.add_argument('--request-hash')
    decision.add_argument('--kind', choices=['routine', 'authority'], default='routine')
    decision.add_argument('--text')
    commands.add_parser('service-start', help='Start only the current frozen group’s configured services')
    commands.add_parser('service-stop', help='Stop only the current frozen group’s owned services')
    connection = commands.add_parser('connection', help='Generate a local MCP plugin with explicit roots; never prints the token')
    connection.add_argument('--write-plugin', action='store_true')
    args = parser.parse_args(argv)
    if args.command == 'decision':
        from .decisions import command
        print(json.dumps(command(args), ensure_ascii=False, indent=2))
        return
    if args.command in ('service-start', 'service-stop'):
        context = load_context()
        local = Engine(context['state_root'])
        item, attempt = local.attempt(context['experiment_id'], context['attempt_id'])
        if attempt['context_hash'] != os.environ['HERDR_EXPERIMENT_CONTEXT_HASH']:
            raise ValueError('Group identity mismatch')
        if args.command == 'service-stop': local.services.stop(attempt['id'])
        else: print(json.dumps(local.services.start(attempt, item['config']['services']), ensure_ascii=False))
        return
    if args.command in ('session', 'profile-env'):
        context = load_context()
        path = os.environ['HERDR_EXPERIMENT_CONTEXT']
        if args.command == 'profile-env':
            for key, value in context_environment(context, path).items():
                print('export ' + key + '=' + shlex.quote(value))
            return
        command, env = session_command(context, path)
        supplied = args.agent_args
        if supplied and supplied[0] == '--': supplied = supplied[1:]
        # The dispatcher freezes all flags. A positional initial task is the only override.
        if len(supplied) > 1 or supplied and supplied[0].startswith('-'):
            raise ValueError('Frozen sessions accept only one initial prompt, no model/config/resume overrides')
        if args.check:
            print(json.dumps({'model_invoked': False, 'profile': context['profile'], 'workflow': context['workflow'],
                              'command': command, 'title': context['title_line']}, ensure_ascii=False)); return
        if os.environ.get('HERDR_ENV') != '1' or not os.environ.get('HERDR_PANE_ID'):
            raise ValueError('A genuine Herdr pane is required for model execution')
        os.execvpe(command[0], command + supplied, dict(os.environ) | env)
    engine = Engine(args.state_dir, args.allow_repo)
    bridge = Bridge(engine)
    if args.command == 'prepare-launch':
        from .launch import prepare
        print(json.dumps(prepare(engine, args.capacity), ensure_ascii=False, indent=2))
    elif args.command == 'status':
        print(json.dumps(bridge.status(), ensure_ascii=False, indent=2))
    elif args.command == 'call':
        print(json.dumps(bridge.call(args.tool, json.loads(args.arguments)), ensure_ascii=False, indent=2))
    elif args.command == 'connection':
        if args.write_plugin:
            from .store import write_json
            folder = engine.store.root / 'dot-plugin'
            write_json(folder / 'plugin.json', {'$schema': 'https://agent-plugins.org/schemas/1.0.0/plugin.schema.json',
                'name': 'herdr-experiments', 'version': '1.3.0',
                'description': 'Configure and compare isolated Herdr experiments using a shared local evidence store.'})
            command = [str(ROOT / 'scripts/experiment.py'), '--state-dir', str(engine.store.root)]
            for repo in engine.allowed_repos: command.extend(['--allow-repo', str(repo)])
            command.append('mcp')
            write_json(folder / 'mcp.json', {'mcpServers': {'herdr-experiments':
                {'type': 'stdio', 'command': sys.executable, 'args': command}}})
        print(json.dumps({'state_dir': str(engine.store.root), 'server_metadata': str(engine.store.root / 'server.json'),
                          'local_token_file': str(engine.store.root / 'access-token'),
                          'cloud_dot_connected': False, 'local_plugin_path': str(engine.store.root / 'dot-plugin'),
                          'note': 'Local tools work through a connected computer. Cloud MCP Events require a separately connected authenticated HTTPS endpoint.'}, ensure_ascii=False, indent=2))
    elif args.command == 'mcp':
        # stdio is authorized by the local host process, not by exposing an unauthenticated socket.
        for line in sys.stdin:
            try:
                if len(line) > 1048576: raise ValueError('Request too large')
                result = bridge.rpc(json.loads(line))
            except ValueError:
                result = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Invalid JSON'}}
            if result is not None:
                print(json.dumps(result, ensure_ascii=False), flush=True)
    elif args.command == 'worker':
        from .herdr import Herdr
        if not 1 <= args.capacity <= 32: raise ValueError('capacity must be 1–32')
        adapter = Herdr(args.session)
        # A lock for the whole lifetime prevents two startup panes alternating ticks
        # against different sessions. The inner worker lock still protects each tick.
        with engine.store.lock('dispatcher', blocking=False):
            engine.services.recover()
            print(json.dumps({'dispatcher': 'connected', 'session': adapter.session, 'caller_pane': adapter.caller,
                              'model_heartbeat': False}), flush=True)
            while True:
                engine.tick(adapter, args.capacity)
                if args.once: break
                time.sleep(5)  # Local deterministic status checks, never an LLM heartbeat.
    elif args.command == 'serve':
        with engine.store.lock('dashboard', blocking=False):
            server, bridge = serve(engine, args.port)
            stopped = threading.Event()
            def events():
                while not stopped.wait(5):
                    try: bridge.events.deliver()
                    except (OSError, ValueError): pass
            threading.Thread(target=events, daemon=True).start()
            url = f'http://127.0.0.1:{server.server_port}'
            print(json.dumps({'url': url, 'token_file': str(engine.store.root / 'access-token'),
                              'model_invoked': False, 'dispatcher_connected': bridge.status()['dispatcher_connected']}), flush=True)
            try: server.serve_forever(poll_interval=.25)
            finally: stopped.set(); server.server_close()


if __name__ == '__main__':
    try: main()
    except (ValueError, OSError, RuntimeError, KeyError) as error:
        print('Workflow: ' + str(error)[:2000], file=sys.stderr); sys.exit(2)
